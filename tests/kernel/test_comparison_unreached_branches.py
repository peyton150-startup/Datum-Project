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

import pytest

from datum.reconcile.comparison import (
    compare_list,
    compare_numeric,
    compare_object,
    compare_string,
    compare_timestamp,
)
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import VALID_FIELD_TYPES, FieldConfig, InvalidModeParameter


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


class TestAModeParameterThatStatesNothingUsableIsNeverAMatch:
    """A recognised mode carrying an unusable parameter degrades, not crashes.

    The sibling of the unknown-mode class above, and the case it did not cover:
    there the *name* was unrecognised, here the name is recognised and the
    parameter is not. Both are "the configured comparison cannot run", and both
    must answer the same way.

    Asserted with identical values on both sides, so nothing but the parameter
    handling can produce the answer -- a fallback that defaulted to equality
    would pass a naive test using differing values. Before this, each of these
    raised ValueError out of the kernel (issue #33).
    """

    def numeric(self, mode: str) -> FieldConfig:
        return past_validation(
            FieldConfig("n", "numeric", {"mode": "exact_value"}, "discrepancy"), mode
        )

    def object_(self, mode: str) -> FieldConfig:
        return past_validation(FieldConfig("o", "object", {"mode": "opaque"}, "discrepancy"), mode)

    @pytest.mark.parametrize(
        "mode",
        [
            "tolerance(x)",  # not a number
            "tolerance()",  # no parameter at all
            "tolerance(5",  # unclosed
            "tolerance(-1)",  # parses, but no pair of values can satisfy it
        ],
    )
    def test_numeric_tolerance(self, mode):
        is_equal, log = compare_numeric(PlaneValue.of(3), PlaneValue.of(3), self.numeric(mode))
        assert is_equal is False
        assert any(f"Unusable mode parameter: {mode}" in step for step in log.steps)

    @pytest.mark.parametrize(
        "mode",
        [
            "recurse(x)",  # not a number
            "recurse()",  # no parameter at all
            "recurse(2",  # unclosed
            "recurse(-2)",  # below full recursion, which is the floor
            "recurse(1.5)",  # a depth has to be whole
        ],
    )
    def test_object_recurse(self, mode):
        is_equal, log = compare_object(
            PlaneValue.of({"a": 1}), PlaneValue.of({"a": 1}), self.object_(mode)
        )
        assert is_equal is False
        assert any(f"Unusable mode parameter: {mode}" in step for step in log.steps)

    def test_a_valid_parameter_still_runs_its_comparison(self):
        """The guard must reject only what it was written to reject.

        Placed here rather than with the ordinary mode tests because a guard
        that degraded everything would leave every test in this class passing.
        """
        equal_within, _ = compare_numeric(
            PlaneValue.of(10), PlaneValue.of(11), self.numeric("tolerance(2)")
        )
        equal_outside, _ = compare_numeric(
            PlaneValue.of(10), PlaneValue.of(20), self.numeric("tolerance(2)")
        )
        assert equal_within is True
        assert equal_outside is False

        # recurse(0) is opaque; the objects differ below the surface.
        drilled, _ = compare_object(
            PlaneValue.of({"a": {"b": 1}}),
            PlaneValue.of({"a": {"b": 2}}),
            self.object_("recurse(-1)"),
        )
        assert drilled is False

    @pytest.mark.parametrize("mode", ["tolerance(inf)", "tolerance(nan)"])
    def test_a_non_finite_tolerance_degrades_rather_than_matching(self, mode):
        """The half of issue #44 that is a wrong result rather than a nuisance.

        `tolerance(inf)` is the case: `abs(d - c) <= inf` is True for every pair
        of values, so before the fix this returned a *match* between two numbers
        three orders of magnitude apart, and every discrepancy on the field was
        suppressed with nothing in the audit log to distinguish it from a field
        that genuinely agreed.

        Values chosen far apart on purpose. Equal values would pass under the
        defect, since the answer would be True for the wrong reason.
        """
        is_equal, log = compare_numeric(PlaneValue.of(1), PlaneValue.of(1000), self.numeric(mode))
        assert is_equal is False
        assert any(f"Unusable mode parameter: {mode}" in step for step in log.steps)


def degrade_label(steps: list[str], mode: str) -> str:
    """The audit label one degrade path used, with the mode name stripped off."""
    written = [step for step in steps if step.endswith(f": {mode}")]
    assert len(written) == 1, f"expected exactly one degrade step for {mode}, got {steps}"
    return written[0].removesuffix(f": {mode}")


class TestBothDegradePathsReportOneConditionOneWay:
    """The numeric and object paths must not spell the same condition differently.

    They did. A malformed `recurse(...)` reported "Unknown mode", the same text
    an unrecognised mode name uses, while a malformed `tolerance(...)` reported
    "Unusable mode parameter". No verdict was wrong -- both degrade to False --
    but the audit log is what an operator greps to find misconfigured fields,
    and a search for either text silently missed every instance of the other.

    The second test is why this class is not satisfied by making both paths say
    "Unknown mode": that would be one encoding of one rule and would also erase
    a distinction worth keeping. Parity alone does not pin the fix; parity plus
    the distinction does.
    """

    def numeric(self, mode: str) -> FieldConfig:
        return past_validation(
            FieldConfig("n", "numeric", {"mode": "exact_value"}, "discrepancy"), mode
        )

    def object_(self, mode: str) -> FieldConfig:
        return past_validation(FieldConfig("o", "object", {"mode": "opaque"}, "discrepancy"), mode)

    def test_a_recognised_name_with_an_unusable_parameter_reads_alike(self):
        _, numeric_log = compare_numeric(
            PlaneValue.of(3), PlaneValue.of(3), self.numeric("tolerance(x)")
        )
        _, object_log = compare_object(
            PlaneValue.of({"a": 1}), PlaneValue.of({"a": 1}), self.object_("recurse(x)")
        )
        assert degrade_label(numeric_log.steps, "tolerance(x)") == degrade_label(
            object_log.steps, "recurse(x)"
        )

    def test_an_unrecognised_name_reads_alike_and_differently(self):
        _, numeric_log = compare_numeric(
            PlaneValue.of(3), PlaneValue.of(3), self.numeric("roughly")
        )
        _, object_log = compare_object(
            PlaneValue.of({"a": 1}), PlaneValue.of({"a": 1}), self.object_("sortof")
        )
        unknown_name = degrade_label(numeric_log.steps, "roughly")
        assert unknown_name == degrade_label(object_log.steps, "sortof")

        _, unusable_log = compare_numeric(
            PlaneValue.of(3), PlaneValue.of(3), self.numeric("tolerance(x)")
        )
        assert unknown_name != degrade_label(unusable_log.steps, "tolerance(x)")


class TestAnUnclosedModeIsNotSilentlyTruncated:
    """The parameter is read between the parentheses, not by dropping one char.

    `mode[len(prefix):-1]` drops the final character whatever it is, so an
    unclosed `tolerance(15` yielded `"15"[:-1]` -- a *valid* tolerance of 1.0.
    The barricade accepted it and the comparison ran with a parameter nobody
    wrote. That is worse than the crash issue #33 is named for, because it is
    silent, and it is why the closing parenthesis is checked rather than
    assumed.
    """

    def test_the_barricade_rejects_an_unclosed_tolerance(self):
        with pytest.raises(InvalidModeParameter):
            FieldConfig("n", "numeric", {"mode": "tolerance(15"}, "discrepancy")

    def test_the_barricade_rejects_an_unclosed_recurse(self):
        with pytest.raises(InvalidModeParameter):
            FieldConfig("o", "object", {"mode": "recurse(15"}, "discrepancy")

    def test_an_unclosed_tolerance_does_not_compare_as_one_point_zero(self):
        """10 against 11 is inside a tolerance of 1.0 and outside of nothing."""
        config = past_validation(
            FieldConfig("n", "numeric", {"mode": "exact_value"}, "discrepancy"), "tolerance(15"
        )
        is_equal, _ = compare_numeric(PlaneValue.of(10), PlaneValue.of(11), config)
        assert is_equal is False

    def test_the_whole_parameter_is_read_when_the_mode_is_closed(self):
        """The mirror: `tolerance(15)` really does mean 15, not 1."""
        config = FieldConfig("n", "numeric", {"mode": "tolerance(15)"}, "discrepancy")
        is_equal, _ = compare_numeric(PlaneValue.of(10), PlaneValue.of(20), config)
        assert is_equal is True


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
