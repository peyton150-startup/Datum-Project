import pytest

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.enums import DiscrepancyState, DiscrepancyType, MatchState
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
    assert (d.declared_present, d.declared_value) == (True, 3)
    assert (d.discovered_present, d.discovered_value) == (True, 5)


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


# CF-6's write half. The anchor columns are what a match is about, and the
# service wrote them nowhere: every row landed on ('', '', ''). One pair per run
# hid it, because a single empty anchor collides with nothing.


def test_a_written_match_carries_the_anchor_it_was_decided_about(intent_repo):
    """Without this the row is unfindable and uncountable.

    `_stored_decisions` looks a decision up by these four columns, and the
    active-match index is unique over them. An empty anchor is therefore both a
    decision that can never be read back and a row that refuses to have a
    sibling -- the second pair in any run is an IntegrityError.
    """
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)

    match = Match.objects.get(tenant_id=TENANT)
    assert (match.declared_kind, match.declared_scope, match.declared_name) == (
        "Deployment",
        "default",
        "web",
    )
    assert match.discovered_provider_id == "uid-web-1"


def test_two_pairs_in_one_run_get_two_distinct_anchors(intent_repo):
    """The shape the defect actually took, reproduced at its narrowest.

    Both pairs are the same kind in the same scope, so the anchor differs by
    name alone -- the least distinguishing case, and the one an empty anchor
    collapses. `uq_active_match_per_declared` is unique over the four columns,
    so before the fix the second write raised IntegrityError rather than
    producing a wrong row: two matches existing at all is the assertion.
    """
    ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))
    run_collector(from_recording("fixtures/k8s/deployments-pair.json"), TENANT)

    run_reconciliation(TENANT)

    anchors = {
        (m.declared_kind, m.declared_scope, m.declared_name, m.discovered_provider_id)
        for m in Match.objects.filter(tenant_id=TENANT)
    }
    assert anchors == {
        ("Deployment", "default", "web", "uid-web-1"),
        ("Deployment", "default", "api", "uid-api-1"),
    }


def test_a_confirmed_match_survives_a_rerun_undemoted(intent_repo):
    """The matcher re-derives a confirmed pairing every run; the row must not be rewritten.

    A fresh proposal for the same anchor would collide with the human's row on
    the active-match index, and demote a decision to a proposal if it did not.
    The row keeps the strategy it was proposed under: it is the human's record,
    not this run's output.
    """
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)
    Match.objects.filter(tenant_id=TENANT).update(state=MatchState.CONFIRMED)

    run_reconciliation(TENANT)

    match = Match.objects.get(tenant_id=TENANT)
    assert match.state == MatchState.CONFIRMED
    assert match.declared_name == "web"


def test_a_confirmed_match_still_has_its_fields_compared(intent_repo):
    """The nearby case the fix must not break.

    Leaving the match row alone is about the pairing, not about the diff. A
    confirmed pair is still two resources that can drift, and the run that
    skips rewriting its row must still report the field that differs.
    """
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)
    Match.objects.filter(tenant_id=TENANT).update(state=MatchState.CONFIRMED)

    run_reconciliation(TENANT)

    d = Discrepancy.objects.get(tenant_id=TENANT, state=DiscrepancyState.OPEN)
    assert (d.discrepancy_type, d.field_name) == (DiscrepancyType.FIELD, "replicas")
    assert (d.declared_value, d.discovered_value) == (3, 5)
