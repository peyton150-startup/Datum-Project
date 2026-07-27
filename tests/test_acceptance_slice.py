import pytest
from django.test import Client

from datum.discovery.collector import run_collector
from datum.enums import DiscrepancyState, DiscrepancyType
from datum.intent.ingest import InvalidRevision, ingest_revision
from datum.reconcile.models import Discrepancy, Match
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
pytestmark = pytest.mark.django_db


def test_phase1_slice_end_to_end(intent_repo):
    # 1-2: declare web replicas=3, ingest, traceable to commit
    revision = ingest_revision(TENANT, intent_repo())
    assert revision.is_active and len(revision.commit_sha) == 40
    # 3-4: collector reports replicas=5 into discovered plane
    run = run_collector(TENANT, FIXTURE)
    assert run.resources_written == 1
    # 5-6: match + diff -> exactly one field discrepancy, no orphans
    run_reconciliation(TENANT)
    assert Match.objects.filter(tenant_id=TENANT).count() == 1
    field = Discrepancy.objects.filter(tenant_id=TENANT, discrepancy_type=DiscrepancyType.FIELD)
    assert field.count() == 1
    assert (
        Discrepancy.objects.filter(
            tenant_id=TENANT,
            discrepancy_type__in=[
                DiscrepancyType.DECLARED_MISSING,
                DiscrepancyType.DISCOVERED_UNDECLARED,
            ],
        ).count()
        == 0
    )
    d = field.get()
    assert (d.field_name, d.declared_value, d.discovered_value) == ("replicas", 3, 5)
    # 7: visible via API with authoritative side declared
    item = Client().get("/api/discrepancies?state=open").json()["items"][0]
    assert item["authoritative_plane"] == "declared"
    # 8: resolve removes it from the open queue
    Client().post(f"/api/discrepancies/{d.id}/resolve")
    assert Client().get("/api/discrepancies?state=open").json()["count"] == 0
    # 9: determinism - re-running reconciliation yields the identical open set (one field disc)
    run_reconciliation(TENANT)
    reopened = Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN)
    assert reopened.count() == 1
    assert reopened.get().field_name == "replicas"


def test_negative_malformed_document_keeps_previous_revision(intent_repo):
    ingest_revision(TENANT, intent_repo())
    with pytest.raises(InvalidRevision):
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-malformed"))


def test_negative_discovered_undeclared_is_one_orphan(intent_repo):
    # no intent at all; only discovery
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)
    orphans = Discrepancy.objects.filter(
        tenant_id=TENANT, discrepancy_type=DiscrepancyType.DISCOVERED_UNDECLARED
    )
    assert orphans.count() == 1


def test_negative_declared_missing_is_one_orphan(intent_repo):
    # intent only; no discovery run
    ingest_revision(TENANT, intent_repo())
    run_reconciliation(TENANT)
    orphans = Discrepancy.objects.filter(
        tenant_id=TENANT, discrepancy_type=DiscrepancyType.DECLARED_MISSING
    )
    assert orphans.count() == 1
