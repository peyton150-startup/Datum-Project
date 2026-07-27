"""Ingest a Git revision into the declared plane: DESIGN section 10.

The boundary between a working tree and the domain. Reads files, hands their
text to the validator, and projects the result. Nothing here decides whether a
document is valid -- that is `documents` -- and nothing here talks to a
provider.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from django.db import transaction

from datum.graph.models import DeclaredResource
from datum.intent.documents import DocumentSource, KindSchemas, parse_document_set
from datum.intent.models import IntentRevision
from datum.intent.repository import head_sha
from datum.kinds.models import Kind
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
    existing = IntentRevision.objects.filter(tenant_id=tenant_id, commit_sha=commit_sha).first()
    if existing is not None:
        return existing

    # Read the kinds once and serve both validation and projection from that
    # snapshot, so a kind cannot be validated against and then vanish before it
    # is used.
    kinds_by_name = {kind.name: kind for kind in Kind.objects.all()}
    schemas: KindSchemas = {name: kind.attribute_schema for name, kind in kinds_by_name.items()}

    # Raises InvalidRevision, carrying every error found, before anything is written.
    snapshots = parse_document_set(_read_documents(repo_path), tenant_id, schemas)
    return _project(tenant_id, commit_sha, snapshots, kinds_by_name)


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
    IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).update(is_active=False)
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
