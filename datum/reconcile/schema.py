"""Schema validation and loading for field comparison configurations.

This module validates Kind.attribute_schema and provides FieldConfig objects
to comparison functions. Each field must have an explicit comparison config;
no defaults are inferred.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

# int for recurse(N), float for tolerance(N). Constrained rather than bounded so
# the parser returns the parameter's own type, not a widened one.
_Parameter = TypeVar("_Parameter", int, float)


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


# --- Parameterised modes -----------------------------------------------------
#
# `tolerance(N)` and `recurse(N)` carry a parameter inside their name, so
# something has to read it back out. Two things need to: this module, to reject
# a bad one at the barricade, and `comparison`, to use a good one.
#
# They read it through the same function. A second reading would have to agree
# with the first about what "valid" means, and the two would be free to drift --
# which is exactly what happened before: the barricade checked the range and the
# comparison functions did not check anything, so a parameter that got past
# construction crashed on `float()` instead of degrading (issue #33).
#
# The functions return None rather than raising, because their two callers want
# opposite things from a bad parameter: the validator turns None into
# `InvalidModeParameter`, and the comparison functions turn it into a
# discrepancy. Neither behaviour belongs in the parser.

TOLERANCE_PREFIX = "tolerance("
RECURSE_PREFIX = "recurse("
MODE_PARAMETER_CLOSE = ")"

# The sentinel depth meaning "drill without limit", and the floor of the legal
# range, which is the same number seen from the other side: asking for less than
# "all of it" names no depth at all. Written as an equation rather than as two
# `-1` literals, because two names for one number are free to drift apart and
# the mode's meaning depends on their staying equal.
FULL_RECURSION = -1
MINIMUM_RECURSION_DEPTH = FULL_RECURSION

MINIMUM_TOLERANCE = 0.0


def states_tolerance_mode(mode: Any) -> bool:
    """True for the parameterized tolerance(N) numeric mode."""
    return isinstance(mode, str) and mode.startswith(TOLERANCE_PREFIX)


def states_recursion_mode(mode: Any) -> bool:
    """True for the parameterized recurse(N) object mode."""
    return isinstance(mode, str) and mode.startswith(RECURSE_PREFIX)


def tolerance_of(mode: str) -> float | None:
    """The tolerance in `tolerance(N)`, or None when the mode does not state one.

    None covers every way the parameter can fail to be usable: an unclosed
    mode, an empty parameter, one that is not a number, a non-finite one, and a
    negative one. A negative tolerance parses but cannot be satisfied by any
    pair of values, so it is rejected here rather than silently reported as a
    discrepancy forever.
    """
    return _mode_parameter(mode, TOLERANCE_PREFIX, _finite_float, MINIMUM_TOLERANCE)


def _finite_float(text: str) -> float:
    """`float()`, refusing the two non-finite literals it otherwise accepts.

    `float("inf")` and `float("nan")` both succeed, and both then defeat the
    range check for different reasons: infinity really is larger than the
    minimum, and NaN compares false against everything, so `nan < minimum` is
    False. Raising here rather than testing the parsed value afterwards keeps
    one question in one place -- "is this text a tolerance I can use" -- and
    `_mode_parameter` already turns a `ValueError` from the parse into "states
    no usable parameter".

    `inf` is the one that has to be refused. `abs(d - c) <= inf` holds for every
    pair of values, so `tolerance(inf)` silently reports agreement on a field
    nothing ever compared, which is `ignore` under a name that does not say so.

    Deliberately not shared with `recurse(N)`, which parses with `int()`. Every
    Python int is finite, so there is nothing there to refuse -- and
    `math.isfinite` is not even total over them, because it converts to a C
    double first and raises `OverflowError` on an int too large to fit. A
    universal finiteness check in the shared parser therefore crashed the
    barricade on `recurse(N)` at 309 digits where 308 returned normally: a
    parameter that parses and then raises instead of degrading, which is the
    issue #33 class reintroduced one path over.
    """
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a finite number")
    return value


def recursion_depth_of(mode: str) -> int | None:
    """The depth in `recurse(N)`, or None when the mode does not state one.

    `-1` means full recursion and is the smallest legal depth; anything below
    it names no depth at all.
    """
    return _mode_parameter(mode, RECURSE_PREFIX, int, MINIMUM_RECURSION_DEPTH)


def _mode_parameter(
    mode: str,
    prefix: str,
    parse: Callable[[str], _Parameter],
    minimum: _Parameter,
) -> _Parameter | None:
    """One parameterised mode's parameter, or None when it states none usable.

    Closing parenthesis checked rather than assumed: `mode[len(prefix):-1]`
    drops the last character whatever it is, so an unclosed `tolerance(5`
    would otherwise be read as `tolerance(` plus a silently truncated `5`.

    What counts as unusable beyond the range check is the *parse function's*
    business, not this one's, and `_finite_float` says why: the two parameters
    have different notions of a usable value, and a single predicate that suits
    one of them is wrong for the other rather than shared between them.
    """
    if not mode.endswith(MODE_PARAMETER_CLOSE):
        return None
    try:
        parameter = parse(mode[len(prefix) : -len(MODE_PARAMETER_CLOSE)])
    except ValueError:
        return None
    if parameter < minimum:
        return None
    return parameter


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
    comparison: dict[str, Any]
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

        # A table rather than an if-chain, per the construction conventions:
        # repeated branching on one value is a lookup. It also removes the
        # fall-through an if/elif chain leaves behind -- unreachable, because
        # __post_init__ has already rejected any type not in VALID_FIELD_TYPES,
        # but still a branch the 100% gate counts against the kernel.
        validators: dict[str, Callable[[str], None]] = {
            "list": self._validate_list_config,
            "numeric": self._validate_numeric_config,
            "string": self._validate_string_config,
            "timestamp": self._validate_timestamp_config,
            "object": self._validate_object_config,
        }
        validators[self.field_type](mode)

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
        is_tolerance = states_tolerance_mode(mode)

        if mode not in valid_modes and not is_tolerance:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid numeric mode {mode!r}. "
                f"Valid modes: exact_value, exact_string, tolerance(N)"
            )

        if is_tolerance and tolerance_of(mode) is None:
            raise InvalidModeParameter(
                f"Field {self.field_name}: invalid tolerance mode {mode!r}. "
                f"Expected 'tolerance(N)' where N is a finite, non-negative number."
            )

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
        is_recurse = states_recursion_mode(mode)

        if mode not in valid_simple_modes and not is_recurse:
            raise InvalidComparisonMode(
                f"Field {self.field_name}: invalid object mode {mode!r}. "
                f"Valid modes: opaque, version, identity, ignore, recurse(N)"
            )

        if is_recurse and recursion_depth_of(mode) is None:
            raise InvalidModeParameter(
                f"Field {self.field_name}: invalid recurse mode {mode!r}. "
                f"Expected 'recurse(N)' where N is an integer >= -1."
            )


class ComparisonSchema:
    """Loads and validates Kind.attribute_schema at reconciliation start.

    Provides FieldConfig objects to comparison functions. All fields must have
    explicit comparison configs; no defaults are inferred.

    Attributes:
        kind_name: Name of the Kind
        fields: Dict mapping field name to FieldConfig
    """

    def __init__(self, kind_name: str, raw_schema: dict[str, Any]) -> None:
        """Initialize schema from raw Kind.attribute_schema dict.

        Args:
            kind_name: Name of the Kind being configured
            raw_schema: The raw attribute_schema dict from Kind model

        Raises:
            SchemaError: If schema is malformed or missing required configs
        """
        self.kind_name = kind_name
        self.fields: dict[str, FieldConfig] = {}
        self._validate_and_parse(raw_schema)

    def _validate_and_parse(self, raw_schema: dict[str, Any]) -> None:
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
