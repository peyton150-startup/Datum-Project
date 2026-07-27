import pytest

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import MalformedProviderData, read_deployment_fixture
from datum.discovery.models import DiscoveredResource
from datum.enums import CollectorRunStatus

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
MALFORMED_FIXTURE = "fixtures/k8s/deployments-malformed.json"
pytestmark = pytest.mark.django_db


def test_fixture_normalizes_to_snapshots():
    snaps = read_deployment_fixture(FIXTURE, TENANT)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.kind == "Deployment"
    assert s.scope == "default"
    assert s.name == "web"
    assert s.provider_id == "uid-web-1"
    assert s.attributes == {"replicas": 5}


def test_run_writes_one_discovered_row_and_records_counts():
    run = run_collector(TENANT, FIXTURE)
    assert run.status == CollectorRunStatus.SUCCESS
    assert run.resources_read == 1
    assert run.resources_written == 1
    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1


def test_snapshot_natural_key_carries_the_tenant():
    """The tenant is one of the four natural-key components, so it must be set.

    A blank tenant makes a snapshot match nothing: the same resource would be
    reported as one declared orphan plus one discovered orphan.
    """
    snap = read_deployment_fixture(FIXTURE, TENANT)[0]
    assert snap.natural_key == ("Deployment", TENANT, "default", "web")


def test_record_missing_replicas_is_rejected_at_the_barricade():
    with pytest.raises(MalformedProviderData):
        read_deployment_fixture(MALFORMED_FIXTURE, TENANT)


def test_malformed_provider_data_yields_partial_run_and_writes_nothing():
    run = run_collector(TENANT, MALFORMED_FIXTURE)
    assert run.status == CollectorRunStatus.PARTIAL
    assert run.errors == 1
    assert run.resources_written == 0
    assert not DiscoveredResource.objects.filter(tenant_id=TENANT).exists()


def test_running_twice_is_idempotent():
    run_collector(TENANT, FIXTURE)
    run_collector(TENANT, FIXTURE)
    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1
