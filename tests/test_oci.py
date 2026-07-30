"""The Oracle Cloud collector, and the second kind (WBS 1.4.3).

Two things are under test here, and the second matters more than the first.

The collector is ordinary: a normalizer, a recorded payload, and the same
partial-failure behaviour every collector owes. Worth testing, not interesting.

**The interesting part is that this is kind number two.** ADR-001 says a kind is
data rather than code, and DESIGN §24 names the early warning that would falsify
it: *"the second kind requires a migration."* Until now the bet was untestable --
one kind cannot falsify a claim about the second. These tests are where it gets
checked, which is why they reach past discovery into projection and
reconciliation rather than stopping at the collector boundary.
"""

import json

import pytest

from datum.discovery.collector import run_collector
from datum.discovery.errors import MalformedProviderData, ProviderUnavailable
from datum.discovery.models import DiscoveredResource
from datum.discovery.oci import KIND_NAME, OracleCloudCollector, from_recording
from datum.discovery.recorded import RecordedSource
from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/oci/instances.json"
COMPARTMENT = "ocid1.compartment.oc1..aaaaprod"

pytestmark = pytest.mark.django_db


def collect(source: str = FIXTURE):
    return run_collector(from_recording(source), TENANT)


def instance(name: str, **overrides) -> dict:
    record = {
        "id": f"ocid1.instance.oc1..{name}",
        "display-name": name,
        "compartment-id": COMPARTMENT,
        "shape": "VM.Standard.A1.Flex",
        "shape-config": {"ocpus": 2},
        "lifecycle-state": "RUNNING",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# The second kind: is it really just data?
# ---------------------------------------------------------------------------


def test_the_second_kind_exists_as_data_with_no_model_of_its_own():
    """ADR-001's central claim, finally testable.

    A `Kind` row carries the shape. Nothing about `ComputeInstance` appears in
    any model, column, or constraint -- if it did, adding the third kind would
    cost a migration too and the schema-defined bet would be dead.
    """
    kind = Kind.objects.get(name=KIND_NAME)

    assert kind.attribute_schema == {"shape": "str", "ocpus": "int"}


def test_both_kinds_share_the_same_discovered_table():
    """The two planes are two tables, not two-tables-per-kind. A Deployment and
    a compute instance are rows in the same place, distinguished by a foreign
    key rather than by a schema."""
    collect()

    kinds_present = set(
        DiscoveredResource.objects.filter(tenant_id=TENANT).values_list("kind__name", flat=True)
    )
    assert kinds_present == {KIND_NAME}
    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2


def test_two_kinds_reconcile_together_without_interfering(intent_repo):
    """End to end across both kinds at once, which is the real test of the bet.

    A repo declaring one Deployment and one compute instance, a discovery run
    for each, and a diff that keeps them apart. If kinds leaked into each other
    -- a shared natural key, a matcher that ignored kind -- this is where it
    would show, as a cross-kind match or a phantom orphan.
    """
    from datum.discovery.kubernetes import from_recording as k8s_from_recording
    from datum.enums import DiscrepancyType
    from datum.intent.ingest import ingest_revision
    from datum.reconcile.models import Discrepancy, Match
    from datum.reconcile.service import run_reconciliation

    ingest_revision(TENANT, intent_repo("fixtures/intent-repo-two-kinds"))
    run_collector(k8s_from_recording("fixtures/k8s/deployments.json"), TENANT)
    collect()

    run_reconciliation(TENANT)

    # web (Deployment, declared 3 / discovered 5) and web-1 (ComputeInstance,
    # declared and discovered identical) both matched, on their own kinds.
    assert Match.objects.filter(tenant_id=TENANT).count() == 2
    field_discrepancies = Discrepancy.objects.filter(
        tenant_id=TENANT, discrepancy_type=DiscrepancyType.FIELD
    )
    assert field_discrepancies.count() == 1
    assert (field_discrepancies.get().kind_name, field_discrepancies.get().field_name) == (
        "Deployment",
        "replicas",
    )
    # db-1 is discovered and never declared; nothing else is orphaned.
    orphans = Discrepancy.objects.filter(
        tenant_id=TENANT, discrepancy_type=DiscrepancyType.DISCOVERED_UNDECLARED
    )
    assert [o.name for o in orphans] == ["db-1"]


def test_the_same_name_in_two_kinds_is_two_resources(intent_repo):
    """The natural key includes the kind, so a Deployment and an instance may
    share a name without colliding. Untestable before a second kind existed."""
    from datum.discovery.kubernetes import from_recording as k8s_from_recording

    run_collector(k8s_from_recording("fixtures/k8s/deployments.json"), TENANT)
    collect()

    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1
    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web-1").count() == 1


# ---------------------------------------------------------------------------
# The collector: the ordinary obligations every collector owes
# ---------------------------------------------------------------------------


def test_a_clean_run_writes_what_it_read():
    """The fixture is multi-record with one bad record, per DESIGN section 11 --
    single-record fixtures are what let CF-1 hide."""
    run = collect()

    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)
    assert run.status == CollectorRunStatus.PARTIAL


def test_one_bad_record_does_not_take_the_good_ones_with_it():
    """CF-1 again, from the second adapter. The framework is what prevents it,
    so this is really a test that the new collector uses the framework rather
    than working around it."""
    collect()

    names = set(DiscoveredResource.objects.filter(tenant_id=TENANT).values_list("name", flat=True))
    assert names == {"web-1", "db-1"}


def test_read_equals_written_plus_errors():
    run = collect()

    assert run.resources_read == run.resources_written + run.errors


def test_running_twice_is_idempotent():
    collect()
    collect()

    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2


def test_a_missing_payload_is_failed_not_an_empty_estate(tmp_path):
    run = collect(str(tmp_path / "absent.json"))

    assert run.status == CollectorRunStatus.FAILED


def test_the_collector_declares_the_kind_it_produces():
    """The 1.4.4 invariant, which the framework asserts on every record. A
    second collector is the first chance to get it wrong."""
    collector = from_recording(FIXTURE)

    assert collector.kind == KIND_NAME
    assert collector.normalize(instance("x"), TENANT).kind == collector.kind


# ---------------------------------------------------------------------------
# The normalizer: provider vocabulary in, Datum vocabulary out
# ---------------------------------------------------------------------------


def test_provider_names_are_translated_to_datum_names():
    """A compartment becomes a scope and a display name becomes a name. The
    translation is this module's whole reason to exist -- an intent document for
    an instance must not look like an OCI API call."""
    snapshot = from_recording(FIXTURE).normalize(instance("web-1"), TENANT)

    assert snapshot.natural_key == (KIND_NAME, TENANT, COMPARTMENT, "web-1")
    assert snapshot.provider_id == "ocid1.instance.oc1..web-1"
    assert snapshot.attributes == {"shape": "VM.Standard.A1.Flex", "ocpus": 2}


def test_lifecycle_state_is_not_carried_across():
    """Present in the payload and deliberately not an attribute.

    `attribute_schema` is shared by both planes, so every attribute must be
    something intent can declare, and no author declares that an instance is
    currently RUNNING. Recorded as a precedence question in PROJECT_PLAN, not
    modelled here.
    """
    snapshot = from_recording(FIXTURE).normalize(instance("web-1"), TENANT)

    assert "lifecycle_state" not in snapshot.attributes
    assert "lifecycle-state" not in snapshot.attributes


@pytest.mark.parametrize(
    "missing_field",
    ["display-name", "compartment-id", "id", "shape"],
)
def test_each_missing_top_level_field_is_rejected_and_named(missing_field):
    record = instance("x")
    del record[missing_field]

    with pytest.raises(MalformedProviderData) as caught:
        from_recording(FIXTURE).normalize(record, TENANT)

    assert missing_field in str(caught.value)


def test_a_missing_nested_ocpus_is_rejected_and_named():
    """The one field behind a nested key, which is where a flat lookup breaks."""
    with pytest.raises(MalformedProviderData) as caught:
        from_recording(FIXTURE).normalize(instance("x", **{"shape-config": {}}), TENANT)

    assert "shape-config.ocpus" in str(caught.value)


def test_a_shape_config_that_is_not_a_mapping_is_rejected():
    """The parent of a required field being the wrong type must read as absent
    rather than raising AttributeError from inside the normalizer."""
    with pytest.raises(MalformedProviderData):
        from_recording(FIXTURE).normalize(instance("x", **{"shape-config": "flex"}), TENANT)


def test_a_rejection_names_every_missing_field_not_only_the_first():
    with pytest.raises(MalformedProviderData) as caught:
        from_recording(FIXTURE).normalize({}, TENANT)

    message = str(caught.value)
    for field in ("display-name", "compartment-id", "id", "shape", "shape-config.ocpus"):
        assert field in message


@pytest.mark.parametrize("record", [None, "a string", 7, ["a", "list"]])
def test_a_record_that_is_not_a_mapping_is_rejected(record):
    with pytest.raises(MalformedProviderData):
        from_recording(FIXTURE).normalize(record, TENANT)


def test_ocpus_of_zero_would_be_a_value_not_a_missing_field():
    """The boundary a truthiness check gets wrong. Not a shape OCI offers, but
    the normalizer must not invent a rule the provider did not state."""
    snapshot = from_recording(FIXTURE).normalize(
        instance("x", **{"shape-config": {"ocpus": 0}}), TENANT
    )

    assert snapshot.attributes["ocpus"] == 0


# ---------------------------------------------------------------------------
# The shared recorded source
# ---------------------------------------------------------------------------


def test_the_envelope_key_is_the_providers_not_datums(tmp_path):
    """OCI says `data` where Kubernetes says `items`. One class, parameterized,
    because the difference belongs to the providers rather than to Datum."""
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"data": [instance("only")]}), encoding="utf-8")

    assert len(RecordedSource(str(path), "data").read()) == 1


def test_a_payload_missing_its_envelope_key_is_unavailable(tmp_path):
    """Not an empty estate: there is no record to reject, so nothing was
    observed and the run learns nothing about what exists."""
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"items": [instance("wrong-envelope")]}), encoding="utf-8")

    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(path), "data").read()


def test_an_empty_list_is_a_legitimately_empty_estate(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"data": []}), encoding="utf-8")

    assert RecordedSource(str(path), "data").read() == []


def test_invalid_json_is_unavailable(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(path), "data").read()


def test_a_payload_that_is_not_an_envelope_is_unavailable(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(json.dumps([instance("bare-list")]), encoding="utf-8")

    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(path), "data").read()


# ---------------------------------------------------------------------------
# Absence, scoped per collector
# ---------------------------------------------------------------------------


def test_a_kubernetes_run_does_not_mark_oci_resources_absent():
    """The 1.4.4 scope rule, now testable with two real collectors instead of a
    fabricated one. A successful Kubernetes read says nothing about Oracle
    Cloud, and a rule that forgot to scope by collector would empty this estate
    on the next Deployment poll."""
    from datum.discovery.kubernetes import from_recording as k8s_from_recording

    collect()
    k8s_from_recording("fixtures/k8s/deployments.json")

    run_collector(k8s_from_recording("fixtures/k8s/deployments.json"), TENANT)

    assert not DiscoveredResource.objects.filter(
        tenant_id=TENANT, kind__name=KIND_NAME, is_absent=True
    ).exists()


def test_an_oci_resource_that_disappears_is_marked_absent(tmp_path):
    """And the other direction: this collector's own absence still works."""
    collect()
    shrunk = tmp_path / "instances.json"
    shrunk.write_text(json.dumps({"data": [instance("web-1")]}), encoding="utf-8")

    collect(str(shrunk))

    db = DiscoveredResource.objects.get(tenant_id=TENANT, name="db-1")
    assert db.is_absent is True


def test_the_collector_is_the_one_it_says_it_is():
    """`collector_name` is what absence scopes on, so a copy-paste from the
    Kubernetes adapter that left the name behind would silently let each
    collector mark the other's resources absent."""
    run = collect()

    assert run.collector_name == "oci"
    assert OracleCloudCollector.name == "oci"
