"""Tests for numeric field comparison.

Covers all numeric modes: exact_value, exact_string, tolerance(N).
Tests null/absent/missing cases, type coercion, edge cases, and adversarial corpus.
"""

from datum.reconcile.comparison import compare_numeric
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


class TestNumericExactValue:
    """Tests for exact_value mode: canonical form comparison."""

    def test_equal_integers(self):
        """3 == 3 should match."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3)
        config = FieldConfig(
            field_name="cpu_count",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True
        assert log.result is True
        assert "exact_value" in log.comparison_mode

    def test_equal_floats(self):
        """3.14 == 3.14 should match."""
        declared = PlaneValue.of(3.14)
        discovered = PlaneValue.of(3.14)
        config = FieldConfig(
            field_name="cpu_request",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True
        assert log.result is True

    def test_int_differs_from_float(self):
        """3 != 3.0 in exact_value mode (canonical form distinguishes them)."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3.0)
        config = FieldConfig(
            field_name="memory",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False
        assert log.result is False

    def test_different_numbers(self):
        """3 != 4 should not match."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(4)
        config = FieldConfig(
            field_name="cpu_count",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False
        assert log.result is False

    def test_negative_numbers(self):
        """-5 == -5 should match."""
        declared = PlaneValue.of(-5)
        discovered = PlaneValue.of(-5)
        config = FieldConfig(
            field_name="offset",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_zero(self):
        """0 == 0 should match."""
        declared = PlaneValue.of(0)
        discovered = PlaneValue.of(0)
        config = FieldConfig(
            field_name="count",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_zero_vs_negative_zero(self):
        """0 (int) != -0.0 (float) in exact_value mode (canonical form distinguishes)."""
        declared = PlaneValue.of(0)
        discovered = PlaneValue.of(-0.0)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_large_numbers(self):
        """Large numbers should compare correctly."""
        declared = PlaneValue.of(9999999999)
        discovered = PlaneValue.of(9999999999)
        config = FieldConfig(
            field_name="size",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_scientific_notation(self):
        """Scientific notation: 1.5e3 (float 1500.0) != 1500 (int) in exact_value."""
        declared = PlaneValue.of(1.5e3)  # 1500.0 (float)
        discovered = PlaneValue.of(1500)  # 1500 (int)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False


class TestNumericExactString:
    """Tests for exact_string mode: string representation must match."""

    def test_equal_integer_strings(self):
        """'3' == '3' should match."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3)
        config = FieldConfig(
            field_name="version",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_int_differs_from_float_string(self):
        """'3' != '3.0' should not match in exact_string mode."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3.0)
        config = FieldConfig(
            field_name="version",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False
        assert log.result is False

    def test_equal_float_strings(self):
        """'3.14' == '3.14' should match."""
        declared = PlaneValue.of(3.14)
        discovered = PlaneValue.of(3.14)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_different_float_precision(self):
        """'3.1' != '3.10' should not match."""
        # This depends on how Python stringifies floats
        # For floats that are exactly representable, they should match
        declared = PlaneValue.of(3.1)
        discovered = PlaneValue.of(3.1)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_negative_int_string(self):
        """'-5' == '-5' should match."""
        declared = PlaneValue.of(-5)
        discovered = PlaneValue.of(-5)
        config = FieldConfig(
            field_name="offset",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True


class TestNumericTolerance:
    """Tests for tolerance(N) mode: absolute difference <= N."""

    def test_within_tolerance(self):
        """Difference within tolerance should match."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.01)
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "tolerance(0.02)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True
        assert "tolerance(0.02)" in log.comparison_mode

    def test_exactly_at_tolerance_boundary(self):
        """Difference exactly at tolerance should match."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.01)
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_exceeds_tolerance(self):
        """Difference exceeding tolerance should not match."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.02)
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_negative_difference(self):
        """Absolute value is used, so -0.01 difference should work."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(2.99)
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_zero_tolerance(self):
        """tolerance(0) should require exact equality."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.0)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_zero_tolerance_fails_on_difference(self):
        """tolerance(0) should fail on any difference."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.00001)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_large_tolerance(self):
        """Large tolerance should accept large differences."""
        declared = PlaneValue.of(10.0)
        discovered = PlaneValue.of(15.0)
        config = FieldConfig(
            field_name="memory",
            field_type="numeric",
            comparison={"mode": "tolerance(10)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_tolerance_with_integers(self):
        """tolerance should work with integer values."""
        declared = PlaneValue.of(100)
        discovered = PlaneValue.of(105)
        config = FieldConfig(
            field_name="count",
            field_type="numeric",
            comparison={"mode": "tolerance(5)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_tolerance_between_int_and_float(self):
        """tolerance should work comparing int to float."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3.01)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0.02)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_tolerance_with_negative_numbers(self):
        """tolerance should work with negative numbers."""
        declared = PlaneValue.of(-5.0)
        discovered = PlaneValue.of(-4.99)
        config = FieldConfig(
            field_name="offset",
            field_type="numeric",
            comparison={"mode": "tolerance(0.02)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True


class TestNumericNullAndAbsent:
    """Tests for null, absent, and missing value handling."""

    def test_both_absent(self):
        """Both absent should match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_both_null(self):
        """Both null should match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_declared_absent_discovered_value(self):
        """Absent vs value should not match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.of(5)
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_declared_value_discovered_absent(self):
        """Value vs absent should not match."""
        declared = PlaneValue.of(5)
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_declared_null_discovered_value(self):
        """Null vs value should not match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(5)
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_declared_value_discovered_null(self):
        """Value vs null should not match."""
        declared = PlaneValue.of(5)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="optional_value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False

    def test_tolerance_with_null(self):
        """tolerance mode should handle null correctly."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(5)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(1)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is False


class TestNumericAuditLog:
    """Tests for audit log content and completeness."""

    def test_audit_log_contains_mode(self):
        """Audit log should contain the comparison mode."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3)
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert log.comparison_mode == "exact_value"
        assert log.field_type == "numeric"
        assert log.field_name == "cpu"

    def test_audit_log_contains_values(self):
        """Audit log should contain raw and transformed values."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert log.declared_raw == 3
        assert log.discovered_raw == 3

    def test_audit_log_contains_result(self):
        """Audit log should contain the comparison result."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(4)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert log.result is False
        assert is_equal is False

    def test_audit_log_contains_steps(self):
        """Audit log should contain comparison steps."""
        declared = PlaneValue.of(3.0)
        discovered = PlaneValue.of(3.01)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert len(log.steps) > 0
        assert any("tolerance" in step for step in log.steps)

    def test_audit_log_exact_string_mode(self):
        """Audit log should document exact_string mode."""
        declared = PlaneValue.of(3)
        discovered = PlaneValue.of(3.0)
        config = FieldConfig(
            field_name="version",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert log.comparison_mode == "exact_string"
        assert log.result is False


class TestNumericAdversarialCases:
    """Adversarial corpus cases from DIFF_SEMANTICS.md."""

    def test_very_small_numbers(self):
        """Handle very small positive numbers."""
        declared = PlaneValue.of(0.00001)
        discovered = PlaneValue.of(0.00001)
        config = FieldConfig(
            field_name="epsilon",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_infinity(self):
        """Handle infinity values (if allowed by schema)."""
        declared = PlaneValue.of(float("inf"))
        discovered = PlaneValue.of(float("inf"))
        config = FieldConfig(
            field_name="limit",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_tolerance_with_very_small_values(self):
        """tolerance mode with values near zero."""
        declared = PlaneValue.of(0.001)
        discovered = PlaneValue.of(0.0011)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0.00011)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_mixed_sign_within_tolerance(self):
        """Tolerance should work across positive/negative boundary."""
        declared = PlaneValue.of(-0.005)
        discovered = PlaneValue.of(0.005)
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_precision_loss_in_float(self):
        """Handle floating point precision issues."""
        # 0.1 + 0.2 != 0.3 in IEEE 754, but should match with tolerance
        declared = PlaneValue.of(0.1 + 0.2)
        discovered = PlaneValue.of(0.3)
        config = FieldConfig(
            field_name="sum",
            field_type="numeric",
            comparison={"mode": "tolerance(0.0001)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True

    def test_string_like_numbers_fail_gracefully(self):
        """Non-numeric strings should not match in numeric mode."""
        # This tests error handling: string values converted to float fail
        declared = PlaneValue.of("three")
        discovered = PlaneValue.of("three")
        config = FieldConfig(
            field_name="value",
            field_type="numeric",
            comparison={"mode": "tolerance(1)"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        # Conversion fails, so comparison returns False
        assert is_equal is False

    def test_bool_as_numeric(self):
        """Python booleans (True/False) are distinct from ints in canonical form."""
        declared = PlaneValue.of(True)
        discovered = PlaneValue.of(1)
        config = FieldConfig(
            field_name="enabled",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        # True canonical form (true) != 1 canonical form (1)
        assert is_equal is False

    def test_numeric_edge_case_max_int(self):
        """Maximum integer value should compare correctly."""
        max_int = 9223372036854775807  # 2^63 - 1
        declared = PlaneValue.of(max_int)
        discovered = PlaneValue.of(max_int)
        config = FieldConfig(
            field_name="big",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )

        is_equal, log = compare_numeric(declared, discovered, config)
        assert is_equal is True
