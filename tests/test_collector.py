"""Collector framework tests: DESIGN section 11.

The CF-1 cases are the reason this phase exists, so they are stated as the plan
reproduced them -- three records, the middle one broken -- rather than as a tidy
pair. The counts are as much the subject as the rows: `resources_read`
under-reporting what a run saw is the silent half of that defect.
"""

import datetime
import json
from unittest import mock

import pytest
from django.db import DataError, InterfaceError

from datum.discovery.collector import _refuse_unstorable_attributes, run_collector
from datum.discovery.errors import MalformedProviderData, ProviderUnavailable
from datum.discovery.kubernetes import ENVELOPE_KEY, from_recording
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.discovery.recorded import RecordedSource
from datum.enums import CollectorRunStatus
from datum.reconcile.domain import ResourceSnapshot, _inspected
from datum.reconcile.domain import unstorable_attribute as _unstorable_attribute

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
        # Issue #56: a key's contents, not just its type. These reached Postgres
        # and came back as driver text, because the type question was asked
        # where keys are enumerated and the contents question in the walk, which
        # never visits a key.
        ({"labels": {"a" + chr(0) + "b": "safe"}}, "a NUL inside a nested key"),
        ({"labels": {"a" + chr(0xD800) + "b": "safe"}}, "a surrogate inside a nested key"),
        ({"a" + chr(0) + "b": "safe"}, "a NUL inside a top-level attribute name"),
        ({"a" + chr(0xD800) + "b": "safe"}, "a surrogate inside an attribute name"),
        ({1: "a"}, "an int attribute name, which nothing was checking"),
        ({None: "a"}, "a None attribute name, which nothing was checking"),
        ({"note": "a\x00b"}, "a NUL, which Postgres cannot store in text"),
        ({"note": "\ud800"}, "an unpaired surrogate, which is not valid UTF-8"),
        ({"labels": {"k": "pre\x00post"}}, "a NUL nested inside a mapping value"),
        ({"args": ["fine", "\udfff"]}, "a surrogate nested inside a list"),
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

    The two mapping-key type cases never raised at all -- they were stored,
    silently coerced to `{"1": "a"}` and `{"true": "a"}`. Those are the ones a
    test that only looked for exceptions would have missed.

    **The four key-*contents* cases are true here and prove nothing here.** With
    the key check removed the record still reaches Postgres, Postgres still
    refuses it, and the counts are still `(1, 0, 1)`. What changes is who
    decided and what the operator is told, which this assertion cannot see.
    `test_a_bad_key_is_caught_by_the_barricade_and_not_by_postgres` is where
    that is pinned; they are listed here for the end-to-end claim only.

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
        # A tuple is JSON-safe: `json.dumps` emits it as an array, storage keeps
        # it as one, and it canonicalizes identically to the list. Rejecting it
        # dropped a healthy resource for no correctness gain, which is the
        # over-broad direction and the worse one.
        {"ports": (80, 443)},
        {"spec": {"args": ("--v", "2")}},
        # Strings whose contents are unusual but storable. The NUL and surrogate
        # rules must not become "reject anything non-ASCII".
        {"note": "emoji \U0001f600 and éè and 中文"},
        {"note": 'tab\tnewline\nquote"backslash\\'},
        {"note": "😀"},  # a *paired* surrogate: one valid astral char
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

    # Compared against the JSON round-trip rather than the input, because
    # storage normalizes and one of these cases proves it: a tuple is stored as
    # an array and read back as a list. Asserting equality with the input would
    # have quietly required tuples to be rejected, which is the thing this case
    # exists to say they are not.
    stored = DiscoveredResource.objects.get(tenant_id=TENANT, name="one").attributes
    assert stored == json.loads(json.dumps(attributes))


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
                attributes={"spec": {"ports": [{"seen": "a" + chr(0) + "b"}]}},
            )

    with pytest.raises(MalformedProviderData) as caught:
        _refuse_unstorable_attributes(Nested(), Nested().normalize(1, TENANT))

    message = str(caught.value)
    assert "kubernetes" in message
    assert "Deployment/prod/api" in message
    assert "spec.ports[0].seen" in message
    assert "NUL" in message


def test_a_write_the_database_refuses_is_counted_not_raised():
    """The last mile: a value the type barricade did not anticipate.

    The barricade is a list of types, and no such list can be proven to
    anticipate everything Postgres declines. Before this, an unanticipated
    value raised out of `run_collector`: the run was left at its provisional
    FAILED with no `finished_at`, and every record after it was never read,
    counted, or stored -- CF-1's shape, through the one door still open.

    Simulated by making the *write* fail for one record while the barricade
    passes it, which is exactly the situation an incomplete type table
    produces. The point is that the run survives whether or not the table is
    complete, so this deliberately does not go through a known-bad type.
    """
    from datum.discovery import collector as collector_module

    real_upsert = collector_module._upsert

    def refusing_upsert(tenant_id, kind, snapshot, run):
        if snapshot.name == "poison":
            raise DataError("simulated: the database refused this row")
        return real_upsert(tenant_id, kind, snapshot, run)

    class Batch:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return ["before", "poison", "after"]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record,
                provider_id=f"uid-{record}",
                attributes={"replicas": 1},
            )

    with mock.patch.object(collector_module, "_upsert", refusing_upsert):
        run = run_collector(Batch(), TENANT)

    # The record after the poisoned one is the whole point: it must be read,
    # written, and present. Before the fix the run died at "poison".
    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)
    assert run.status == CollectorRunStatus.PARTIAL
    assert run.finished_at is not None
    assert names_written() == {"before", "after"}


def test_among_equally_shallow_problems_the_first_written_is_named():
    """A LIFO stack named the last-inserted attribute while claiming the first."""
    problem = _unstorable_attribute({"aaa_first": "x" + chr(0), "zzz_last": {1: "x"}})

    assert problem is not None
    assert "aaa_first" in problem
    assert "zzz_last" not in problem


def test_a_shallower_problem_outranks_an_earlier_deeper_one():
    """The case that tells breadth-first apart from insertion order.

    The test above cannot: both its problems sit at the same depth, so it passes
    under either rule and the docstring's "first in insertion order" borrowed
    credit from it. Here `a` is written first and its problem is nested, while
    `b` is written second and its problem is at the top level -- so the two
    rules give different answers and only one of them is what the code does.

    Asserted rather than corrected because shallowest-first is the better
    behaviour to report: it names the attribute an operator can see.
    """
    problem = _unstorable_attribute({"a": {"nested": "x" + chr(0)}, "b": "y" + chr(0)})

    assert problem is not None
    assert problem.startswith("b contains a NUL")


@pytest.mark.parametrize(
    ("label", "attributes"),
    [
        ("5000 levels deep", None),  # built below; json.dumps recurses
        ("an integer of 5001 digits", {"n": 10**5000}),
        ("a self-referential mapping", None),  # built below
    ],
)
def test_a_payload_the_encoder_refuses_is_rejected_before_the_write(label, attributes):
    """Three the type walk passed and `json.dumps` does not.

    All three escaped every guard: the walk described what JSON accepts rather
    than asking it, and psycopg serializes the JSONB parameter **client-side**,
    so `RecursionError` and `ValueError` were raised before any SQL was sent and
    were not `django.db.Error` either. They left the run at its provisional
    FAILED and took every later record with them.

    The deep case is the sharp one: an earlier test asserted 5000 levels was
    safe, and it was -- of the walk, which was made iterative for exactly that
    reason. That made the walk the one component in the chain that survived
    input the next component could not.
    """
    if attributes is None and label.startswith("5000"):
        deep: object = "leaf"
        for _ in range(5000):
            deep = {"k": [deep]}
        attributes = {"nest": deep}
    elif attributes is None:
        cyclic: dict = {}
        cyclic["self"] = cyclic
        attributes = {"loop": cyclic}

    run = emitting(attributes)

    assert (run.resources_read, run.resources_written, run.errors) == (1, 0, 1)
    assert run.status == CollectorRunStatus.PARTIAL
    assert run.finished_at is not None
    assert names_written() == set()


def test_an_encoder_refusal_does_not_take_the_rest_of_the_batch():
    """The consequence, which is the part that mattered.

    Counting the bad record is not the point; the records after it are. Before
    this, the exception propagated out of `run_collector` and everything later
    in iteration order was never read, counted, or stored.
    """
    deep: object = "leaf"
    for _ in range(5000):
        deep = {"k": [deep]}

    class Batch:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return ["before", "poison", "after"]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record,
                provider_id=f"uid-{record}",
                attributes={"nest": deep} if record == "poison" else {"replicas": 1},
            )

    run = run_collector(Batch(), TENANT)

    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)
    assert names_written() == {"before", "after"}


def batch_whose_write_raises(error: BaseException, poisoned: str = "poison"):
    """Run a three-record batch where one record's write raises `error`."""
    from datum.discovery import collector as collector_module

    real_upsert = collector_module._upsert

    def failing(tenant_id, kind, snapshot, run):
        if snapshot.name == poisoned:
            raise error
        return real_upsert(tenant_id, kind, snapshot, run)

    class Batch:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return ["before", poisoned, "after"]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record,
                provider_id=f"uid-{record}",
                attributes={"replicas": 1},
            )

    with mock.patch.object(collector_module, "_upsert", failing):
        return run_collector(Batch(), TENANT)


def test_an_assertion_is_not_swallowed_by_the_blanket_catch():
    """Issue #57. `AssertionError` is an `Exception`, so `except Exception` ate it.

    ADR-008 makes an assertion the mechanism for a condition the code believes
    impossible, which means it is the one failure here that is *supposed* to end
    the run. Turning it into a counted per-record rejection is precisely the
    outcome it exists to prevent, and it would be invisible: the run would look
    like an ordinary PARTIAL over slightly bad provider data.

    The broad catch is for failures nobody predicted. An assertion is a failure
    someone predicted and declared fatal.
    """
    with pytest.raises(AssertionError, match="impossible"):
        batch_whose_write_raises(AssertionError("impossible condition reached"))


def test_a_connection_level_failure_is_also_survived():
    """`InterfaceError` is a sibling of `DatabaseError`, not a child.

    The catch was narrowed twice on reasoning about which failures were worth
    surviving, and was wrong both times in a direction nobody predicted. This
    pins the sibling case the narrower guess missed, so a future narrowing
    fails here rather than in production.
    """
    from datum.discovery import collector as collector_module

    real_upsert = collector_module._upsert

    def failing(tenant_id, kind, snapshot, run):
        if snapshot.name == "poison":
            raise InterfaceError("simulated: the connection is gone")
        return real_upsert(tenant_id, kind, snapshot, run)

    class Batch:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return ["before", "poison", "after"]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind=self.kind,
                tenant_id=tenant_id,
                scope="default",
                name=record,
                provider_id=f"uid-{record}",
                attributes={"replicas": 1},
            )

    with mock.patch.object(collector_module, "_upsert", failing):
        run = run_collector(Batch(), TENANT)

    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)
    assert names_written() == {"before", "after"}


@pytest.mark.parametrize(
    "attributes",
    [
        {"labels": {"a" + chr(0) + "b": "safe"}},
        {"labels": {"a" + chr(0xD800) + "b": "safe"}},
        {"a" + chr(0) + "b": "safe"},
        {"a" + chr(0xD800) + "b": "safe"},
    ],
)
def test_a_bad_key_is_caught_by_the_barricade_and_not_by_postgres(attributes):
    """The run-level cases for keys do not discriminate, and this does.

    With the key check removed, those cases still pass: the record reaches
    Postgres, Postgres refuses it, `_stored` counts the rejection, and the run
    is still `(1, 0, 1)` PARTIAL. The *outcome* is identical either way — what
    differs is who decided and what the operator is told, which is the whole
    content of issue #56.

    So the run-level assertions are true and prove nothing here, and this one
    is where the fix is actually pinned. Confirmed by reverting the key check
    and watching only this test and the message test fail.
    """
    assert _unstorable_attribute(attributes) is not None


def test_a_key_is_named_and_placed_the_way_a_value_is():
    """Issue #56: the message has to be actionable for a key too.

    A key is not addressable by the path that would name its value, so the two
    report differently on purpose. Both must still say which mapping and which
    key, or the operator is back to grepping the driver's text.
    """
    nested = _unstorable_attribute({"labels": {"a" + chr(0) + "b": "safe"}})
    assert nested is not None
    assert "labels" in nested
    assert "NUL" in nested

    top = _unstorable_attribute({"a" + chr(0) + "b": "safe"})
    assert top is not None
    assert "attribute name" in top
    assert "NUL" in top


@pytest.mark.parametrize(
    "attributes",
    [
        {"labels": {"app": "api"}},
        {"labels": {"": "an empty key is a key"}},
        {"labels": {"emoji-\U0001f600-key": "v"}},
        {"labels": {"tab\tkey": "v"}},
        {"a-é中-name": "v"},
    ],
)
def test_ordinary_keys_are_untouched(attributes):
    """The guard reads key contents, so it must not reject ordinary contents.

    An empty key and a key carrying non-ASCII are both storable. Without these,
    a guard that rejected any key it had to look inside would pass every case
    above and look correct.
    """
    assert _unstorable_attribute(attributes) is None


def test_the_walks_own_unknown_type_branch_still_answers():
    """A defensive branch the encoder trial now makes unreachable in normal use.

    `_inspected` ends in a case for a type it does not recognise. Nothing can
    reach it through `unstorable_attribute` any more, because `json.dumps` is
    asked first and refuses every non-JSON type before the walk starts. Asserted
    directly, the way this repository already reaches the comparison functions
    defensive mode branches: the failure it guards against is a value silently
    walking past a boundary, which is the worst direction for this code to fail
    in, and an untested branch is a branch nobody has checked answers at all.
    """
    problem, children = _inspected("created", datetime.date(2026, 8, 3))

    assert problem is not None
    assert "created" in problem
    assert "date" in problem
    assert children == []


def test_a_deep_payload_is_refused_rather_than_walked_forever_or_crashed():
    """This test used to assert the opposite, and that was the defect.

    It asserted 5000 levels returned None -- true of the walk, which is
    iterative, and false of `json.dumps`, which recurses. So the walk was the
    one component in the chain that survived input the next component could not,
    and the test certified exactly that gap as safe.

    The iterative walk is still right and still here: what changed is that the
    encoder is asked first, so a structure it cannot serialize is refused before
    the walk ever sees it. Asserted at both 5000 (past the recursion limit) and
    50 (comfortably under it) so the guard cannot become "refuse anything
    nested".
    """
    deep: object = "leaf"
    for _ in range(5000):
        deep = {"k": [deep]}
    assert "encoder refused" in (_unstorable_attribute({"nest": deep}) or "")

    shallow: object = "leaf"
    for _ in range(50):
        shallow = {"k": [shallow]}
    assert _unstorable_attribute({"nest": shallow}) is None


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
