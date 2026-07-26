from hypothesis import given
from hypothesis import strategies as st

from datum.enums import DiscrepancyType
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key

T = "t1"


def snap(name, replicas, scope="default", provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_single_field_discrepancy_replicas_3_vs_5():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    diff = reconcile(result)
    assert len(diff.field_discrepancies) == 1
    fd = diff.field_discrepancies[0]
    assert fd.field_name == "replicas"
    assert fd.declared_value == 3
    assert fd.discovered_value == 5
    assert not diff.orphans


def test_identical_attributes_produce_no_discrepancy():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 3, provider_id="u1")])
    diff = reconcile(result)
    assert not diff.field_discrepancies and not diff.orphans


def test_declared_orphan_is_declared_missing():
    result = match_by_natural_key([snap("web", 3)], [])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DECLARED_MISSING.value


def test_discovered_orphan_is_discovered_undeclared():
    result = match_by_natural_key([], [snap("ghost", 2, provider_id="u9")])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DISCOVERED_UNDECLARED.value


def test_absent_key_on_one_side_is_a_discrepancy():
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": 3, "paused": True})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {"replicas": 3})
    diff = reconcile(match_by_natural_key([d], [x]))
    fields = {fd.field_name for fd in diff.field_discrepancies}
    assert fields == {"paused"}


def test_rerun_on_same_input_is_identical():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    assert reconcile(result) == reconcile(result)


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
    forward = reconcile(match_by_natural_key(declared, discovered))
    reversed_ = reconcile(match_by_natural_key(declared[::-1], discovered[::-1]))
    assert forward == reversed_
