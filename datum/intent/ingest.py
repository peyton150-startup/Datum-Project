import subprocess
from pathlib import Path

from django.db import transaction

from datum.graph.models import DeclaredResource
from datum.intent.documents import InvalidDocument, parse_deployment_document
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind
from datum.reconcile.domain import ResourceSnapshot


class InvalidRevision(Exception):
    """A revision failed validation and was rejected whole; no state was written."""


def ingest_revision(tenant_id: str, repo_path: str) -> IntentRevision:
    commit_sha = _head_sha(repo_path)
    existing = IntentRevision.objects.filter(tenant_id=tenant_id, commit_sha=commit_sha).first()
    if existing is not None:
        return existing
    snapshots = _parse_all(tenant_id, repo_path)  # raises InvalidRevision on any bad doc
    return _project(tenant_id, commit_sha, snapshots)


def _head_sha(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parse_all(tenant_id: str, repo_path: str) -> list[ResourceSnapshot]:
    documents = sorted(Path(repo_path, "deployments").glob("*.yaml"))
    snapshots: list[ResourceSnapshot] = []
    for path in documents:
        try:
            snapshots.append(parse_deployment_document(path.read_text(encoding="utf-8"), tenant_id))
        except InvalidDocument as exc:
            raise InvalidRevision(f"{path.name}: {exc}") from exc
    return snapshots


@transaction.atomic
def _project(tenant_id: str, commit_sha: str, snapshots: list[ResourceSnapshot]) -> IntentRevision:
    IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).update(is_active=False)
    revision = IntentRevision.objects.create(
        tenant_id=tenant_id, commit_sha=commit_sha, is_active=True
    )
    kind = Kind.objects.get(name="Deployment")
    for snap in snapshots:
        DeclaredResource.objects.create(
            tenant_id=tenant_id,
            kind=kind,
            name=snap.name,
            scope=snap.scope,
            provider_id=None,
            attributes=dict(snap.attributes),
            revision=revision,
        )
    return revision
