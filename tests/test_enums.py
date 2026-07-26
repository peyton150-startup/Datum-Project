from pathlib import Path

from datum.enums import DiscrepancyState, DiscrepancyType, MatchStrategy


def test_discrepancy_states_are_open_and_resolved_only():
    assert {c.value for c in DiscrepancyState} == {"open", "resolved"}


def test_orphan_types_named_by_direction():
    assert DiscrepancyType.DECLARED_MISSING.value == "declared_missing"
    assert DiscrepancyType.DISCOVERED_UNDECLARED.value == "discovered_undeclared"


def test_generated_ts_enum_matches_python():
    ts = Path("web/src/enums.ts").read_text()
    for member in MatchStrategy:
        assert f'"{member.value}"' in ts
