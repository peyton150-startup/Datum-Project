"""Branches in comparison.py that no Phase 2B-2E test reached.

The kernel gate is 100% branch coverage on datum/reconcile. comparison.py sat
at 95%, and the uncovered branches fall into two kinds.

**Real behavior nobody asserted.** Timestamp offset conversion, and the
element-mismatch exit inside multiset and set comparison. These are ordinary
tests; the code paths run in normal operation and simply had no case.

**Defensive fallbacks the boundary makes unreachable.** Each comparison
function ends in an `else` for a mode it does not know, and `FieldConfig`
rejects every such mode at construction. They are asserted anyway, for the
reason the mode tables exist: if the barricade is ever bypassed, the failure
these guard against is silent agreement on every field of that type, which is
the worst direction for a kernel to fail in. Reaching them means writing past
the constructor, which is what these tests do deliberately.
"""

from datum.reconcile.comparison import (
    compare_list,
    compare_numeric,
    compare_object,
    compare_string,
    compare_timestamp,
)
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import VALID_FIELD_TYPES, FieldConfig


def past_validation(config: FieldConfig, mode: str) -> FieldConfig:
    """Set a mode the constructor would have rejected.

    FieldConfig validates on construction and holds the comparison dict by
    reference, so this reaches the state the schema layer exists to prevent.
    """
    config.comparison["mode"] = mode
    return config


# --- Real behavior that had no test ------------------------------------------


class TestTimestampOffsetConversion:
    """A timestamp that carries an offset is converted, not just stamped UTC."""

    def config(self):
        return FieldConfig(
            "created_at",
            "timestamp",
            {"mode": "semantic_utc", "precision": "second"},
            "discrepancy",
        )

    def test_two_offsets_naming_the_same_instant_match(self):
        """12:00+02:00 and 10:00Z are one instant written two ways."""
        declared = PlaneValue.of("2026-07-30T12:00:00+02:00")
        discovered = PlaneValue.of("2026-07-30T10:00:00+00:00")
        assert compare_timestamp(declared, discovered, self.config())[0] is True

    def test_two_offsets_naming_different_instants_are_a_discrepancy(self):
        """The nearby case that must not trigger: same wall clock, not same instant."""
        declared = PlaneValue.of("2026-07-30T12:00:00+02:00")
        discovered = PlaneValue.of("2026-07-30T12:00:00+00:00")
        assert compare_timestamp(declared, discovered, self.config())[0] is False

    def test_an_offset_side_against_a_naive_side(self):
        """A naive timestamp is read as UTC, so +02:00 noon is not naive noon."""
        declared = PlaneValue.of("2026-07-30T12:00:00+02:00")
        discovered = PlaneValue.of("2026-07-30T12:00:00")
        assert compare_timestamp(declared, discovered, self.config())[0] is False

    def test_a_naive_declared_side_is_read_as_utc(self):
        """The declared side's naive branch, which no Phase 2E test reached."""
        declared = PlaneValue.of("2026-07-30T10:00:00")
        discovered = PlaneValue.of("2026-07-30T10:00:00+00:00")
        assert compare_timestamp(declared, discovered, self.config())[0] is True

    def test_two_naive_sides_are_compared_as_written(self):
        declared = PlaneValue.of("2026-07-30T10:00:00")
        discovered = PlaneValue.of("2026-07-30T11:00:00")
        assert compare_timestamp(declared, discovered, self.config())[0] is False


class TestListElementMismatch:
    """Same length, different contents: the exit inside the element walk."""

    def multiset_config(self):
        return FieldConfig(
            "tags",
            "list",
            {"mode": "unordered_multiset", "element_comparison": {"mode": "exact_value"}},
            "discrepancy",
        )

    def set_config(self):
        return FieldConfig(
            "tags",
            "list",
            {"mode": "set", "element_comparison": {"mode": "exact_value"}},
            "discrepancy",
        )

    def test_multiset_of_equal_length_with_one_element_differing(self):
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of([1, 2, 4])
        assert compare_list(declared, discovered, self.multiset_config())[0] is False

    def test_multiset_with_the_same_elements_in_another_order_matches(self):
        """The nearby case: only the contents may decide, never the order."""
        declared = PlaneValue.of([3, 1, 2])
        discovered = PlaneValue.of([2, 3, 1])
        assert compare_list(declared, discovered, self.multiset_config())[0] is True

    def test_set_of_equal_size_with_one_element_differing(self):
        declared = PlaneValue.of([1, 2])
        discovered = PlaneValue.of([1, 3])
        assert compare_list(declared, discovered, self.set_config())[0] is False

    def test_set_ignores_duplicates_on_the_way_to_the_same_size(self):
        """The nearby case: [1, 1, 2] and [2, 1] are the same set."""
        declared = PlaneValue.of([1, 1, 2])
        discovered = PlaneValue.of([2, 1])
        assert compare_list(declared, discovered, self.set_config())[0] is True

    def test_sets_of_different_size_short_circuit_before_the_element_walk(self):
        """Deduplication must not hide a genuinely extra element."""
        declared = PlaneValue.of([1, 2])
        discovered = PlaneValue.of([1, 2, 3])
        assert compare_list(declared, discovered, self.set_config())[0] is False

    def test_duplicates_alone_do_not_change_the_size(self):
        """The nearby case: [1, 1] and [1] differ in length but not as sets."""
        declared = PlaneValue.of([1, 1])
        discovered = PlaneValue.of([1])
        assert compare_list(declared, discovered, self.set_config())[0] is True


# --- Defensive fallbacks the schema layer makes unreachable ------------------


class TestAnUnknownModeIsNeverAMatch:
    """Every type must report a discrepancy for a mode it cannot execute.

    Asserted with identical values on both sides, so nothing but the mode
    handling can produce the answer. A fallback that defaulted to equality
    would pass a naive test using differing values.
    """

    def test_numeric(self):
        config = past_validation(
            FieldConfig("n", "numeric", {"mode": "exact_value"}, "discrepancy"), "roughly"
        )
        is_equal, log = compare_numeric(PlaneValue.of(3), PlaneValue.of(3), config)
        assert is_equal is False
        assert any("Unknown mode: roughly" in step for step in log.steps)

    def test_string(self):
        config = past_validation(
            FieldConfig("s", "string", {"mode": "exact"}, "discrepancy"), "soundex"
        )
        is_equal, log = compare_string(PlaneValue.of("a"), PlaneValue.of("a"), config)
        assert is_equal is False
        assert any("Unknown mode: soundex" in step for step in log.steps)

    def test_list(self):
        config = past_validation(
            FieldConfig(
                "l",
                "list",
                {"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
                "discrepancy",
            ),
            "sorted",
        )
        is_equal, log = compare_list(PlaneValue.of([1]), PlaneValue.of([1]), config)
        assert is_equal is False
        assert any("Unknown mode: sorted" in step for step in log.steps)

    def test_timestamp(self):
        config = past_validation(
            FieldConfig("t", "timestamp", {"mode": "string"}, "discrepancy"), "fuzzy"
        )
        is_equal, log = compare_timestamp(
            PlaneValue.of("2026-07-30"), PlaneValue.of("2026-07-30"), config
        )
        assert is_equal is False
        assert any("Unknown mode: fuzzy" in step for step in log.steps)

    def test_object(self):
        config = past_validation(
            FieldConfig("o", "object", {"mode": "opaque"}, "discrepancy"), "sortof"
        )
        is_equal, log = compare_object(PlaneValue.of({"a": 1}), PlaneValue.of({"a": 1}), config)
        assert is_equal is False
        assert any("Unknown mode: sortof" in step for step in log.steps)


class TestEveryValidFieldTypeHasAValidator:
    """The guard that replaces the if-chain's fall-through branch.

    `_validate_comparison_config` now looks its validator up by field type
    rather than walking an if/elif chain, which means an accepted type with no
    row in that table is a KeyError from inside the constructor instead of a
    silently unvalidated field. The chain's `else` used to absorb that; nothing
    absorbs it now, so this test is what holds the two in agreement.
    """

    def test_each_valid_type_can_be_constructed(self):
        """Whitelist, not blacklist: every accepted type is exercised.

        Asserting the set has a validator by iterating VALID_FIELD_TYPES
        catches a type added to the set and forgotten in the table, which is
        the direction the mistake actually goes.
        """
        minimal_config = {
            "list": {"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            "numeric": {"mode": "exact_value"},
            "string": {"mode": "exact"},
            "timestamp": {"mode": "string"},
            "object": {"mode": "opaque"},
        }
        assert set(minimal_config) == VALID_FIELD_TYPES, (
            "a field type was added without a case here; the validator table "
            "in schema.py needs a row for it too"
        )
        for field_type, comparison in minimal_config.items():
            config = FieldConfig("f", field_type, comparison, "discrepancy")
            assert config.field_type == field_type
