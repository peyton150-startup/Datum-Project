import logging.config

import pytest
from django.conf import settings
from django.test import Client

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.intent.ingest import ingest_revision
from datum.kinds.models import Kind
from datum.reconcile.models import Discrepancy
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def test_reconciliation_emits_using_supported_logging_configuration(intent_repo, capsys):
    """No test handler: removing Datum's INFO level or root handler fails this."""
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording("fixtures/k8s/deployments.json"), TENANT)
    try:
        logging.config.dictConfig(settings.LOGGING)
        capsys.readouterr()
        run_reconciliation(TENANT)
        output = capsys.readouterr().err
        assert output.count("[DIFF]") == 1
        assert "datum.reconcile.audit" in output
        assert "kind=Deployment, field=replicas" in output
        assert "result: DISCREPANCY" in output
    finally:
        logging.config.dictConfig(settings.LOGGING)


def test_uncovered_field_is_actionable_in_api_and_run_continues(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording("fixtures/k8s/deployments.json"), TENANT)
    # Policy was complete when saved; the kind later gains another field.
    kind = Kind.objects.get(name="Deployment")
    kind.attribute_schema = {"replicas": "int", "new_field": "str"}
    kind.save()
    from datum.discovery.models import DiscoveredResource

    resource = DiscoveredResource.objects.get(tenant_id=TENANT)
    resource.attributes["new_field"] = None
    resource.save()
    assert run_reconciliation(TENANT)
    items = Client().get("/api/discrepancies?state=open").json()["items"]
    undecidable = next(item for item in items if item["field_name"] == "new_field")
    assert undecidable["discrepancy_type"] == "missing_comparison_policy"
    assert undecidable["authoritative_plane"] is None
    assert undecidable["declared"] == {"present": False, "value": None}
    assert undecidable["discovered"] == {"present": True, "value": None}
    assert Discrepancy.objects.filter(discrepancy_type="field").count() == 1
