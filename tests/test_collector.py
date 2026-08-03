"""Collector framework tests: DESIGN section 11.

The CF-1 cases are the reason this phase exists, so they are stated as the plan
reproduced them -- three records, the middle one broken -- rather than as a tidy
pair. The counts are as much the subject as the rows: `resources_read`
under-reporting what a run saw is the silent half of that defect.
"""

import datetime
import json

import pytest

from datum.discovery.collector import (
    _refuse_unstorable_attributes,
    _unstorable_attribute,
    run_collector,
)
from datum.discovery.errors import MalformedProviderData, ProviderUnavailable
from datum.discovery.kubernetes import ENVELOPE_KEY, from_recording
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.discovery.recorded import RecordedSource
from datum.enums import CollectorRunStatus
from datum.reconcile.domain import ResourceSnapshot

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
MALFORMED_FIXTURE = "fixtures/k8s/deployments-malformed.json"
MULTI_FIXTURE = "fixtures/k8s/deployments-multi.json"

pytestmark = pytest.mark.django_db


def collect(source: str = FIXTURE, tenant_id: str = TENANT) -> CollectorRun:
    return run_collector(from_recording(source), tenant_id)


def names_written() -> set[str]:
    return set(DiscoveredResource.objects.filter(tenant_id=TENANT).values_list("name", flat=True))


# ---------------------------------------------------------------------------
# CF-1: one bad record must not take the good ones with it
# ---------------------------------------------------------------------------


def test_one_malformed_record_does_not_discard_the_good_ones():
    """CF-1, exactly as PROJECT_PLAN reproduced it.

    Three records, the middle one missing `spec.replicas`. Phase 1 returned
    read=1, written=0 and stored nothing. Every healthy record must survive a
    sibling's failure, because robustness is this component's first-priority
    quality attribute.
    """
    run = collect(MULTI_FIXTURE)

    assert run.resources_written == 2
    assert names_written() == {"api", "worker"}


def test_resources_read_counts_what_the_provider_returned_not_what_survived():
    """The silent half of CF-1.

    The run record is the audit trail for what the collector observed. Phase 1
    reported 1 for a three-record payload, so an operator could not tell that
    anything had been dropped at all.
    """
    run = collect(MULTI_FIXTURE)

    assert run.resources_read == 3
    assert run.errors == 1


def test_a_partial_run_is_reported_as_partial():
    run = collect(MULTI_FIXTURE)

    assert run.status == CollectorRunStatus.PARTIAL


def test_the_surviving_records_keep_their_own_attributes():
    """Not just row count: the right values must land on the right resources."""
    collect(MULTI_FIXTURE)

    by_name = {
        row.name: row.attributes for row in DiscoveredResource.objects.filter(tenant_id=TENANT)
    }
    assert by_name == {"api": {"replicas": 2}, "worker": {"replicas": 4}}


# ---------------------------------------------------------------------------
# The count invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [FIXTURE, MALFORMED_FIXTURE, MULTI_FIXTURE])
def test_read_equals_written_plus_errors(source):
    """DESIGN section 11 states this as an invariant and requires it tested.

    Parametrized across a clean payload, an all-bad payload, and a mixed one,
    because the interesting failure is a record counted into neither bucket or
    into both.
    """
    run = collect(source)

    assert run.resources_read == run.resources_written + run.errors


# ---------------------------------------------------------------------------
# Run status, and what may be read into it
# ---------------------------------------------------------------------------


def test_clean_run_is_success():
    run = collect()

    assert run.status == CollectorRunStatus.SUCCESS
    assert (run.resources_read, run.resources_written, run.errors) == (1, 1, 0)


def test_all_records_rejected_still_persists_nothing_and_reports_partial():
    """The far boundary of partial: every record bad, but a read did happen.

    PARTIAL rather than FAILED, because the provider answered.
    """
    run = collect(MALFORMED_FIXTURE)

    assert run.status == CollectorRunStatus.PARTIAL
    assert (run.resources_read, run.resources_written, run.errors) == (1, 0, 1)
    assert not DiscoveredResource.objects.filter(tenant_id=TENANT).exists()


def test_unreachable_provider_is_failed_not_an_empty_success(tmp_path):
    """The rule absence semantics rests on.

    A missing payload is not an empty estate. If this returned SUCCESS, a later
    phase would be entitled to read one outage as the deletion of everything.
    """
    run = collect(str(tmp_path / "no-such-payload.json"))

    assert run.status == CollectorRunStatus.FAILED
    assert (run.resources_read, run.resources_written, run.errors) == (0, 0, 0)


def test_an_empty_estate_read_successfully_is_success(tmp_path):
    """The other side of that boundary: zero records, but the provider answered.

    SUCCESS is the truthful answer here, and the distinction from the case above
    is the whole reason the unreachable path is handled separately.
    """
    payload = tmp_path / "empty.json"
    payload.write_text(json.dumps({"kind": "DeploymentList", "items": []}), encoding="utf-8")

    run = collect(str(payload))

    assert run.status == CollectorRunStatus.SUCCESS
    assert run.resources_read == 0


def test_a_run_that_never_finishes_is_left_failed():
    """A run is provisionally FAILED from the moment it opens.

    If the process dies mid-run, the row left behind must not claim success --
    absence semantics would then read a crash as a clean read of an empty
    estate.
    """

    class NeverReturns:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            raise KeyboardInterrupt("killed mid-read")

        def normalize(self, record, tenant_id):  # pragma: no cover - never reached
            raise AssertionError("normalize must not run when fetch dies")

    with pytest.raises(KeyboardInterrupt):
        run_collector(NeverReturns(), TENANT)

    run = CollectorRun.objects.filter(tenant_id=TENANT).latest("started_at")
    assert run.status == CollectorRunStatus.FAILED
    assert run.finished_at is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_twice_is_idempotent():
    collect()
    collect()

    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1


def test_running_twice_over_a_partial_payload_is_also_idempotent():
    """Idempotency has to survive the partial path too, which is where an upsert
    keyed on the wrong thing would show up as duplicated rows."""
    collect(MULTI_FIXTURE)
    collect(MULTI_FIXTURE)

    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2


def test_a_changed_attribute_updates_in_place_rather_than_duplicating(tmp_path):
    def payload_with(replicas: int) -> str:
        path = tmp_path / f"deployments-{replicas}.json"
        path.write_text(
            json.dumps(
                {
                    "kind": "DeploymentList",
                    "items": [
                        {
                            "metadata": {
                                "name": "web",
                                "namespace": "default",
                                "uid": "uid-web-1",
                            },
                            "spec": {"replicas": replicas},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return str(path)

    collect(payload_with(3))
    collect(payload_with(9))

    row = DiscoveredResource.objects.get(tenant_id=TENANT, name="web")
    assert row.attributes == {"replicas": 9}


# ---------------------------------------------------------------------------
# The unknown-kind path
# ---------------------------------------------------------------------------


def test_a_collector_whose_declared_kind_was_never_seeded_is_counted_not_raised():
    """Datum's own configuration being wrong is still no reason to lose a run.

    Reached differently since 1.4.4 added the one-kind invariant. A collector
    can no longer produce a kind other than the one it declares -- that is an
    assertion now -- so the way this path is still reachable is a collector
    declaring a kind for which no `Kind` row was ever seeded. Counted as a
    rejection rather than raised, so the run keeps going and records everything
    else it saw.
    """

    class UnseededKind:
        name = "kubernetes"
        kind = "NoSuchKind"

        def fetch(self):
            return [{"name": "ghost"}, {"name": "real"}]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record["name"],
                provider_id=f"uid-{record['name']}",
                attributes={"replicas": 1},
            )

    run = run_collector(UnseededKind(), TENANT)

    assert (run.resources_read, run.resources_written, run.errors) == (2, 0, 2)
    assert run.status == CollectorRunStatus.PARTIAL
    assert names_written() == set()


# ---------------------------------------------------------------------------
# The attribute-type barricade (issue #47)
# ---------------------------------------------------------------------------


def emitting(attributes: dict, name: str = "one") -> CollectorRun:
    """Run a collector whose normalizer emits exactly these attributes.

    Written past the adapters on purpose. Both shipped normalizers happen to
    emit clean types today, so a test going through them would assert the
    adapters' good behaviour rather than the framework's guarantee -- and the
    guarantee is the point: an adapter that has not been written yet must not
    be able to get this wrong.
    """

    class Emitter:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return [{"name": name}]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record["name"],
                provider_id=f"uid-{record['name']}",
                attributes=attributes,
            )

    return run_collector(Emitter(), TENANT)


@pytest.mark.parametrize(
    ("attributes", "because"),
    [
        ({"created": datetime.datetime(2026, 8, 3)}, "datetime is not a JSON type"),
        ({"created": datetime.date(2026, 8, 3)}, "date is not a JSON type"),
        ({"raw": b"bytes"}, "bytes is not a JSON type"),
        ({"tags": {"a", "b"}}, "set is not a JSON type"),
        ({"ratio": float("nan")}, "NaN is not representable in JSON"),
        ({"ratio": float("inf")}, "Infinity is not representable in JSON"),
        ({"ratio": float("-inf")}, "-Infinity is not representable in JSON"),
        ({"labels": {1: "a"}}, "an int key is silently rendered as a string"),
        ({"labels": {True: "a"}}, "a bool key is silently rendered as a string"),
        ({"ports": [1, datetime.date(2026, 8, 3)]}, "nested inside a list"),
        ({"spec": {"inner": {"when": datetime.date(2026, 8, 3)}}}, "nested two levels deep"),
        ({"spec": {"ports": [{"t": float("inf")}]}}, "nested through a list inside a dict"),
    ],
)
def test_an_attribute_that_cannot_be_stored_is_rejected_not_raised(attributes, because):
    """Each of these previously escaped as a raw TypeError or DataError.

    Measured before the barricade was written: `datetime`, `date`, `bytes` and
    `set` raised `TypeError` out of `json.dumps` inside Django's field
    serialization, and the non-finite floats raised `DataError` out of the
    driver, because `json.dumps` emits bare `NaN` and `Infinity` which are not
    valid JSON. Both arrived across a module boundary naming neither the
    collector nor the field.

    The two mapping-key cases never raised at all -- they were stored, silently
    coerced to `{"1": "a"}` and `{"true": "a"}`. Those are the ones a test that
    only looked for exceptions would have missed.

    `because` is unused by the assertion and is there to name the case in the
    test id.
    """
    run = emitting(attributes)

    assert (run.resources_read, run.resources_written, run.errors) == (1, 0, 1)
    assert run.status == CollectorRunStatus.PARTIAL
    assert names_written() == set()


@pytest.mark.parametrize(
    "attributes",
    [
        {"replicas": 3},
        {"name": "api"},
        {"enabled": True},
        {"ratio": 1.5},
        {"missing": None},
        {"ports": [80, 443]},
        {"labels": {"app": "api", "tier": "web"}},
        {"spec": {"ports": [{"port": 80, "tls": False}], "note": None}},
        {"empty_list": [], "empty_dict": {}},
        {"deep": {"a": {"b": {"c": [1, {"d": "e"}]}}}},
    ],
)
def test_ordinary_attributes_are_untouched(attributes):
    """The guard must refuse only what it was written to refuse.

    Every JSON-native shape, including the containers and the edge values a
    careless check would catch: `False` and `0` are not absent, `None` is a
    legal leaf rather than a missing one, and an empty container is not a
    violation. A barricade that rejected any of these would leave every test in
    the class above passing.
    """
    run = emitting(attributes)

    assert (run.resources_read, run.resources_written, run.errors) == (1, 1, 0)
    assert run.status == CollectorRunStatus.SUCCESS
    assert DiscoveredResource.objects.get(tenant_id=TENANT, name="one").attributes == attributes


def test_one_unstorable_record_does_not_take_its_siblings():
    """CF-1's rule applied to the new rejection reason.

    A type violation must behave exactly like every other malformed record:
    counted, logged, and survived. Rejecting it by letting the write fail would
    have aborted the transaction and lost the healthy rows -- which is why this
    is caught at normalize time rather than left to the database.
    """

    class Mixed:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return ["good-a", "bad", "good-b"]

        def normalize(self, record, tenant_id):
            attributes = (
                {"created": datetime.date(2026, 8, 3)} if record == "bad" else {"replicas": 1}
            )
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record,
                provider_id=f"uid-{record}",
                attributes=attributes,
            )

    run = run_collector(Mixed(), TENANT)

    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)
    assert names_written() == {"good-a", "good-b"}


def test_the_rejection_names_the_collector_the_resource_and_the_path():
    """The whole point: a domain error a reader can act on, not a TypeError.

    The old failure said `Object of type date is not JSON serializable` and
    named nothing else. This asserts the four facts an operator needs to find
    the offending normalizer: which collector, which resource, which attribute,
    and how far inside it.
    """

    class Nested:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return [1]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="prod",
                name="api",
                provider_id="uid-api",
                attributes={"spec": {"ports": [{"seen": datetime.date(2026, 8, 3)}]}},
            )

    with pytest.raises(MalformedProviderData) as caught:
        _refuse_unstorable_attributes(Nested(), Nested().normalize(1, TENANT))

    message = str(caught.value)
    assert "kubernetes" in message
    assert "Deployment/prod/api" in message
    assert "spec.ports[0].seen" in message
    assert "date" in message


def test_a_deeply_nested_payload_does_not_exhaust_the_stack():
    """The barricade must not become the crash it exists to prevent.

    A recursive walk raises `RecursionError` past ~1000 levels, which would be
    the same defect wearing a new hat: a boundary condition escaping as a raw
    Python exception. 5000 is comfortably past the default recursion limit, so
    this fails loudly if the walk is ever rewritten recursively.
    """
    deep: object = "leaf"
    for _ in range(5000):
        deep = {"k": [deep]}

    assert _unstorable_attribute({"nest": deep}) is None

    bad: object = datetime.date(2026, 8, 3)
    for _ in range(5000):
        bad = {"k": [bad]}

    assert "date" in (_unstorable_attribute({"nest": bad}) or "")


# ---------------------------------------------------------------------------
# The normalizer, on its own
# ---------------------------------------------------------------------------


def test_normalize_builds_the_full_natural_key():
    collector = from_recording(FIXTURE)
    record = collector.fetch()[0]

    snapshot = collector.normalize(record, TENANT)

    assert snapshot.natural_key == ("Deployment", TENANT, "default", "web")
    assert snapshot.provider_id == "uid-web-1"
    assert snapshot.attributes == {"replicas": 5}


@pytest.mark.parametrize(
    "record, missing",
    [
        ({"metadata": {"namespace": "d", "uid": "u"}, "spec": {"replicas": 1}}, "metadata.name"),
        ({"metadata": {"name": "n", "uid": "u"}, "spec": {"replicas": 1}}, "metadata.namespace"),
        ({"metadata": {"name": "n", "namespace": "d"}, "spec": {"replicas": 1}}, "metadata.uid"),
        ({"metadata": {"name": "n", "namespace": "d", "uid": "u"}, "spec": {}}, "spec.replicas"),
    ],
)
def test_each_missing_required_field_is_rejected_and_named(record, missing):
    """One case per natural-key component, plus the attribute.

    A rejection that does not name the field it is missing sends the operator
    back to the provider to guess.
    """
    with pytest.raises(MalformedProviderData) as caught:
        from_recording(FIXTURE).normalize(record, TENANT)

    assert missing in str(caught.value)


def test_a_rejection_names_every_missing_field_not_only_the_first():
    with pytest.raises(MalformedProviderData) as caught:
        from_recording(FIXTURE).normalize({"metadata": {}}, TENANT)

    message = str(caught.value)
    assert "metadata.name" in message
    assert "metadata.namespace" in message
    assert "metadata.uid" in message
    assert "spec.replicas" in message


@pytest.mark.parametrize("record", [None, "a string", 7, ["a", "list"]])
def test_a_record_that_is_not_a_mapping_is_rejected(record):
    with pytest.raises(MalformedProviderData):
        from_recording(FIXTURE).normalize(record, TENANT)


def test_a_record_whose_metadata_is_not_a_mapping_is_rejected():
    """The parent of a required field being the wrong type reads the same as the
    field being absent, and must not surface as an AttributeError."""
    with pytest.raises(MalformedProviderData):
        from_recording(FIXTURE).normalize(
            {"metadata": "not-a-mapping", "spec": {"replicas": 1}}, TENANT
        )


def test_replicas_of_zero_is_a_value_not_a_missing_field():
    """The boundary a truthiness check gets wrong.

    A Deployment scaled to zero is a real, readable state, and rejecting it
    would report a scaled-down service as unreadable junk.
    """
    snapshot = from_recording(FIXTURE).normalize(
        {"metadata": {"name": "n", "namespace": "d", "uid": "u"}, "spec": {"replicas": 0}},
        TENANT,
    )

    assert snapshot.attributes == {"replicas": 0}


# ---------------------------------------------------------------------------
# fetch: unreadable payloads are unavailability, not junk
# ---------------------------------------------------------------------------


def test_fetch_on_a_missing_file_is_provider_unavailable(tmp_path):
    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(tmp_path / "absent.json"), ENVELOPE_KEY).read()


def test_fetch_on_invalid_json_is_provider_unavailable(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(path), ENVELOPE_KEY).read()


def test_fetch_on_a_payload_with_no_items_list_is_provider_unavailable(tmp_path):
    """An envelope with no items is not an empty estate: there is no record to
    reject, so nothing was observed."""
    path = tmp_path / "no-items.json"
    path.write_text(json.dumps({"kind": "DeploymentList"}), encoding="utf-8")

    with pytest.raises(ProviderUnavailable):
        RecordedSource(str(path), ENVELOPE_KEY).read()


def test_fetch_does_not_judge_records():
    """The structural guarantee against CF-1.

    An adapter never sees the collection it would have to abort, so a payload
    full of junk still fetches cleanly and fails one record at a time.
    """
    records = from_recording(MULTI_FIXTURE).fetch()

    assert len(records) == 3
