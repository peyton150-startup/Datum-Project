"""SPECIFICATION for WBS 1.4.4: CF-4 and CF-5, the intent-side races.

Written before the implementation, reviewed as a description of intended
behaviour. Nothing behind these passes yet.

Both defects are in already-merged phase 2 code and neither loses data -- a
Postgres constraint holds the invariant in both cases. What fails is the
*contract*: the conflict reaches the caller as `IntegrityError`, wearing the
database's abstraction rather than the module's, so no caller can be written to
expect it. That is the same inversion as CF-2, which is why DESIGN section 11
records it as a class rather than two more one-off entries.

## How these tests force the race

Racing two real threads through `ingest_revision` would reproduce the defect
only sometimes, and a test that fails one run in twenty is worse than no test.
Instead each race is made deterministic by driving both callers past the point
where they would have interleaved -- the check that each one performs before the
other has committed. The window is the defect; forcing it open is how the fix
gets proven rather than hoped for.

Under READ COMMITTED this is a fair substitute rather than a convenient one:
the only way this class of race can resolve is one transaction committing and
the other conflicting against the constraint, so forcing the losing caller's
check to return the stale value reproduces the database-visible symptom exactly.

**Known gap, named rather than assumed covered.** These prove the
unique-violation outcome is handled. They say nothing about whether two
genuinely concurrent transactions taking the update-then-insert path in CF-5
could deadlock (Postgres 40P01) instead of cleanly hitting the partial unique
index, which depends on lock acquisition order and cannot be exposed by a
single-threaded replay. Deferred deliberately; if it happens it is loud and
retryable, not silent corruption.

## The API these tests assume

- `datum.intent.errors.RevisionConflict` -- raised when a concurrent write
  claims the same revision identity. A domain exception, not `IntegrityError`.
- `ingest_revision` stays idempotent on `(tenant, commit_sha)` under
  concurrency, which is a stronger promise than the one it makes today.
"""

import pytest

from datum.intent.errors import RevisionConflict
from datum.intent.ingest import ingest_revision
from datum.intent.models import IntentRevision

TENANT = "00000000-0000-0000-0000-000000000001"


def _miss_once(monkeypatch):
    """Make the pre-insert existence check miss exactly once.

    That is the real shape of the race, and the distinction matters. The check
    returns nothing because the winner has not committed *yet*; by the time the
    loser's insert has conflicted, the winner is committed and a fresh look
    finds it. A patch that made the check permanently blind would also blind the
    recovery path, and would be testing a database that never commits rather
    than a race.
    """
    from datum.intent import ingest

    real = ingest._existing_revision
    calls = {"n": 0}

    def once(tenant_id, commit_sha):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(tenant_id, commit_sha)

    monkeypatch.setattr("datum.intent.ingest._existing_revision", once)


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# CF-4: check-then-insert on (tenant, commit_sha)
# ---------------------------------------------------------------------------


def test_the_same_commit_ingested_twice_concurrently_yields_one_revision(intent_repo, monkeypatch):
    """The poll and the webhook deliver the same commit at the same moment.

    Both look for an existing revision, both find none, both project. Today the
    loser gets IntegrityError from `uq_revision_tenant_commit`. The promise is
    idempotency, so the loser must return the winner's revision instead.
    """
    repo = intent_repo()
    ingest_revision(TENANT, repo)
    existing = IntentRevision.objects.get(tenant_id=TENANT)

    _miss_once(monkeypatch)

    revision = ingest_revision(TENANT, repo)

    assert revision.pk == existing.pk
    assert IntentRevision.objects.filter(tenant_id=TENANT).count() == 1


def test_the_losing_writer_does_not_leave_a_half_projected_revision(intent_repo, monkeypatch):
    """Losing the race must not leave declared rows behind belonging to a
    revision that was never created. Projection is already atomic; this asserts
    the conflict path does not open a hole in it."""
    from datum.graph.models import DeclaredResource

    repo = intent_repo()
    ingest_revision(TENANT, repo)
    rows_before = DeclaredResource.objects.filter(tenant_id=TENANT).count()

    _miss_once(monkeypatch)
    ingest_revision(TENANT, repo)

    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == rows_before


def test_a_conflict_never_surfaces_as_an_integrity_error(intent_repo, monkeypatch):
    """The contract, stated directly.

    Every caller of `ingest_revision` is written against `InvalidRevision` and
    `RepositoryUnavailable`. A third failure wearing psycopg's abstraction is
    one no caller can be written to expect, which is what makes this a defect
    rather than merely an ugly traceback.
    """
    from django.db.utils import IntegrityError

    repo = intent_repo()
    ingest_revision(TENANT, repo)
    _miss_once(monkeypatch)

    try:
        ingest_revision(TENANT, repo)
    except IntegrityError as exc:  # pragma: no cover - the defect being fixed
        pytest.fail(f"conflict surfaced as a database exception: {exc}")


# ---------------------------------------------------------------------------
# CF-5: two revisions both becoming active
# ---------------------------------------------------------------------------


def test_two_different_commits_cannot_both_become_active(intent_repo, monkeypatch):
    """Under READ COMMITTED, T2's scan cannot see the row T1 has not yet
    committed, so both may insert `is_active=True`. The partial unique index
    catches it -- as `IntegrityError`, across a module boundary.

    Exactly one revision is active afterwards, whichever wins.

    `RevisionConflict` specifically, not `InvalidRevision`. Accepting either
    would let an implementation synthesize a fake `DocumentError` and raise
    `InvalidRevision` for a race with no document to blame -- and because
    `poll_intent_repository` already catches `InvalidRevision`, that lazy branch
    would pass every test in this file while `tasks.py` never learned the new
    exception existed. The point of naming the exception is defeated by an
    assertion loose enough to not require it.
    """
    first = intent_repo()
    ingest_revision(TENANT, first)

    # Force the race: the deactivating UPDATE sees nothing to deactivate, as it
    # would if the other transaction had not yet committed its active row.
    monkeypatch.setattr("datum.intent.ingest._deactivate_current", lambda tenant_id: 0)

    with pytest.raises(RevisionConflict):
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))

    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1


def test_the_previously_active_revision_still_stands_after_a_conflict(intent_repo, monkeypatch):
    """A lost race must leave the estate's declared plane exactly as it was.

    This is the same promise the malformed-document path already makes: a
    revision that does not land leaves the previous one active.
    """
    first = intent_repo()
    winner = ingest_revision(TENANT, first)

    monkeypatch.setattr("datum.intent.ingest._deactivate_current", lambda tenant_id: 0)
    with pytest.raises(RevisionConflict):
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))

    winner.refresh_from_db()
    assert winner.is_active is True


def test_a_revision_conflict_is_survivable_by_the_poll_task(intent_repo, monkeypatch, settings):
    """The task promises it never raises. A new exception type that the task
    does not catch would break that promise the first time two triggers
    overlapped -- which is precisely when the webhook lands beside the poller.
    """
    from datum.intent.tasks import poll_intent_repository

    settings.INTENT_REPO_URL = "file:///irrelevant"
    settings.INTENT_WORKTREE_DIR = intent_repo()
    monkeypatch.setattr("datum.intent.tasks.sync_worktree", lambda *args: "sha")

    def conflict(*args, **kwargs):
        raise RevisionConflict("another writer activated a revision first")

    monkeypatch.setattr("datum.intent.tasks.ingest_revision", conflict)

    assert poll_intent_repository() is None


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


def test_ordinary_sequential_ingestion_is_unaffected(intent_repo):
    """The nearby case the fix must not disturb: two different commits arriving
    one after another, with no race at all, still replace one another."""
    ingest_revision(TENANT, intent_repo())
    second = ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))

    assert second.is_active is True
    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1


def test_re_ingesting_the_same_commit_is_still_a_no_op(intent_repo):
    """Plain idempotency, unchanged. The conflict handling must not turn the
    ordinary re-poll into an error path."""
    repo = intent_repo()
    first = ingest_revision(TENANT, repo)

    assert ingest_revision(TENANT, repo).pk == first.pk
