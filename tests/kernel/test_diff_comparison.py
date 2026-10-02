"""Production diff decisions must come from configured comparison policy."""

import pytest

from datum.reconcile.audit import AuditLogWriter
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import PlaneValue, ResourceSnapshot
from datum.reconcile.matcher import match_resources
from datum.reconcile.schema import ComparisonSchema, SchemaError


def schema(kind="Deployment", mode="tolerance(2)", logging="discrepancy"):
    return ComparisonSchema(
        kind, {"replicas": {"type": "numeric", "comparison": {"mode": mode}, "logging": logging}}
    )


def pair(kind="Deployment", name="web", declared=3, discovered=5):
    return (
        ResourceSnapshot(kind, "t", "default", name, None, {"replicas": declared}),
        ResourceSnapshot(kind, "t", "default", name, f"{kind}/{name}", {"replicas": discovered}),
    )


def test_tolerance_decides_real_drift_instead_of_plane_equality():
    d, x = pair()
    result = reconcile(match_resources([d], [x]), {"Deployment": schema()})
    assert not result.field_discrepancies


def test_missing_policy_is_undecidable_even_when_values_agree():
    d, x = pair(discovered=3)
    result = reconcile(match_resources([d], [x]), {})
    (item,) = result.field_discrepancies
    assert item.discrepancy_type == "missing_comparison_policy"
    assert item.declared == item.discovered == PlaneValue.of(3)


def test_cross_kind_sampling_isolation_in_real_comparisons(caplog):
    declared, discovered = zip(
        pair("Deployment", "a"), pair("ComputeInstance", "a"), pair("Deployment", "b"), strict=False
    )
    schemas = {
        kind: schema(kind, logging="sampled_audit") for kind in ("Deployment", "ComputeInstance")
    }
    reconcile(match_resources(declared, discovered), schemas, AuditLogWriter(2))
    records = [r.message for r in caplog.records if r.name == "datum.reconcile.audit"]
    assert len(records) == 1
    assert "kind=Deployment" in records[0]


@pytest.mark.parametrize("kind", ["", None, 3])
def test_schema_rejects_invalid_kind_identity(kind):
    with pytest.raises(SchemaError):
        schema(kind)


def test_unknown_is_a_legitimate_kind_name():
    assert schema("unknown").get_field_config("replicas").kind_name == "unknown"


ABSENT = object()
TYPE_CASES = [
    ("numeric", {"mode": "exact_value"}, [ABSENT, None, 0, 1, 2]),
    ("string", {"mode": "exact"}, [ABSENT, None, "", "a", "b"]),
    ("list", {"mode": "ordered", "element_comparison": "exact"}, [ABSENT, None, [], [1], [2]]),
    ("timestamp", {"mode": "string"}, [ABSENT, None, "", "2026-01-01", "2026-01-02"]),
    ("object", {"mode": "opaque"}, [ABSENT, None, {}, {"a": 1}, {"a": 2}]),
    ("boolean", {"mode": "exact"}, [ABSENT, None, False, True, 1]),
]
CORPUS = [
    pytest.param(
        field_type,
        comparison,
        left,
        right,
        left_index == right_index and not (field_type == "boolean" and left_index == 4),
        id=f"{field_type}-{left_index}-{right_index}",
    )
    for field_type, comparison, values in TYPE_CASES
    for left_index, left in enumerate(values)
    for right_index, right in enumerate(values)
]


@pytest.mark.parametrize("field_type,comparison,left,right,agrees", CORPUS)
def test_production_presence_and_value_corpus(field_type, comparison, left, right, agrees):
    """150 dispatcher-to-diff cases; an invalid boolean never affirms agreement."""
    d = ResourceSnapshot("Corpus", "t", "s", "r", None, {} if left is ABSENT else {"f": left})
    x = ResourceSnapshot("Corpus", "t", "s", "r", "id", {} if right is ABSENT else {"f": right})
    configured = ComparisonSchema(
        "Corpus", {"f": {"type": field_type, "comparison": comparison, "logging": "debug"}}
    )
    result = reconcile(match_resources([d], [x]), {"Corpus": configured})
    assert bool(result.field_discrepancies) is not agrees
    if not agrees:
        (item,) = result.field_discrepancies
        assert item.discrepancy_type == "field"
        assert item.declared == (PlaneValue.absent() if left is ABSENT else PlaneValue.of(left))
        assert item.discovered == (PlaneValue.absent() if right is ABSENT else PlaneValue.of(right))


def test_missing_field_policy_keeps_neighbor_comparisons():
    d, x = pair()
    d.attributes["unconfigured"] = None
    x.attributes["unconfigured"] = None
    result = reconcile(match_resources([d], [x]), {"Deployment": schema(mode="exact_value")})
    assert [(item.field_name, item.discrepancy_type) for item in result.field_discrepancies] == [
        ("replicas", "field"),
        ("unconfigured", "missing_comparison_policy"),
    ]


def test_identity_mismatch_is_an_internal_bug():
    d, x = pair()
    with pytest.raises(AssertionError, match="schema identity"):
        reconcile(match_resources([d], [x]), {"Deployment": schema("OtherKind")})
    from datum.reconcile.domain import MatchedPair, MatchResult

    y, _ = pair("OtherKind")
    with pytest.raises(AssertionError, match="matched kinds"):
        reconcile(MatchResult((MatchedPair(d, y, "natural_key", "high"),), (), ()), {})
