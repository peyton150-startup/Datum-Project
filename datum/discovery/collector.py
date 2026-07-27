from django.utils import timezone

from datum.discovery.kubernetes import MalformedProviderData, read_deployment_fixture
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind

COLLECTOR_NAME = "kubernetes"


def run_collector(tenant_id: str, fixture_path: str) -> CollectorRun:
    run = CollectorRun.objects.create(
        tenant_id=tenant_id,
        collector_name=COLLECTOR_NAME,
        status=CollectorRunStatus.SUCCESS,
    )
    kind = Kind.objects.get(name="Deployment")
    read = written = errors = 0
    for record in _read(fixture_path, tenant_id):
        read += 1
        if record is None:
            errors += 1
            continue
        _upsert(tenant_id, kind, record, run)
        written += 1
    run.resources_read = read
    run.resources_written = written
    run.errors = errors
    run.status = CollectorRunStatus.PARTIAL if errors else CollectorRunStatus.SUCCESS
    run.finished_at = timezone.now()
    run.save()
    return run


def _read(fixture_path: str, tenant_id: str) -> list:
    try:
        return list(read_deployment_fixture(fixture_path, tenant_id))
    except MalformedProviderData:
        return [None]


def _upsert(tenant_id: str, kind: Kind, snapshot, run: CollectorRun) -> None:
    DiscoveredResource.objects.update_or_create(
        tenant_id=tenant_id,
        kind=kind,
        scope=snapshot.scope,
        name=snapshot.name,
        defaults={
            "provider_id": snapshot.provider_id,
            "attributes": dict(snapshot.attributes),
            "run": run,
        },
    )
