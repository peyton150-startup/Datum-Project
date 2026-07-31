"""Schema validation and loading for field comparison configurations.

This module validates Kind.attribute_schema and provides FieldConfig objects
to comparison functions. Each field must have an explicit comparison config;
no defaults are inferred.
"""

from dataclasses import dataclass
from typing import Any, Dict


class SchemaError(Exception):
    """Base exception for schema validation errors."""

    pass


class MissingFieldConfig(SchemaError):
    """A field is missing a comparison configuration."""

    pass


class InvalidFieldType(SchemaError):
    """An unknown or invalid field type."""

    pass


class InvalidComparisonMode(SchemaError):
    """An invalid comparison mode for the field type."""

    pass


class InvalidLoggingLevel(SchemaError):
    """An invalid logging level."""

    pass


class InvalidModeParameter(SchemaError):
    """A mode parameter (e.g., tolerance value) is invalid."""

    pass


# Valid field types
VALID_FIELD_TYPES = {"list", "numeric", "string", "timestamp", "object"}

# Valid logging levels (3 tiers)
VALID_LOGGING_LEVELS = {"debug", "discrepancy", "sampled_audit"}


@dataclass(frozen=True)
class FieldConfig:
    """Immutable configuration for how a field is compared.

    Attributes:
        field_name: Name of the field
        field_type: Type of the field (list, numeric, string, timestamp, object)
        comparison: Dict containing mode and mode-specific parameters
        logging: Logging level (debug, discrepancy, sampled_audit)
    """

    field_name: str
    field_type: str
    comparison: Dict[str, Any]
    logging: str

    def __post_init__(self) -> None:
        """Validate configuration after construction."""
        if self.field_type not in VALID_FIELD_TYPES:
            raise InvalidFieldType(
                f"Field {self.field_name}: unknown type {self.field_type!r}. "
                f"Valid types: {', '.join(sorted(VALID_FIELD_TYPES))}"
            )

        if self.logging not in VALID_LOGGING_LEVELS:
            raise InvalidLoggingLevel(
                f"Field {self.field_name}: invalid logging level {self.logging!r}. "
                f"Valid levels: {', '.join(sorted(VALID_LOGGING_LEVELS))}"
            )

        self._validate_comparison_config()

    def _validate_comparison_config(self) -> None:
        """Validate mode and parameters based on field type."""
        if not self.comparison:
            raise InvalidComparisonMode(f"Field {self.field_name}: comparison config is empty")

        mode = self.comparison.get("mode")
        if not mode:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: 'mode' key is required in comparison config"
            )

        # Type-specific validation
        if self.field_type == "list":
            self._validate_list_config(mode)
        elif self.field_type == "numeric":
            self._validate_numeric_config(mode)
        elif self.field_type == "string":
            self._validate_string_config(mode)
        elif self.field_type == "timestamp":
            self._validate_timestamp_config(mode)
        elif self.field_type == "object":
            self._validate_object_config(mode)

    def _validate_list_config(self, mode: str) -> None:
        """Validate list-type comparison config."""
        valid_modes = {"ordered", "unordered_multiset", "set"}
        if mode not in valid_modes:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid list mode {mode!r}. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

        # element_comparison is required for lists
        if "element_comparison" not in self.comparison:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: 'element_comparison' is required for list type"
            )

    def _validate_numeric_config(self, mode: str) -> None:
        """Validate numeric-type comparison config."""
        valid_modes = {"exact_value", "exact_string"}
        # tolerance(N) is a parameterized mode
        is_tolerance = isinstance(mode, str) and mode.startswith("tolerance(")

        if mode not in valid_modes and not is_tolerance:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid numeric mode {mode!r}. "
                f"Valid modes: exact_value, exact_string, tolerance(N)"
            )

        # If tolerance mode, validate the parameter
        if is_tolerance:
            try:
                # Extract N from "tolerance(N)"
                tolerance_str = mode[len("tolerance(") : -1]
                tolerance_val = float(tolerance_str)
                if tolerance_val < 0:
                    raise ValueError("tolerance must be non-negative")
            except (ValueError, IndexError) as e:
                raise InvalidModeParameter(
                    f"Field {self.field_name}: invalid tolerance mode {mode!r}. "
                    f"Expected 'tolerance(N)' where N is a non-negative number. Error: {e}"
                ) from e

    def _validate_string_config(self, mode: str) -> None:
        """Validate string-type comparison config."""
        valid_modes = {"exact", "lowercase", "trim", "normalize"}
        if mode not in valid_modes:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid string mode {mode!r}. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

    def _validate_timestamp_config(self, mode: str) -> None:
        """Validate timestamp-type comparison config."""
        valid_modes = {"string", "semantic_utc", "semantic_resource_tz"}
        if mode not in valid_modes:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid timestamp mode {mode!r}. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

        # Precision is required for semantic modes
        if mode in {"semantic_utc", "semantic_resource_tz"}:
            if "precision" not in self.comparison:
                raise InvalidComparisonMode(
                    f"Field {self.field_name}: 'precision' is required for "
                    f"semantic_utc and semantic_resource_tz modes"
                )

            precision = self.comparison["precision"]
            valid_precisions = {"day", "hour", "minute", "second"}
            if precision not in valid_precisions:
                raise InvalidModeParameter(
                    f"Field {self.field_name}: invalid precision {precision!r}. "
                    f"Valid precisions: {', '.join(sorted(valid_precisions))}"
                )

    def _validate_object_config(self, mode: str) -> None:
        """Validate object-type comparison config."""
        valid_simple_modes = {"opaque", "version", "identity", "ignore"}
        # recurse(N) is a parameterized mode
        is_recurse = isinstance(mode, str) and mode.startswith("recurse(")

        if mode not in valid_simple_modes and not is_recurse:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid object mode {mode!r}. "
                f"Valid modes: opaque, version, identity, ignore, recurse(N)"
            )

        # If recurse mode, validate the parameter
        if is_recurse:
            try:
                # Extract N from "recurse(N)"
                depth_str = mode[len("recurse(") : -1]
                depth_val = int(depth_str)
                if depth_val < -1:
                    raise ValueError("depth must be >= -1")
            except (ValueError, IndexError) as e:
                raise InvalidModeParameter(
                    f"Field {self.field_name}: invalid recurse mode {mode!r}. "
                    f"Expected 'recurse(N)' where N is an integer >= -1. Error: {e}"
                ) from e


class ComparisonSchema:
    """Loads and validates Kind.attribute_schema at reconciliation start.

    Provides FieldConfig objects to comparison functions. All fields must have
    explicit comparison configs; no defaults are inferred.

    Attributes:
        kind_name: Name of the Kind
        fields: Dict mapping field name to FieldConfig
    """

    def __init__(self, kind_name: str, raw_schema: Dict[str, Any]) -> None:
        """Initialize schema from raw Kind.attribute_schema dict.

        Args:
            kind_name: Name of the Kind being configured
            raw_schema: The raw attribute_schema dict from Kind model

        Raises:
            SchemaError: If schema is malformed or missing required configs
        """
        self.kind_name = kind_name
        self.fields: Dict[str, FieldConfig] = {}
        self._validate_and_parse(raw_schema)

    def _validate_and_parse(self, raw_schema: Dict[str, Any]) -> None:
        """Validate and parse the raw schema into FieldConfig objects.

        Args:
            raw_schema: The raw attribute_schema dict

        Raises:
            SchemaError: If validation fails
        """
        if not isinstance(raw_schema, dict):
            raise SchemaError(
                f"Kind {self.kind_name}: attribute_schema must be a dict, "
                f"got {type(raw_schema).__name__}"
            )

        if not raw_schema:
            raise SchemaError(
                f"Kind {self.kind_name}: attribute_schema is empty. "
                f"All fields must have explicit comparison configs."
            )

        for field_name, field_def in raw_schema.items():
            if not isinstance(field_def, dict):
                raise SchemaError(
                    f"Kind {self.kind_name}, field {field_name}: definition must be a dict, "
                    f"got {type(field_def).__name__}"
                )

            # Validate required keys
            if "type" not in field_def:
                raise SchemaError(
                    f"Kind {self.kind_name}, field {field_name}: 'type' key is required"
                )

            if "comparison" not in field_def:
                raise SchemaError(
                    f"Kind {self.kind_name}, field {field_name}: 'comparison' key is required"
                )

            field_type = field_def["type"]
            comparison_config = field_def["comparison"]

            # Default logging level to "discrepancy" if not specified
            logging_level = field_def.get("logging", "discrepancy")

            # Create and validate FieldConfig
            try:
                config = FieldConfig(
                    field_name=field_name,
                    field_type=field_type,
                    comparison=comparison_config,
                    logging=logging_level,
                )
                self.fields[field_name] = config
            except SchemaError:
                # Re-raise with kind context
                raise

    def get_field_config(self, field_name: str) -> FieldConfig:
        """Get comparison config for a field.

        Args:
            field_name: The field to retrieve config for

        Returns:
            FieldConfig for the field

        Raises:
            MissingFieldConfig: If field has no config
        """
        if field_name not in self.fields:
            raise MissingFieldConfig(
                f"Kind {self.kind_name}: no comparison config for field {field_name!r}. "
                f"All fields must have explicit comparison configs."
            )
        return self.fields[field_name]

    def __repr__(self) -> str:
        return f"ComparisonSchema({self.kind_name}, {len(self.fields)} fields)"
