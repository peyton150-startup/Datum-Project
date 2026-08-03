"""Test schema validation and loading (Phase 2A).

Tests ComparisonSchema initialization, FieldConfig validation, and error handling.
All test cases are from the adversarial corpus.
"""

import pytest

from datum.reconcile.schema import (
    ComparisonSchema,
    FieldConfig,
    InvalidComparisonMode,
    InvalidFieldType,
    InvalidLoggingLevel,
    InvalidModeParameter,
    MissingFieldConfig,
    SchemaError,
)


class TestFieldConfigValidation:
    """Test FieldConfig initialization and validation."""

    def test_valid_numeric_exact_value(self):
        """FieldConfig accepts valid numeric exact_value mode."""
        config = FieldConfig(
            field_name="replicas",
            field_type="numeric",
            comparison={"mode": "exact_value"},
            logging="discrepancy",
        )
        assert config.field_name == "replicas"
        assert config.field_type == "numeric"
        assert config.comparison["mode"] == "exact_value"

    def test_valid_numeric_exact_string(self):
        """FieldConfig accepts valid numeric exact_string mode."""
        config = FieldConfig(
            field_name="version",
            field_type="numeric",
            comparison={"mode": "exact_string"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "exact_string"

    def test_valid_numeric_tolerance(self):
        """FieldConfig accepts valid numeric tolerance mode."""
        config = FieldConfig(
            field_name="cpu",
            field_type="numeric",
            comparison={"mode": "tolerance(0.01)"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "tolerance(0.01)"

    def test_valid_string_exact(self):
        """FieldConfig accepts valid string exact mode."""
        config = FieldConfig(
            field_name="name",
            field_type="string",
            comparison={"mode": "exact"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "exact"

    def test_valid_string_lowercase(self):
        """FieldConfig accepts valid string lowercase mode."""
        config = FieldConfig(
            field_name="label",
            field_type="string",
            comparison={"mode": "lowercase"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "lowercase"

    def test_valid_string_trim(self):
        """FieldConfig accepts valid string trim mode."""
        config = FieldConfig(
            field_name="description",
            field_type="string",
            comparison={"mode": "trim"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "trim"

    def test_valid_string_normalize(self):
        """FieldConfig accepts valid string normalize mode."""
        config = FieldConfig(
            field_name="tag",
            field_type="string",
            comparison={"mode": "normalize"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "normalize"

    def test_valid_list_ordered(self):
        """FieldConfig accepts valid list ordered mode with element_comparison."""
        config = FieldConfig(
            field_name="ports",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": "exact"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "ordered"

    def test_valid_list_unordered_multiset(self):
        """FieldConfig accepts valid list unordered_multiset mode."""
        config = FieldConfig(
            field_name="env_vars",
            field_type="list",
            comparison={"mode": "unordered_multiset", "element_comparison": "exact"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "unordered_multiset"

    def test_valid_list_set(self):
        """FieldConfig accepts valid list set mode."""
        config = FieldConfig(
            field_name="labels",
            field_type="list",
            comparison={"mode": "set", "element_comparison": "exact"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "set"

    def test_valid_timestamp_string(self):
        """FieldConfig accepts valid timestamp string mode."""
        config = FieldConfig(
            field_name="created_at",
            field_type="timestamp",
            comparison={"mode": "string"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "string"

    def test_valid_timestamp_semantic_utc(self):
        """FieldConfig accepts valid timestamp semantic_utc mode with precision."""
        config = FieldConfig(
            field_name="updated_at",
            field_type="timestamp",
            comparison={"mode": "semantic_utc", "precision": "second"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "semantic_utc"

    def test_valid_timestamp_semantic_resource_tz(self):
        """FieldConfig accepts valid timestamp semantic_resource_tz mode."""
        config = FieldConfig(
            field_name="scheduled_at",
            field_type="timestamp",
            comparison={"mode": "semantic_resource_tz", "precision": "minute"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "semantic_resource_tz"

    def test_valid_object_opaque(self):
        """FieldConfig accepts valid object opaque mode."""
        config = FieldConfig(
            field_name="metadata",
            field_type="object",
            comparison={"mode": "opaque"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "opaque"

    def test_valid_object_version(self):
        """FieldConfig accepts valid object version mode."""
        config = FieldConfig(
            field_name="provider_tags",
            field_type="object",
            comparison={"mode": "version"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "version"

    def test_valid_object_identity(self):
        """FieldConfig accepts valid object identity mode."""
        config = FieldConfig(
            field_name="owner",
            field_type="object",
            comparison={"mode": "identity"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "identity"

    def test_valid_object_ignore(self):
        """FieldConfig accepts valid object ignore mode."""
        config = FieldConfig(
            field_name="internal_state",
            field_type="object",
            comparison={"mode": "ignore"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "ignore"

    def test_valid_object_recurse_zero(self):
        """FieldConfig accepts valid object recurse(0) mode."""
        config = FieldConfig(
            field_name="config",
            field_type="object",
            comparison={"mode": "recurse(0)"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "recurse(0)"

    def test_valid_object_recurse_negative_one(self):
        """FieldConfig accepts valid object recurse(-1) mode (full recursion)."""
        config = FieldConfig(
            field_name="spec",
            field_type="object",
            comparison={"mode": "recurse(-1)"},
            logging="discrepancy",
        )
        assert config.comparison["mode"] == "recurse(-1)"

    def test_valid_logging_debug(self):
        """FieldConfig accepts debug logging level."""
        config = FieldConfig(
            field_name="field",
            field_type="string",
            comparison={"mode": "exact"},
            logging="debug",
        )
        assert config.logging == "debug"

    def test_valid_logging_sampled_audit(self):
        """FieldConfig accepts sampled_audit logging level."""
        config = FieldConfig(
            field_name="field",
            field_type="string",
            comparison={"mode": "exact"},
            logging="sampled_audit",
        )
        assert config.logging == "sampled_audit"

    def test_invalid_field_type(self):
        """FieldConfig rejects unknown field type."""
        with pytest.raises(InvalidFieldType, match="unknown type"):
            FieldConfig(
                field_name="field",
                field_type="unknown_type",
                comparison={"mode": "exact"},
                logging="discrepancy",
            )

    def test_invalid_logging_level(self):
        """FieldConfig rejects invalid logging level."""
        with pytest.raises(InvalidLoggingLevel, match="invalid logging level"):
            FieldConfig(
                field_name="field",
                field_type="string",
                comparison={"mode": "exact"},
                logging="invalid",
            )

    def test_invalid_numeric_mode(self):
        """FieldConfig rejects invalid numeric mode."""
        with pytest.raises(InvalidComparisonMode, match="invalid numeric mode"):
            FieldConfig(
                field_name="replicas",
                field_type="numeric",
                comparison={"mode": "invalid_mode"},
                logging="discrepancy",
            )

    def test_invalid_string_mode(self):
        """FieldConfig rejects invalid string mode."""
        with pytest.raises(InvalidComparisonMode, match="invalid string mode"):
            FieldConfig(
                field_name="name",
                field_type="string",
                comparison={"mode": "invalid_mode"},
                logging="discrepancy",
            )

    def test_invalid_list_mode(self):
        """FieldConfig rejects invalid list mode."""
        with pytest.raises(InvalidComparisonMode, match="invalid list mode"):
            FieldConfig(
                field_name="items",
                field_type="list",
                comparison={"mode": "invalid_mode", "element_comparison": "exact"},
                logging="discrepancy",
            )

    def test_invalid_timestamp_mode(self):
        """FieldConfig rejects invalid timestamp mode."""
        with pytest.raises(InvalidComparisonMode, match="invalid timestamp mode"):
            FieldConfig(
                field_name="created_at",
                field_type="timestamp",
                comparison={"mode": "invalid_mode"},
                logging="discrepancy",
            )

    def test_invalid_object_mode(self):
        """FieldConfig rejects invalid object mode."""
        with pytest.raises(InvalidComparisonMode, match="invalid object mode"):
            FieldConfig(
                field_name="metadata",
                field_type="object",
                comparison={"mode": "invalid_mode"},
                logging="discrepancy",
            )

    def test_list_missing_element_comparison(self):
        """FieldConfig rejects list mode without element_comparison."""
        with pytest.raises(InvalidComparisonMode, match="element_comparison.*required"):
            FieldConfig(
                field_name="items",
                field_type="list",
                comparison={"mode": "ordered"},
                logging="discrepancy",
            )

    def test_timestamp_semantic_utc_missing_precision(self):
        """FieldConfig rejects semantic_utc without precision."""
        with pytest.raises(InvalidComparisonMode, match="precision.*required"):
            FieldConfig(
                field_name="created_at",
                field_type="timestamp",
                comparison={"mode": "semantic_utc"},
                logging="discrepancy",
            )

    def test_timestamp_semantic_resource_tz_missing_precision(self):
        """FieldConfig rejects semantic_resource_tz without precision."""
        with pytest.raises(InvalidComparisonMode, match="precision.*required"):
            FieldConfig(
                field_name="scheduled_at",
                field_type="timestamp",
                comparison={"mode": "semantic_resource_tz"},
                logging="discrepancy",
            )

    def test_timestamp_invalid_precision(self):
        """FieldConfig rejects invalid precision."""
        with pytest.raises(InvalidModeParameter, match="invalid precision"):
            FieldConfig(
                field_name="created_at",
                field_type="timestamp",
                comparison={"mode": "semantic_utc", "precision": "invalid"},
                logging="discrepancy",
            )

    def test_numeric_tolerance_negative_value(self):
        """FieldConfig rejects negative tolerance value.

        Matches the stated rule rather than a nested exception's wording: the
        message used to end in the text of an inner ValueError, which was an
        implementation detail of how the parameter was read.
        """
        with pytest.raises(InvalidModeParameter, match="non-negative"):
            FieldConfig(
                field_name="cpu",
                field_type="numeric",
                comparison={"mode": "tolerance(-0.01)"},
                logging="discrepancy",
            )

    def test_numeric_tolerance_invalid_format(self):
        """FieldConfig rejects invalid tolerance format."""
        with pytest.raises(InvalidModeParameter, match="tolerance"):
            FieldConfig(
                field_name="cpu",
                field_type="numeric",
                comparison={"mode": "tolerance(invalid)"},
                logging="discrepancy",
            )

    def test_object_recurse_invalid_depth(self):
        """FieldConfig rejects recurse with invalid depth.

        Matches the stated rule rather than a nested exception's wording, for
        the reason given on the tolerance case above.
        """
        with pytest.raises(InvalidModeParameter, match=r"integer >= -1"):
            FieldConfig(
                field_name="spec",
                field_type="object",
                comparison={"mode": "recurse(-2)"},
                logging="discrepancy",
            )

    def test_object_recurse_invalid_format(self):
        """FieldConfig rejects invalid recurse format."""
        with pytest.raises(InvalidModeParameter, match="recurse"):
            FieldConfig(
                field_name="spec",
                field_type="object",
                comparison={"mode": "recurse(invalid)"},
                logging="discrepancy",
            )

    def test_missing_mode_key(self):
        """FieldConfig rejects comparison config without mode."""
        with pytest.raises(InvalidComparisonMode, match="mode.*required"):
            FieldConfig(
                field_name="field",
                field_type="string",
                comparison={"other_key": "value"},
                logging="discrepancy",
            )

    def test_empty_comparison_config(self):
        """FieldConfig rejects empty comparison config."""
        with pytest.raises(InvalidComparisonMode, match="empty"):
            FieldConfig(
                field_name="field",
                field_type="string",
                comparison={},
                logging="discrepancy",
            )


class TestComparisonSchemaValidation:
    """Test ComparisonSchema initialization and validation."""

    def test_valid_schema_single_field(self):
        """ComparisonSchema accepts valid single-field schema."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "exact_value"},
            }
        }
        schema = ComparisonSchema("Deployment", raw_schema)
        assert schema.kind_name == "Deployment"
        assert "replicas" in schema.fields
        assert schema.fields["replicas"].field_type == "numeric"

    def test_valid_schema_multiple_fields(self):
        """ComparisonSchema accepts valid multi-field schema."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "exact_value"},
            },
            "image": {
                "type": "string",
                "comparison": {"mode": "exact"},
            },
            "labels": {
                "type": "list",
                "comparison": {"mode": "set", "element_comparison": "exact"},
            },
        }
        schema = ComparisonSchema("Deployment", raw_schema)
        assert len(schema.fields) == 3
        assert "replicas" in schema.fields
        assert "image" in schema.fields
        assert "labels" in schema.fields

    def test_valid_schema_with_default_logging(self):
        """ComparisonSchema defaults logging to 'discrepancy' if not specified."""
        raw_schema = {
            "name": {
                "type": "string",
                "comparison": {"mode": "exact"},
                # logging not specified
            }
        }
        schema = ComparisonSchema("Kind", raw_schema)
        assert schema.fields["name"].logging == "discrepancy"

    def test_valid_schema_with_explicit_logging(self):
        """ComparisonSchema respects explicit logging level."""
        raw_schema = {
            "name": {
                "type": "string",
                "comparison": {"mode": "exact"},
                "logging": "debug",
            }
        }
        schema = ComparisonSchema("Kind", raw_schema)
        assert schema.fields["name"].logging == "debug"

    def test_schema_not_dict(self):
        """ComparisonSchema rejects non-dict schema."""
        with pytest.raises(SchemaError, match="must be a dict"):
            ComparisonSchema("Kind", "not a dict")

    def test_schema_empty(self):
        """ComparisonSchema rejects empty schema."""
        with pytest.raises(SchemaError, match="empty"):
            ComparisonSchema("Kind", {})

    def test_field_definition_not_dict(self):
        """ComparisonSchema rejects field definition that is not a dict."""
        raw_schema = {
            "name": "not a dict",
        }
        with pytest.raises(SchemaError, match="must be a dict"):
            ComparisonSchema("Kind", raw_schema)

    def test_field_missing_type_key(self):
        """ComparisonSchema rejects field definition without 'type'."""
        raw_schema = {
            "name": {
                "comparison": {"mode": "exact"},
            }
        }
        with pytest.raises(SchemaError, match="type.*required"):
            ComparisonSchema("Kind", raw_schema)

    def test_field_missing_comparison_key(self):
        """ComparisonSchema rejects field definition without 'comparison'."""
        raw_schema = {
            "name": {
                "type": "string",
            }
        }
        with pytest.raises(SchemaError, match="comparison.*required"):
            ComparisonSchema("Kind", raw_schema)

    def test_get_field_config_exists(self):
        """ComparisonSchema.get_field_config returns config for existing field."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "exact_value"},
            }
        }
        schema = ComparisonSchema("Deployment", raw_schema)
        config = schema.get_field_config("replicas")
        assert config.field_name == "replicas"
        assert config.field_type == "numeric"

    def test_get_field_config_missing(self):
        """ComparisonSchema.get_field_config raises MissingFieldConfig for missing field."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "exact_value"},
            }
        }
        schema = ComparisonSchema("Deployment", raw_schema)
        with pytest.raises(MissingFieldConfig, match="no comparison config"):
            schema.get_field_config("nonexistent")

    def test_schema_repr(self):
        """ComparisonSchema.__repr__ provides useful string representation."""
        raw_schema = {
            "field1": {"type": "string", "comparison": {"mode": "exact"}},
            "field2": {"type": "numeric", "comparison": {"mode": "exact_value"}},
        }
        schema = ComparisonSchema("Kind", raw_schema)
        assert "Kind" in repr(schema)
        assert "2 fields" in repr(schema)

    def test_complex_valid_schema(self):
        """ComparisonSchema accepts complex valid schema with all field types."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "exact_value"},
                "logging": "discrepancy",
            },
            "image": {
                "type": "string",
                "comparison": {"mode": "exact"},
                "logging": "discrepancy",
            },
            "labels": {
                "type": "list",
                "comparison": {"mode": "set", "element_comparison": "exact"},
                "logging": "discrepancy",
            },
            "created_at": {
                "type": "timestamp",
                "comparison": {"mode": "semantic_utc", "precision": "second"},
                "logging": "discrepancy",
            },
            "metadata": {
                "type": "object",
                "comparison": {"mode": "opaque"},
                "logging": "debug",
            },
        }
        schema = ComparisonSchema("Deployment", raw_schema)
        assert len(schema.fields) == 5
        assert all(field in schema.fields for field in raw_schema.keys())

    def test_invalid_mode_propagates_from_field_config(self):
        """ComparisonSchema propagates validation errors from FieldConfig."""
        raw_schema = {
            "replicas": {
                "type": "numeric",
                "comparison": {"mode": "invalid_mode"},
            }
        }
        with pytest.raises(InvalidComparisonMode):
            ComparisonSchema("Deployment", raw_schema)


class TestAToleranceIsAFiniteNumber:
    """`tolerance(inf)` and `tolerance(nan)` are rejected at the barricade.

    Both parse as floats and both passed the `< MINIMUM_TOLERANCE` guard, for
    two different reasons: NaN compares false against everything, so `nan < 0.0`
    is False, and infinity really is larger. The guard was written to catch
    unusable tolerances and could not see either one.

    `inf` is the one that mattered. `abs(d - c) <= inf` holds for every pair of
    values, so the field always matched and every real discrepancy on it was
    suppressed with nothing in the audit log to say so -- a wrong result, not a
    degradation. `nan` fails the other way, never matching, which at least
    announces itself.
    """

    def config(self, mode: str) -> FieldConfig:
        return FieldConfig("replicas", "numeric", {"mode": mode}, "discrepancy")

    @pytest.mark.parametrize(
        "mode",
        [
            "tolerance(inf)",
            "tolerance(Infinity)",  # float() takes this spelling too
            "tolerance(-inf)",  # was already rejected, by the range check
            "tolerance(nan)",
            "tolerance(NaN)",
        ],
    )
    def test_a_non_finite_tolerance_is_refused(self, mode):
        with pytest.raises(InvalidModeParameter):
            self.config(mode)

    @pytest.mark.parametrize("mode", ["tolerance(0)", "tolerance(0.5)", "tolerance(1e308)"])
    def test_an_ordinary_tolerance_is_still_accepted(self, mode):
        """The guard must reject only what it was written to reject.

        `1e308` is finite and enormous, which is the nearest legal neighbour of
        the value being excluded: a guard that rejected "very large" rather than
        "not finite" would fail here.
        """
        assert self.config(mode).comparison["mode"] == mode

    @pytest.mark.parametrize("mode", ["recurse(inf)", "recurse(nan)"])
    def test_a_non_finite_depth_is_refused(self, mode):
        """Guard, not demonstration: `int()` rejects both spellings on its own.

        Says so rather than borrowing credit from the tolerance fix. There is
        no non-finite Python int for a check to catch here; these are refused
        because the two words do not parse as integers.
        """
        with pytest.raises(InvalidModeParameter):
            FieldConfig("spec", "object", {"mode": mode}, "discrepancy")

    @pytest.mark.parametrize("digits", [308, 309, 310, 400])
    def test_an_enormous_depth_is_absurd_rather_than_fatal(self, digits):
        """The boundary a shared finiteness check moved, on the wrong parameter.

        `math.isfinite` converts to a C double before answering, so on a Python
        int past roughly 1.8e308 it raises `OverflowError` instead of returning
        False. Checking finiteness in the parser shared by both parameters
        therefore crashed the barricade at 309 digits while 308 returned
        normally -- an uncaught `OverflowError` out of `FieldConfig`, where the
        contract is `InvalidModeParameter` or nothing.

        A depth no data can nest to is equivalent to full recursion and has
        always been accepted. Refusing non-finite *tolerances* must not change
        that, which is why the check lives in `_finite_float` and not in
        `_mode_parameter`. 308 is the last value that passed under the defect
        and 309 the first that crashed; both are asserted so the test fails on
        either side of the boundary moving again.
        """
        mode = "recurse(" + "9" * digits + ")"
        assert (
            FieldConfig("spec", "object", {"mode": mode}, "discrepancy").comparison["mode"] == mode
        )
