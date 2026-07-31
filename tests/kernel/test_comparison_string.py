"""Tests for string field comparison.

Covers all string modes: exact, lowercase, trim, normalize.
Tests null/absent/missing cases, whitespace, unicode, and adversarial corpus.
"""

from datum.reconcile.comparison import compare_string
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


class TestStringExact:
    """Tests for exact mode: no transformations."""

    def test_equal_strings(self):
        """'hello' == 'hello' should match."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True
        assert log.result is True

    def test_different_strings(self):
        """'hello' != 'world' should not match."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.of("world")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_case_sensitive(self):
        """'Hello' != 'hello' in exact mode."""
        declared = PlaneValue.of("Hello")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_empty_strings(self):
        """'' == '' should match."""
        declared = PlaneValue.of("")
        discovered = PlaneValue.of("")
        config = FieldConfig(
            field_name="optional",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_whitespace_sensitive(self):
        """'hello' != 'hello ' in exact mode."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.of("hello ")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_internal_whitespace_preserved(self):
        """'hello  world' with double space should be sensitive."""
        declared = PlaneValue.of("hello  world")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_unicode_strings(self):
        """Unicode strings should compare correctly."""
        declared = PlaneValue.of("café")
        discovered = PlaneValue.of("café")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_numeric_strings(self):
        """Numeric strings should compare as strings."""
        declared = PlaneValue.of("123")
        discovered = PlaneValue.of("123")
        config = FieldConfig(
            field_name="id",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True


class TestStringLowercase:
    """Tests for lowercase mode: case-insensitive comparison."""

    def test_case_insensitive_match(self):
        """'Hello' == 'hello' in lowercase mode."""
        declared = PlaneValue.of("Hello")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_uppercase_match(self):
        """'HELLO' == 'hello' in lowercase mode."""
        declared = PlaneValue.of("HELLO")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_mixed_case_match(self):
        """'HeLLo WoRLd' == 'hello world' in lowercase mode."""
        declared = PlaneValue.of("HeLLo WoRLd")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_whitespace_still_sensitive(self):
        """Whitespace is still sensitive in lowercase mode."""
        declared = PlaneValue.of("hello ")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_audit_log_shows_transformation(self):
        """Audit log should show raw and transformed values."""
        declared = PlaneValue.of("Hello")
        discovered = PlaneValue.of("WORLD")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert log.declared_raw == "Hello"
        assert log.declared_transformed == "hello"
        assert log.discovered_raw == "WORLD"
        assert log.discovered_transformed == "world"


class TestStringTrim:
    """Tests for trim mode: strip whitespace before comparing."""

    def test_leading_whitespace(self):
        """'  hello' == 'hello' in trim mode."""
        declared = PlaneValue.of("  hello")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_trailing_whitespace(self):
        """'hello  ' == 'hello' in trim mode."""
        declared = PlaneValue.of("hello  ")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_both_sides_trimmed(self):
        """'  hello  ' == '  hello  ' in trim mode."""
        declared = PlaneValue.of("  hello  ")
        discovered = PlaneValue.of("  hello  ")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_internal_whitespace_preserved(self):
        """Internal whitespace is preserved in trim mode."""
        declared = PlaneValue.of("  hello  world  ")
        discovered = PlaneValue.of("hello  world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_case_still_sensitive(self):
        """Case is still sensitive in trim mode."""
        declared = PlaneValue.of("  Hello  ")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_only_whitespace(self):
        """'   ' trimmed equals ''."""
        declared = PlaneValue.of("   ")
        discovered = PlaneValue.of("")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True


class TestStringNormalize:
    """Tests for normalize mode: lowercase + trim + collapse whitespace."""

    def test_case_normalization(self):
        """Case is normalized."""
        declared = PlaneValue.of("HELLO")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_whitespace_normalization(self):
        """'  hello  world  ' == 'hello world' in normalize mode."""
        declared = PlaneValue.of("  hello  world  ")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_collapse_multiple_spaces(self):
        """Multiple internal spaces are collapsed."""
        declared = PlaneValue.of("hello    world")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_newline_handling(self):
        """Newlines are collapsed to single space."""
        declared = PlaneValue.of("hello\nworld")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_tab_handling(self):
        """Tabs are collapsed to single space."""
        declared = PlaneValue.of("hello\tworld")
        discovered = PlaneValue.of("hello world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_mixed_whitespace(self):
        """Mixed whitespace is normalized."""
        declared = PlaneValue.of("  Hello  \n  World  \t!")
        discovered = PlaneValue.of("hello world !")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_only_whitespace_normalized(self):
        """Whitespace-only string normalizes to empty."""
        declared = PlaneValue.of("   \n\t  ")
        discovered = PlaneValue.of("")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True


class TestStringNullAndAbsent:
    """Tests for null, absent, and missing value handling."""

    def test_both_absent(self):
        """Both absent should match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_both_null(self):
        """Both null should match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="optional_text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_declared_absent_discovered_value(self):
        """Absent vs value should not match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="optional_text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_declared_value_discovered_absent(self):
        """Value vs absent should not match."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_declared_null_discovered_value(self):
        """Null vs value should not match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="optional_text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_null_in_normalize_mode(self):
        """Null/absent handling works in normalize mode."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True


class TestStringAuditLog:
    """Tests for audit log content and completeness."""

    def test_audit_log_contains_mode(self):
        """Audit log should contain the comparison mode."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.of("hello")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert log.comparison_mode == "exact"
        assert log.field_type == "string"

    def test_audit_log_contains_values(self):
        """Audit log should contain raw and transformed values."""
        declared = PlaneValue.of("  Hello  ")
        discovered = PlaneValue.of("  WORLD  ")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert log.declared_raw == "  Hello  "
        assert log.declared_transformed == "hello"
        assert log.discovered_raw == "  WORLD  "
        assert log.discovered_transformed == "world"

    def test_audit_log_contains_steps(self):
        """Audit log should contain comparison steps."""
        declared = PlaneValue.of("hello")
        discovered = PlaneValue.of("world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert len(log.steps) > 0
        assert any("exact" in step for step in log.steps)


class TestStringAdversarialCases:
    """Adversarial corpus cases from DIFF_SEMANTICS.md."""

    def test_empty_vs_whitespace_exact(self):
        """'' != '   ' in exact mode."""
        declared = PlaneValue.of("")
        discovered = PlaneValue.of("   ")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_empty_vs_whitespace_trim(self):
        """'' == '   ' in trim mode."""
        declared = PlaneValue.of("")
        discovered = PlaneValue.of("   ")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_unicode_normalization_insensitive(self):
        """Unicode combining characters."""
        declared = PlaneValue.of("café")  # Single char é
        discovered = PlaneValue.of("cafe")
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_very_long_string(self):
        """Very long string comparison."""
        long_text = "a" * 10000
        declared = PlaneValue.of(long_text)
        discovered = PlaneValue.of(long_text)
        config = FieldConfig(
            field_name="description",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_numeric_conversion_from_int(self):
        """Integer converted to string should work."""
        declared = PlaneValue.of(123)
        discovered = PlaneValue.of("123")
        config = FieldConfig(
            field_name="id",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_boolean_conversion_to_string(self):
        """Boolean converted to string."""
        declared = PlaneValue.of(True)
        discovered = PlaneValue.of("True")
        config = FieldConfig(
            field_name="flag",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_zero_padding_not_normalized(self):
        """'001' != '1' in exact mode."""
        declared = PlaneValue.of("001")
        discovered = PlaneValue.of("1")
        config = FieldConfig(
            field_name="code",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is False

    def test_special_characters(self):
        """Special characters should be preserved."""
        declared = PlaneValue.of("hello!@#$%^&*()")
        discovered = PlaneValue.of("hello!@#$%^&*()")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True

    def test_punctuation_in_normalize(self):
        """Punctuation is preserved in normalize mode."""
        declared = PlaneValue.of("  hello  !  world  ")
        discovered = PlaneValue.of("hello ! world")
        config = FieldConfig(
            field_name="text",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )

        is_equal, log = compare_string(declared, discovered, config)
        assert is_equal is True
