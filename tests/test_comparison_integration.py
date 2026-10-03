import logging.config

import pytest
from django.conf import settings
from django.test import Client

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.discovery.models import DiscoveredResource
from datum.discovery.oci import from_recording as from_oci_recording
from datum.intent.ingest import ingest_revision
from datum.kinds.models import Kind
from datum.reconcile.models import Discrepancy
from datum.reconcile.policy import configure_comparison_policy
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def test_real_service_obeys_policy_instead_of_exact_seed_behavior(intent_repo):
    """Removing the loader or ignoring its schema restores drift for 3 vs 5."""
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording("fixtures/k8s/deployments.json"), TENANT)
    configure_comparison_policy(
        Kind.objects.get(name="Deployment"),
        {"replicas": {"comparison": {"mode": "tolerance(2)"}, "logging": "debug"}},
    )
    assert run_reconciliation(TENANT)
    assert not Discrepancy.objects.filter(tenant_id=TENANT).exists()


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
        # The capture fixture closes its stream when this test ends. Restore
        # supported logging against the real stream, not that temporary one.
        with capsys.disabled():
            logging.config.dictConfig(settings.LOGGING)


def test_uncovered_field_is_actionable_in_api_and_run_continues(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording("fixtures/k8s/deployments.json"), TENANT)
    # Policy was complete when saved; the kind later gains another field.
    kind = Kind.objects.get(name="Deployment")
    kind.attribute_schema = {"replicas": "int", "new_field": "str"}
    kind.save()
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


def test_malformed_declared_schema_does_not_abort_other_kinds(intent_repo):
    """A list-valued schema used to abort the tenant before any result persisted."""
    ingest_revision(TENANT, intent_repo("fixtures/intent-repo-two-kinds"))
    run_collector(from_recording("fixtures/k8s/deployments.json"), TENANT)
    run_collector(from_oci_recording("fixtures/oci/instances.json"), TENANT)
    DiscoveredResource.objects.filter(kind__name="ComputeInstance", name="web-1").update(
        attributes={"shape": "VM.Standard.A1.Flex", "ocpus": 4}
    )
    Kind.objects.filter(name="Deployment").update(attribute_schema=["bad"])

    assert run_reconciliation(TENANT)
    broken = Discrepancy.objects.get(kind_name="Deployment")
    assert broken.discrepancy_type == "missing_comparison_policy"
    assert broken.field_name == "replicas"
    assert (broken.declared_present, broken.declared_value) == (True, 3)
    assert (broken.discovered_present, broken.discovered_value) == (True, 5)
    assert broken.authoritative_plane is None
    assert Discrepancy.objects.filter(
        kind_name="ComputeInstance", discrepancy_type="field"
    ).exists()
