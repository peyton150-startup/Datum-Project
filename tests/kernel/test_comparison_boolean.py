"""Tests for boolean field comparison (issue #53).

One mode, `exact`. The cases that carry weight are the ones where a value is
boolean-ish without being a boolean: the discovered plane has no type barricade,
so `1`, `"true"` and `0` all arrive here, and reading any of them as a boolean
would silence the drift this exists to report.
"""

from datum.reconcile.comparison import compare_boolean
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig

CONFIG = FieldConfig(
    field_name="enabled",
    field_type="boolean",
    comparison={"mode": "exact"},
    logging="discrepancy",
)


class TestBooleanExact:
    def test_true_matches_true(self):
        is_equal, log = compare_boolean(PlaneValue.of(True), PlaneValue.of(True), CONFIG)

        assert (is_equal, log.result) == (True, True)

    def test_false_matches_false(self):
        """The other identity. `False` is falsy, so a presence check written as a
        truthiness check would report this pair unstated rather than equal."""
        is_equal, log = compare_boolean(PlaneValue.of(False), PlaneValue.of(False), CONFIG)

        assert (is_equal, log.result) == (True, True)

    def test_true_does_not_match_false(self):
        is_equal, _ = compare_boolean(PlaneValue.of(True), PlaneValue.of(False), CONFIG)

        assert is_equal is False

    def test_the_audit_entry_names_the_type_and_leaves_the_values_untransformed(self):
        """There is no transformation, so the transformed values are the raw ones.

        Asserted because an audit entry that silently coerced its values would
        report a comparison the code did not make.
        """
        _, log = compare_boolean(PlaneValue.of(True), PlaneValue.of(False), CONFIG)

        assert log.field_type == "boolean"
        assert (log.declared_raw, log.declared_transformed) == (True, True)
        assert (log.discovered_raw, log.discovered_transformed) == (False, False)


class TestOnlyABooleanIsABoolean:
    """The bug excluded: `type(v) is bool` relaxed to `isinstance`.

    bool is a subclass of int in Python. Under isinstance every case below
    reports a match or a comparison rather than an unstated statement, so each
    fixture gives a different answer under the bug.
    """

    def test_a_discovered_one_is_not_true(self):
        """The case the declared barricade's own comment is about, on the other plane.

        A provider reporting `1` where a declaration says `true` is drift a
        reconciler exists to surface. Under `isinstance` this returns equal.
        """
        is_equal, log = compare_boolean(PlaneValue.of(True), PlaneValue.of(1), CONFIG)

        assert is_equal is False
        assert log.discovered_transformed != log.declared_transformed

    def test_a_discovered_zero_is_not_false(self):
        is_equal, _ = compare_boolean(PlaneValue.of(False), PlaneValue.of(0), CONFIG)

        assert is_equal is False

    def test_the_string_true_is_not_true(self):
        """`str(True)` is `"True"`, so a comparison that stringified would match."""
        is_equal, _ = compare_boolean(PlaneValue.of(True), PlaneValue.of("true"), CONFIG)

        assert is_equal is False


class TestStatementRule:
    """Absence and null are two facts, and neither is False."""

    def test_both_absent_agree(self):
        is_equal, _ = compare_boolean(PlaneValue.absent(), PlaneValue.absent(), CONFIG)

        assert is_equal is True

    def test_both_null_agree(self):
        is_equal, _ = compare_boolean(PlaneValue.of(None), PlaneValue.of(None), CONFIG)

        assert is_equal is True

    def test_absent_against_null_is_a_discrepancy(self):
        is_equal, _ = compare_boolean(PlaneValue.absent(), PlaneValue.of(None), CONFIG)

        assert is_equal is False

    def test_absent_against_false_is_a_discrepancy(self):
        """Not stating a field and stating it false are different claims.

        The one most likely to be collapsed, because a missing boolean reads as
        "off" in most configuration formats. It is not one here.
        """
        is_equal, _ = compare_boolean(PlaneValue.absent(), PlaneValue.of(False), CONFIG)

        assert is_equal is False

    def test_null_against_false_is_a_discrepancy(self):
        is_equal, _ = compare_boolean(PlaneValue.of(None), PlaneValue.of(False), CONFIG)

        assert is_equal is False


class TestUnknownMode:
    def test_an_unrecognised_mode_does_not_match(self):
        """Degrades to a discrepancy rather than raising, as the other types do.

        Unreachable through `FieldConfig`, which rejects the mode at
        construction; this covers the branch for a config built another way.
        """
        config = FieldConfig(
            field_name="enabled",
            field_type="boolean",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )
        object.__setattr__(config, "comparison", {"mode": "sideways"})

        is_equal, log = compare_boolean(PlaneValue.of(True), PlaneValue.of(True), config)

        assert is_equal is False
        assert log.comparison_mode == "sideways"
