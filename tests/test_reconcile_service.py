import pytest

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.enums import DiscrepancyState, DiscrepancyType
from datum.intent.ingest import ingest_revision
from datum.reconcile.models import Discrepancy, Match
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
pytestmark = pytest.mark.django_db


def test_reconciliation_writes_one_match_and_one_field_discrepancy(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)

    assert Match.objects.filter(tenant_id=TENANT).count() == 1
    open_ = Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN)
    assert open_.count() == 1
    d = open_.get()
    assert d.discrepancy_type == DiscrepancyType.FIELD
    assert d.field_name == "replicas"
    assert d.declared_value == 3
    assert d.discovered_value == 5


def test_rerun_is_idempotent(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)
    run_reconciliation(TENANT)
    assert Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN).count() == 1
    assert Match.objects.filter(tenant_id=TENANT).count() == 1


def test_discovered_resource_with_no_declared_plane_is_undeclared():
    """No active revision at all: every discovered resource is an orphan, not a crash."""
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)

    assert not Match.objects.filter(tenant_id=TENANT).exists()
    d = Discrepancy.objects.get(tenant_id=TENANT)
    assert d.discrepancy_type == DiscrepancyType.DISCOVERED_UNDECLARED
    assert d.name == "web"
    assert d.field_name is None


def test_declared_but_never_provisioned_is_declared_missing(intent_repo):
    """Intent exists, the collector never ran: the resource is missing, not matched."""
    ingest_revision(TENANT, intent_repo())
    run_reconciliation(TENANT)

    assert not Match.objects.filter(tenant_id=TENANT).exists()
    d = Discrepancy.objects.get(tenant_id=TENANT)
    assert d.discrepancy_type == DiscrepancyType.DECLARED_MISSING
    assert d.name == "web"
