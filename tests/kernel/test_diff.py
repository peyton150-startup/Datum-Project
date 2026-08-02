from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datum.enums import DiscrepancyType
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import PlaneValue, ResourceSnapshot, canonical
from datum.reconcile.matcher import match_resources

T = "t1"


def snap(name, replicas, scope="default", provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_single_field_discrepancy_replicas_3_vs_5():
    result = match_resources([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    diff = reconcile(result)
    assert len(diff.field_discrepancies) == 1
    fd = diff.field_discrepancies[0]
    assert fd.field_name == "replicas"
    assert fd.declared == PlaneValue.of(3)
    assert fd.discovered == PlaneValue.of(5)
    assert not diff.orphans


def test_identical_attributes_produce_no_discrepancy():
    result = match_resources([snap("web", 3)], [snap("web", 3, provider_id="u1")])
    diff = reconcile(result)
    assert not diff.field_discrepancies and not diff.orphans


def test_declared_orphan_is_declared_missing():
    result = match_resources([snap("web", 3)], [])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DECLARED_MISSING.value


def test_discovered_orphan_is_discovered_undeclared():
    result = match_resources([], [snap("ghost", 2, provider_id="u9")])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DISCOVERED_UNDECLARED.value


def test_absent_key_on_one_side_is_a_discrepancy():
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": 3, "paused": True})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {"replicas": 3})
    diff = reconcile(match_resources([d], [x]))
    fields = {fd.field_name for fd in diff.field_discrepancies}
    assert fields == {"paused"}


def test_nothing_on_either_plane_is_an_empty_discrepancy_set():
    diff = reconcile(match_resources([], []))
    assert not diff.field_discrepancies and not diff.orphans


def test_every_field_differing_is_reported_not_just_the_first():
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": 3, "paused": False})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {"replicas": 5, "paused": True})
    diff = reconcile(match_resources([d], [x]))
    reported = [(fd.field_name, fd.declared, fd.discovered) for fd in diff.field_discrepancies]
    assert reported == [
        ("paused", PlaneValue.of(False), PlaneValue.of(True)),
        ("replicas", PlaneValue.of(3), PlaneValue.of(5)),
    ]  # sorted by field name


def test_pair_sharing_no_attribute_keys_reports_every_key_from_both_sides():
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": 3})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {"paused": True})
    diff = reconcile(match_resources([d], [x]))
    assert [fd.field_name for fd in diff.field_discrepancies] == ["paused", "replicas"]
    assert [(fd.declared, fd.discovered) for fd in diff.field_discrepancies] == [
        (PlaneValue.absent(), PlaneValue.of(True)),  # paused: absent on declared
        (PlaneValue.of(3), PlaneValue.absent()),  # replicas: absent on discovered
    ]


def test_explicit_null_differs_from_a_missing_key_in_the_comparison():
    """Absent and null are compared distinctly, and now reported distinctly too."""
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": None})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {})
    diff = reconcile(match_resources([d], [x]))
    assert len(diff.field_discrepancies) == 1
    fd = diff.field_discrepancies[0]
    assert fd.field_name == "replicas"
    assert fd.declared == PlaneValue.of(None)
    assert fd.discovered == PlaneValue.absent()


def test_rerun_on_same_input_is_identical():
    result = match_resources([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    assert reconcile(result) == reconcile(result)


# Generation speed is not a property of these strategies, so it must not be able
# to fail them. Both draw at most six pairs from three names and a handful of
# values -- a bounded, tiny input space with no interleaved computation -- yet
# `HealthCheck.too_slow` measures wall clock, and a full-suite run alongside a
# containerised Postgres stalls one draw in a few hundred. Observed once as a
# 7.2-second draw among twelve that took 1-3ms (issue #38).
#
# Suppressed rather than made faster because there is nothing to make faster,
# and a false red on a *determinism* test is expensive: the honest reading of
# one is that the kernel is broken. `deadline` is deliberately left armed --
# it measures the property's own execution, which is worth knowing about.
GENERATION_SPEED_IS_NOT_THE_PROPERTY = settings(suppress_health_check=[HealthCheck.too_slow])


@GENERATION_SPEED_IS_NOT_THE_PROPERTY
@given(
    st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.integers(0, 9)), max_size=6),
    st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.integers(0, 9)), max_size=6),
)
def test_determinism_invariant_input_order_does_not_matter(decl, disc):
    def build(pairs):
        # dedupe by name; last wins
        by_name = {n: r for n, r in pairs}
        return [snap(n, r) for n, r in by_name.items()]

    # Build once, then permute the built snapshots. Reversing the raw tuples
    # instead would change which value survives "last wins", feeding the engine
    # genuinely different inputs rather than the same input in another order.
    declared = build(decl)
    discovered = build(disc)
    forward = reconcile(match_resources(declared, discovered))
    reversed_ = reconcile(match_resources(declared[::-1], discovered[::-1]))
    assert forward == reversed_


# A WBS 1.5.0 deliverable: the D5 invariant, exercised over presence rather
# than only over values. Extends this generator rather than adding a second,
# weaker same-process double call elsewhere.

ATTRIBUTE_NAMES = ["a", "b", "c"]
# int, None, and the absence marker. None is an explicit null; ABSENT means the
# key is not written at all, which is the distinction under test.
ABSENT_MARKER = object()
ATTRIBUTE_VALUES = st.one_of(
    st.integers(0, 9),
    st.none(),
    st.just(ABSENT_MARKER),
    st.booleans(),
)


def build_snapshot(name, attribute_pairs, provider_id=None):
    attributes = {
        key: value for key, value in dict(attribute_pairs).items() if value is not ABSENT_MARKER
    }
    return ResourceSnapshot("Deployment", T, "default", name, provider_id, attributes)


@GENERATION_SPEED_IS_NOT_THE_PROPERTY
@given(
    st.lists(st.tuples(st.sampled_from(ATTRIBUTE_NAMES), ATTRIBUTE_VALUES), max_size=6),
    st.lists(st.tuples(st.sampled_from(ATTRIBUTE_NAMES), ATTRIBUTE_VALUES), max_size=6),
)
def test_determinism_holds_over_absent_and_null_attributes(declared_attrs, discovered_attrs):
    """Identical input in a different key order produces an identical result.

    Attribute insertion order is permuted rather than the snapshot list order,
    because presence lives in the attribute mapping: `_field_discrepancies`
    builds a set union of both sides' keys before sorting, and a set's
    iteration order is where an unsorted implementation would leak.
    """
    declared = build_snapshot("web", declared_attrs)
    discovered = build_snapshot("web", discovered_attrs, provider_id="u1")
    shuffled_declared = build_snapshot("web", list(reversed(declared_attrs)))
    shuffled_discovered = build_snapshot("web", list(reversed(discovered_attrs)), provider_id="u1")

    forward = reconcile(match_resources([declared], [discovered]))
    permuted = reconcile(match_resources([shuffled_declared], [shuffled_discovered]))

    # Same content either way: reversing the pairs changes which duplicate wins
    # under dict "last wins", so only compare when both sides built the same map.
    #
    # Compared canonically, not with `==`. Hypothesis found the reason: for
    # [("a", 0), ("a", False)], last-wins yields {"a": False} forwards and
    # {"a": 0} reversed, and `{"a": 0} == {"a": False}` is True in Python. The
    # guard would have admitted two genuinely different inputs and blamed the
    # engine for the difference. This is the same 0-versus-False conflation
    # PlaneValue defines its own equality to avoid, met here in the test that
    # checks it.
    if canonical(declared.attributes) == canonical(shuffled_declared.attributes) and (
        canonical(discovered.attributes) == canonical(shuffled_discovered.attributes)
    ):
        assert forward == permuted
        assert [fd.field_name for fd in forward.field_discrepancies] == sorted(
            fd.field_name for fd in forward.field_discrepancies
        )
