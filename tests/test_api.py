import pytest
from django.test import Client

from datum.discovery.collector import run_collector
from datum.enums import DiscrepancyState
from datum.intent.ingest import ingest_revision
from datum.reconcile.models import Discrepancy
from datum.reconcile.service import run_reconciliation

FIXTURE = "fixtures/k8s/deployments.json"
TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)


def test_list_open_discrepancies(seeded):
    body = Client().get("/api/discrepancies?state=open").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["field_name"] == "replicas"
    assert item["declared_value"] == 3
    assert item["discovered_value"] == 5
    assert item["authoritative_plane"] == "declared"


def test_resolve_removes_from_open_queue(seeded):
    disc_id = Discrepancy.objects.get(tenant_id=TENANT).id
    resp = Client().post(f"/api/discrepancies/{disc_id}/resolve")
    assert resp.status_code == 200
    assert Discrepancy.objects.get(id=disc_id).state == DiscrepancyState.RESOLVED
    body = Client().get("/api/discrepancies?state=open").json()
    assert body["count"] == 0


def test_list_declared_resources(seeded):
    body = Client().get("/api/resources?plane=declared").json()
    assert body["count"] == 1
    assert body["items"][0]["name"] == "web"


def test_list_discovered_resources_shows_the_other_plane(seeded):
    body = Client().get("/api/resources?plane=discovered").json()
    assert body["count"] == 1
    assert body["items"][0]["attributes"] == {"replicas": 5}


def test_resolving_an_unknown_discrepancy_is_404(seeded):
    assert Client().post("/api/discrepancies/999999/resolve").status_code == 404
