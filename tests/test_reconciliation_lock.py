"""Exclusion between reconciliation runs: DESIGN section 11, fourth instance.

The race these close was reproduced before being fixed: two threads held inside
`run_reconciliation` past `_reset` and before the writes, one committing and the
other taking

  IntegrityError: duplicate key value violates unique constraint
  "uq_active_match_per_declared"

`_reset` deletes PROPOSED rows, which takes row locks -- but an *empty* DELETE
takes none, so two runs over an estate with no standing proposals both compute
the same pairings and both insert.

`transaction=True` is load-bearing wherever it appears below. The default
django_db wraps a test in a transaction that never commits, so rows written by
the main connection are invisible to the other connections these tests open.
"""

import threading

import pytest
from django.db import connection, connections, transaction

from datum import locks
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus, MatchState
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind
from datum.locks import run_lock
from datum.reconcile import service
from datum.reconcile.models import Match
from datum.reconcile.service import RECONCILE_OPERATION, run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


def _seed(tenant_id: str = TENANT) -> None:
    """One declared and one discovered resource that match, for one tenant.

    `get_or_create` on the kind because a `transaction=True` test truncates
    tables without restoring what the migrations seeded.
    """
    kind, _ = Kind.objects.get_or_create(
        name="Deployment", defaults={"attribute_schema": {"replicas": "int"}}
    )
    revision = IntentRevision.objects.create(
        tenant_id=tenant_id, commit_sha=tenant_id.replace("-", "").ljust(40, "0")[:40]
    )
    IntentRevision.objects.filter(pk=revision.pk).update(is_active=True)
    DeclaredResource.objects.create(
        tenant_id=tenant_id,
        kind=kind,
        name="web",
        scope="default",
        attributes={"replicas": 3},
        revision=revision,
    )
    run = CollectorRun.objects.create(
        tenant_id=tenant_id, collector_name="kubernetes", status=CollectorRunStatus.SUCCESS
    )
    DiscoveredResource.objects.create(
        tenant_id=tenant_id,
        kind=kind,
        name="web",
        scope="default",
        provider_id=f"uid-{tenant_id[-4:]}",
        attributes={"replicas": 5},
        last_seen_run=run,
    )


def _advisory_locks_on_this_session() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        )
        return int(cursor.fetchone()[0])


@pytest.fixture
def a_run_holding_the_lock():
    """A reconciliation lock held on another connection for the test's duration.

    Another connection rather than this one: advisory locks are held per session,
    so a same-session check would re-enter a lock it already owns and prove
    nothing.
    """
    released = threading.Event()
    holding = threading.Event()

    def hold():
        try:
            with run_lock(TENANT, RECONCILE_OPERATION) as acquired:
                assert acquired, "the fixture must actually hold the lock"
                holding.set()
                released.wait(timeout=30)
        finally:
            connections.close_all()

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait(timeout=10)

    yield

    released.set()
    holder.join(timeout=10)


# ---------------------------------------------------------------------------
# Exclusion
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_second_run_is_skipped_while_one_holds_the_lock(a_run_holding_the_lock):
    _seed()

    assert run_reconciliation(TENANT) is False


@pytest.mark.django_db(transaction=True)
def test_a_skipped_run_writes_nothing(a_run_holding_the_lock):
    """Not "writes no matches": a skip must leave no partial state anywhere."""
    _seed()

    run_reconciliation(TENANT)

    assert not Match.objects.filter(tenant_id=TENANT).exists()
    from datum.reconcile.models import Discrepancy

    assert not Discrepancy.objects.filter(tenant_id=TENANT).exists()


@pytest.mark.django_db(transaction=True)
def test_a_completed_run_reports_that_it_ran(a_run_holding_the_lock):
    """The other side of the boolean, so False is a signal rather than the only
    value ever returned. A caller must be able to tell a skip from a run."""
    _seed(OTHER_TENANT)

    assert run_reconciliation(OTHER_TENANT) is True


@pytest.mark.django_db(transaction=True)
def test_a_different_tenant_is_not_blocked(a_run_holding_the_lock):
    """The lock is per tenant. One tenant's long run must not stop every other
    tenant's reconciliation, which a global lock would."""
    _seed(OTHER_TENANT)

    assert run_reconciliation(OTHER_TENANT) is True
    assert Match.objects.filter(tenant_id=OTHER_TENANT).count() == 1


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_free_after_a_run_finishes(second_connection):
    _seed()

    run_reconciliation(TENANT)

    assert second_connection(run_lock, TENANT, RECONCILE_OPERATION) is True


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_free_after_a_run_raises(second_connection):
    """A crashed run must not wedge the tenant until someone restarts a worker."""
    _seed()

    def explode(tenant_id: str) -> None:
        raise RuntimeError("reconciliation failed")

    original = service._reconcile_once
    service._reconcile_once = explode
    try:
        with pytest.raises(RuntimeError):
            run_reconciliation(TENANT)
    finally:
        service._reconcile_once = original

    assert second_connection(run_lock, TENANT, RECONCILE_OPERATION) is True


@pytest.mark.django_db(transaction=True)
def test_the_lock_lasts_exactly_as_long_as_the_transaction(second_connection):
    """The test this file exists for, restated for a transaction-scoped lock.

    The lock and the commit must be inseparable. Two ways to get that wrong,
    and this pins both ends:

        too short:  BEGIN -> acquire -> work -> release -> COMMIT
        too long:   acquire -> BEGIN -> work -> COMMIT -> ... -> release

    Too short leaves a window in which the run's writes are uncommitted and the
    lock is free, so a second worker computes from the state this run is about
    to invalidate. Too long is what a *session*-scoped lock gives -- harmless
    here, but it is the shape whose release has to be placed by hand, and
    placing it by hand is what #36 showed can be got wrong from the caller's
    side.

    The first assertion excludes "too short": the probe runs inside
    `_reconcile_once`, after every write and before the commit, on a second
    connection, so a lock released early is a lock this probe is granted.

    The second excludes "too long", and is the assertion that inverted when the
    lock became transaction-scoped. `on_commit` fires immediately after the
    commit; a session-scoped lock is still held there (count 1) because only an
    explicit unlock can release it, while a transaction-scoped lock is already
    gone with the transaction that carried it (count 0). Reverting `locked_run`
    to compose `run_lock` turns this back to 1.
    """
    _seed()
    observed: dict[str, object] = {}
    original = service._reconcile_once

    def probe_before_the_commit(tenant_id: str) -> None:
        original(tenant_id)
        observed["granted_mid_run"] = second_connection(run_lock, TENANT, RECONCILE_OPERATION)
        transaction.on_commit(
            lambda: observed.__setitem__("held_after_commit", _advisory_locks_on_this_session())
        )

    service._reconcile_once = probe_before_the_commit
    try:
        assert run_reconciliation(TENANT) is True
    finally:
        service._reconcile_once = original

    assert observed["granted_mid_run"] is False, (
        "a second session was granted the lock while this run's writes were "
        "still uncommitted -- the lock is being released before its commit"
    )
    assert observed["held_after_commit"] == 0, (
        "the lock outlived the transaction that took it, which means it is "
        "session-scoped and its release is placed by hand again"
    )


# ---------------------------------------------------------------------------
# Lifetime under a caller's transaction
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_callers_transaction_does_not_free_the_lock_early(second_connection):
    """The lock outlives the commit that publishes the run, whoever owns it.

    `locked_run` composes the lock and the transaction in the right order, so a
    *callee* cannot invert them. A **caller** that opens its own transaction
    around `run_reconciliation` inverts them from the other side: Django turns
    the inner `atomic()` into a savepoint, so `locked_run` exits by releasing a
    savepoint rather than committing, and a session-scoped unlock then takes
    effect immediately -- while the real commit is still pending.

        what the caller writes:  BEGIN -> [ acquire -> work -> release ] -> COMMIT

    The bug this excludes: the lock is free during the window between
    `run_reconciliation` returning and the caller's COMMIT. A second worker
    entering that window reads the estate without this run's uncommitted
    writes, computes the same pairings, and collides on
    `uq_active_match_per_declared` -- the race `test_two_concurrent_runs_no_longer_collide`
    closes, arriving by a path that test cannot reach.

    The fixture discriminates because the probe happens *inside* the caller's
    atomic block, before any commit. Under the bug the second connection is
    granted the lock there; under a correct implementation it is refused until
    the outer transaction ends.
    """
    _seed()
    observed: dict[str, bool] = {}

    with transaction.atomic():
        assert run_reconciliation(TENANT) is True
        observed["granted_before_commit"] = second_connection(run_lock, TENANT, RECONCILE_OPERATION)

    assert observed["granted_before_commit"] is False, (
        "the lock was free while the caller's transaction was still open, so "
        "the run's writes were unpublished and unprotected at the same time"
    )


@pytest.mark.django_db(transaction=True)
def test_a_run_that_raises_inside_a_callers_transaction_strands_nothing(second_connection):
    """The edge a transaction-scoped lock does not close by itself.

    `ROLLBACK TO SAVEPOINT` releases a lock acquired inside that savepoint, so
    when the body raises under a caller's own transaction the lock is freed
    while the outer transaction is still open. That is safe only because
    `locked_run` keeps the lock and the body's writes in one block: the
    rollback undoes both together.

    The bug this excludes is therefore not "the lock was released" -- it was,
    correctly -- but "the lock was released while this run's writes survived."
    A second worker acquiring the lock here must find no trace of the failed
    run to build on.

    The fixture discriminates on the *pair*: it asserts the lock is grantable
    and that nothing was written. An implementation that let the body's writes
    escape the rolled-back savepoint would satisfy the first and fail the
    second.
    """
    _seed()

    def explode(tenant_id: str) -> None:
        Match.objects.create(
            tenant_id=tenant_id,
            declared_kind="Deployment",
            declared_scope="default",
            declared_name="web",
            discovered_provider_id="uid-0001",
            state=MatchState.PROPOSED,
        )
        raise RuntimeError("reconciliation failed after writing")

    original = service._reconcile_once
    service._reconcile_once = explode
    try:
        with transaction.atomic():
            with pytest.raises(RuntimeError):
                run_reconciliation(TENANT)
            granted = second_connection(run_lock, TENANT, RECONCILE_OPERATION)
    finally:
        service._reconcile_once = original

    assert granted is True, "a failed run must not wedge the tenant"
    assert not Match.objects.filter(tenant_id=TENANT).exists(), (
        "the lock was freed while the failed run's writes survived, so a second "
        "worker would build on state the first never committed"
    )


@pytest.mark.django_db(transaction=True)
def test_the_transaction_scoped_lock_refuses_to_be_taken_outside_one():
    """The contract `_try_acquire_until_commit` states in prose, pinned.

    A transaction-scoped lock acquired with no transaction open is released
    inside the implicit single-statement transaction: it reports success and
    excludes nothing. The assertion is what turns that silent no-op into a
    failure, and nothing else in this file reaches it -- `locked_run` opens the
    transaction first, so the branch is unreachable through the public helper.
    """
    assert not connection.in_atomic_block, "this test requires autocommit"

    with pytest.raises(AssertionError, match="transaction"):
        locks._try_acquire_until_commit(locks._advisory_key(TENANT, RECONCILE_OPERATION))


@pytest.mark.django_db(transaction=True)
def test_a_callers_transaction_frees_the_lock_once_it_commits(second_connection):
    """The other side of the test above, so holding forever also fails.

    A lock that is never released would satisfy the previous assertion and
    wedge the tenant. This one fails in that case.
    """
    _seed()

    with transaction.atomic():
        assert run_reconciliation(TENANT) is True

    assert second_connection(run_lock, TENANT, RECONCILE_OPERATION) is True


# ---------------------------------------------------------------------------
# The race itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_runs_no_longer_collide():
    """The reproduction that demonstrated the defect, kept as a regression test.

    Before the lock this produced one commit and one IntegrityError on
    uq_active_match_per_declared. The defect is only closed if the thing that
    showed it now passes.
    """
    _seed()
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            ran = run_reconciliation(TENANT)
            with lock:
                outcomes.append("ran" if ran else "skipped")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcomes.append(f"{type(exc).__name__}")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["ran", "skipped"]
    assert Match.objects.filter(tenant_id=TENANT).count() == 1


@pytest.mark.django_db(transaction=True)
def test_ten_concurrent_attempts_admit_exactly_one():
    """Scale the two-thread case up, to catch an exclusion that holds for one
    contender and leaks under real contention."""
    _seed()
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(10, timeout=30)

    def attempt() -> None:
        try:
            start.wait()
            ran = run_reconciliation(TENANT)
            with lock:
                outcomes.append("ran" if ran else "skipped")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcomes.append(f"{type(exc).__name__}")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("ran") == 1, outcomes
    assert outcomes.count("skipped") == 9, outcomes
    assert Match.objects.filter(tenant_id=TENANT).count() == 1
