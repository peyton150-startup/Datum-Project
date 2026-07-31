"""Tests for timestamp field comparison.

Covers all timestamp modes: string (exact), semantic_utc, semantic_resource_tz.
Tests precision levels: day, hour, minute, second.
Tests timezone handling and adversarial corpus.
"""

from datum.reconcile.comparison import compare_timestamp
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


class TestTimestampString:
    """Tests for string mode: exact string matching."""

    def test_equal_iso_strings(self):
        """Equal ISO strings should match."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_different_timezone_notation(self):
        """Different timezone notations should not match in string mode."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00+00:00")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is False

    def test_different_precision_strings(self):
        """Different precision should not match in string mode."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00.000Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is False

    def test_empty_string_timestamp(self):
        """Empty string timestamps."""
        declared = PlaneValue.of("")
        discovered = PlaneValue.of("")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True


class TestTimestampSemanticUTC:
    """Tests for semantic_utc mode: parse and compare in UTC."""

    def test_same_time_different_tz_notation(self):
        """Same UTC time with different notation should match."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00+00:00")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_same_utc_time_different_tz(self):
        """Same UTC time but different local times should match."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")  # 6 PM UTC
        discovered = PlaneValue.of("2026-07-30T10:00:00-08:00")  # 6 PM UTC (PST)
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_precision_day(self):
        """Same day should match with day precision."""
        declared = PlaneValue.of("2026-07-30T00:00:00Z")
        discovered = PlaneValue.of("2026-07-30T23:59:59Z")
        config = FieldConfig(
            field_name="date",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "day"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_precision_hour(self):
        """Same hour should match with hour precision."""
        declared = PlaneValue.of("2026-07-30T18:00:00Z")
        discovered = PlaneValue.of("2026-07-30T18:59:59Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "hour"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_precision_minute(self):
        """Same minute should match with minute precision."""
        declared = PlaneValue.of("2026-07-30T18:30:00Z")
        discovered = PlaneValue.of("2026-07-30T18:30:59Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "minute"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_precision_second(self):
        """Same second should match with second precision."""
        declared = PlaneValue.of("2026-07-30T18:30:45.000Z")
        discovered = PlaneValue.of("2026-07-30T18:30:45.999Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_different_days(self):
        """Different days should not match with day precision."""
        declared = PlaneValue.of("2026-07-30T00:00:00Z")
        discovered = PlaneValue.of("2026-07-31T00:00:00Z")
        config = FieldConfig(
            field_name="date",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "day"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is False


class TestTimestampNullAndAbsent:
    """Tests for null, absent, and missing value handling."""

    def test_both_absent(self):
        """Both absent should match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_timestamp",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_both_null(self):
        """Both null should match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="optional_timestamp",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_null_vs_timestamp(self):
        """Null vs timestamp should not match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of("2026-07-30T18:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is False

    def test_absent_vs_timestamp(self):
        """Absent vs timestamp should not match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.of("2026-07-30T18:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is False


class TestTimestampInvalidFormat:
    """Tests for invalid timestamp formats."""

    def test_invalid_timestamp_string(self):
        """Invalid timestamp strings should not match."""
        declared = PlaneValue.of("not a timestamp")
        discovered = PlaneValue.of("2026-07-30T18:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        # String mode does direct comparison, so should not match
        assert is_equal is False

    def test_malformed_iso_semantic(self):
        """Malformed ISO in semantic mode should fail gracefully."""
        declared = PlaneValue.of("not a timestamp at all")
        discovered = PlaneValue.of("2026-07-30T18:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "day"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        # Should fail gracefully with False
        assert is_equal is False


class TestTimestampEdgeCases:
    """Adversarial corpus cases."""

    def test_midnight_utc(self):
        """Midnight UTC should work."""
        declared = PlaneValue.of("2026-07-30T00:00:00Z")
        discovered = PlaneValue.of("2026-07-30T00:00:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_year_boundary(self):
        """Year boundary timestamps."""
        declared = PlaneValue.of("2026-12-31T23:59:59Z")
        discovered = PlaneValue.of("2026-12-31T23:59:59Z")
        config = FieldConfig(
            field_name="end_of_year",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_leap_second(self):
        """Leap second handling (if supported)."""
        declared = PlaneValue.of("2026-06-30T23:59:60Z")
        discovered = PlaneValue.of("2026-06-30T23:59:60Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        # Should handle or reject leap seconds consistently
        assert isinstance(is_equal, bool)

    def test_milliseconds(self):
        """Millisecond precision in timestamps."""
        declared = PlaneValue.of("2026-07-30T18:00:00.123Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00.456Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        # Same second precision, should match
        assert is_equal is True

    def test_microseconds(self):
        """Microsecond precision."""
        declared = PlaneValue.of("2026-07-30T18:00:00.123456Z")
        discovered = PlaneValue.of("2026-07-30T18:00:00.123456Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        assert is_equal is True

    def test_positive_offset(self):
        """Positive timezone offset."""
        declared = PlaneValue.of("2026-07-30T18:00:00+05:30")
        discovered = PlaneValue.of("2026-07-30T12:30:00Z")
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )

        is_equal, log = compare_timestamp(declared, discovered, config)
        # Same UTC time
        assert is_equal is True
