"""Ingest a Git revision into the declared plane: DESIGN section 10.

The boundary between a working tree and the domain. Reads files, hands their
text to the validator, and projects the result. Nothing here decides whether a
document is valid -- that is `documents` -- and nothing here talks to a
provider.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from django.db import IntegrityError, OperationalError, transaction

from datum.graph.models import DeclaredResource
from datum.intent.documents import DocumentSource, KindSchemas, parse_document_set
from datum.intent.errors import RevisionConflict
from datum.intent.models import IntentRevision
from datum.intent.repository import head_sha
from datum.kinds.models import Kind
from datum.locks import CONCURRENCY_ROLLBACK_CODES, sqlstate_of
from datum.reconcile.domain import ResourceSnapshot

DOCUMENT_SUFFIXES = frozenset({".yaml", ".yml"})
GIT_DIRECTORY = ".git"


def ingest_revision(tenant_id: str, repo_path: str) -> IntentRevision:
    """Project the repository's HEAD into a new active revision.

    Idempotent on `(tenant_id, commit_sha)`: the same commit arriving twice
    returns the existing revision and writes nothing. That holds for a re-poll,
    a retried task, and a replayed webhook alike, none of which are trusted to
    be delivered exactly once.

    Raises `RepositoryUnavailable` if `repo_path` cannot be read as a Git
    worktree, and `InvalidRevision` if any document fails validation. Those two
    are the whole contract, which is what lets the poll task promise it never
    raises.
    """
    commit_sha = head_sha(repo_path)
    existing = _existing_revision(tenant_id, commit_sha)
    if existing is not None:
        return existing

    # Read the kinds once and serve both validation and projection from that
    # snapshot, so a kind cannot be validated against and then vanish before it
    # is used.
    kinds_by_name = {kind.name: kind for kind in Kind.objects.all()}
    schemas: KindSchemas = {name: kind.attribute_schema for name, kind in kinds_by_name.items()}

    # Raises InvalidRevision, carrying every error found, before anything is written.
    snapshots = parse_document_set(_read_documents(repo_path), tenant_id, schemas)

    try:
        return _project(tenant_id, commit_sha, snapshots, kinds_by_name)
    except IntegrityError as exc:
        # The check above and this insert are two statements, so under READ
        # COMMITTED another writer can land between them. The constraint holds
        # the invariant either way; what must not escape is its exception.
        #
        # `_project` is atomic, so its work is already rolled back here -- there
        # is no half-projected revision to clean up.
        return _resolve_conflict(tenant_id, commit_sha, exc)
    except OperationalError as exc:
        # The other way this race resolves, and the one the first pass missed. A
        # deadlock or serialization failure is not an IntegrityError -- psycopg
        # raises it under OperationalError -- so it escaped the handler above and
        # every except clause in the poll task, breaking that task's documented
        # promise never to raise.
        #
        # It is the same condition wearing a different code: the transaction was
        # rolled back because another writer was doing this at the same time. So
        # it gets the same answer, including the idempotency check.
        if not _is_concurrency_rollback(exc):
            raise
        return _resolve_conflict(tenant_id, commit_sha, exc)


def _is_concurrency_rollback(exc: OperationalError) -> bool:
    """Whether this database error means "another writer got in the way".

    Only the rollback codes count here, not `UNIQUE_VIOLATION`: a unique
    violation on this path arrives as `IntegrityError` and is already handled
    by the clause above. The reading of the SQLSTATE is shared with the
    collector (`datum.locks`); which codes matter is each caller's own question.
    """
    return sqlstate_of(exc) in CONCURRENCY_ROLLBACK_CODES


def _existing_revision(tenant_id: str, commit_sha: str) -> IntentRevision | None:
    """The revision for this commit, if one is already recorded.

    A named seam rather than an inline query: it is the exact point two triggers
    interleave at, so a test has somewhere to force the race deterministically.
    """
    return IntentRevision.objects.filter(tenant_id=tenant_id, commit_sha=commit_sha).first()


def _deactivate_current(tenant_id: str) -> int:
    """Stand down the active revision, returning how many rows changed.

    The second seam. Under READ COMMITTED this UPDATE cannot see a row another
    transaction has not yet committed, which is how two revisions can both go
    active (CF-5).
    """
    return IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).update(
        is_active=False
    )


def _resolve_conflict(
    tenant_id: str, commit_sha: str, exc: IntegrityError | OperationalError
) -> IntentRevision:
    """Answer a lost race, or convert it into this module's own exception.

    Takes either loss. A constraint rejection and a transaction rollback mean the
    same thing -- another writer was doing this at the same time -- and differ
    only in which part of the database noticed.

    Idempotency outranks the conflict. If the writer that won was projecting
    this very commit, the caller asked for a revision at that commit and there
    now is one -- returning it is the promised answer, not an error. That is
    CF-4, and under concurrency it is indistinguishable from an ordinary
    re-poll, which is the whole point of the idempotency promise.

    A genuine disagreement -- another commit claiming active while this one was
    projecting -- is CF-5, and the caller has to hear about it. It surfaces as
    `RevisionConflict` rather than `IntegrityError` so callers written against
    this module keep working.
    """
    winner = _existing_revision(tenant_id, commit_sha)
    if winner is not None:
        return winner
    raise RevisionConflict(
        f"another writer activated a revision for tenant {tenant_id} while "
        f"commit {commit_sha} was being projected: {exc}"
    ) from exc


def _read_documents(repo_path: str) -> list[DocumentSource]:
    """Every YAML file in the tree, sorted, named relative to the repository root.

    Layout carries no meaning: a document names its own kind, so which directory
    it sits in is the author's business rather than the ingester's. Sorted so a
    given commit always produces the same error ordering.
    """
    root = Path(repo_path)
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.suffix in DOCUMENT_SUFFIXES and GIT_DIRECTORY not in path.parts
    )
    return [(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")) for path in paths]


@transaction.atomic
def _project(
    tenant_id: str,
    commit_sha: str,
    snapshots: Sequence[ResourceSnapshot],
    kinds_by_name: Mapping[str, Kind],
) -> IntentRevision:
    """Full rebuild: write this revision's complete row set and make it active.

    Prior revisions' rows are retained, keyed to their own revision. Readers of
    the declared plane select through the active revision -- a reader that does
    not is a defect, because it will see every revision at once.

    One transaction, so the deactivate/create/write sequence has no reachable
    half-state.
    """
    _deactivate_current(tenant_id)
    revision = IntentRevision.objects.create(
        tenant_id=tenant_id, commit_sha=commit_sha, is_active=True
    )
    DeclaredResource.objects.bulk_create(
        [
            DeclaredResource(
                tenant_id=tenant_id,
                kind=_kind_for(kinds_by_name, snapshot),
                name=snapshot.name,
                scope=snapshot.scope,
                provider_id=None,
                attributes=dict(snapshot.attributes),
                revision=revision,
            )
            for snapshot in snapshots
        ]
    )
    return revision


def _kind_for(kinds_by_name: Mapping[str, Kind], snapshot: ResourceSnapshot) -> Kind:
    kind = kinds_by_name.get(snapshot.kind)
    # Interior of the barricade: the validator resolved this name against this
    # very mapping, so a miss here is a bug, not bad input.
    assert kind is not None, f"validated snapshot names unknown kind {snapshot.kind!r}"
    return kind
