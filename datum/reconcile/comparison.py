"""Field comparison functions using schema-aware, type-specific logic.

This module implements comparison handlers for each field type defined in
Kind.attribute_schema. Each function:

1. Takes declared and discovered PlaneValue objects
2. Applies mode-specific transformation/comparison logic
3. Returns (is_equal, audit_log_entry) tuple

The audit_log_entry captures the decision trail for debugging and auditing.
"""

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


@dataclass(frozen=True)
class AuditLogEntry:
    """Record of one field comparison decision.

    Attributes:
        kind_name: Name of the Kind
        field_name: Name of the field being compared
        field_type: Type of field (numeric, string, list, timestamp, object)
        comparison_mode: Mode used for comparison
        declared_raw: Raw value from declared plane (before transformation)
        declared_transformed: Value after mode-specific transformation
        discovered_raw: Raw value from discovered plane (before transformation)
        discovered_transformed: Value after mode-specific transformation
        result: True if comparison matched, False if discrepancy
        steps: List of descriptive steps taken during comparison
    """

    kind_name: str
    field_name: str
    field_type: str
    comparison_mode: str
    declared_raw: Any
    declared_transformed: Any
    discovered_raw: Any
    discovered_transformed: Any
    result: bool
    steps: list[str]


def compare_numeric(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare numeric fields using mode-specific logic.

    Modes:
        - exact_value: Canonical form comparison (3 == 3.0)
        - exact_string: String representation must match ("3" != "3.0")
        - tolerance(N): Absolute difference must be <= N

    Args:
        declared: Value from declared plane
        discovered: Value from discovered plane
        field_config: Configuration with mode and parameters

    Returns:
        (is_equal, audit_log_entry) tuple

    Raises:
        ValueError: If values cannot be parsed as numbers in tolerance mode
    """
    mode: Any = field_config.comparison.get("mode")
    kind_name = field_config.comparison.get("_kind_name", "unknown")
    steps: list[str] = []

    # Handle absent/null cases
    def get_value(plane_val: PlaneValue) -> Any:
        return plane_val.resolve(
            on_absent=lambda: None,
            on_present=lambda v: v,
        )

    declared_val = get_value(declared)
    discovered_val = get_value(discovered)

    # If either is None/absent, they must both be None/absent to match
    if declared_val is None or discovered_val is None:
        is_equal = declared_val == discovered_val
        steps.append(f"Null/absent handling: declared={declared_val}, discovered={discovered_val}")
        return (
            is_equal,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="numeric",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=is_equal,
                steps=steps,
            ),
        )

    # Mode-specific comparison
    if mode == "exact_value":
        is_equal = _compare_exact_value(declared_val, discovered_val, steps)
    elif mode == "exact_string":
        is_equal = _compare_exact_string(declared_val, discovered_val, steps)
    elif isinstance(mode, str) and mode.startswith("tolerance("):
        tolerance = float(mode[len("tolerance(") : -1])
        is_equal = _compare_tolerance(declared_val, discovered_val, tolerance, steps)
    else:
        steps.append(f"Unknown mode: {mode}")
        is_equal = False

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="numeric",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=declared_val,
            discovered_raw=discovered_val,
            discovered_transformed=discovered_val,
            result=is_equal,
            steps=steps,
        ),
    )


def _compare_exact_value(
    declared_val: Any,
    discovered_val: Any,
    steps: list[str],
) -> bool:
    """Compare numeric values using canonical (JSON) form.

    This allows 3 == 3.0 to be considered equal, as they have the same
    canonical representation. Type coercion is allowed.

    Args:
        declared_val: Declared plane value
        discovered_val: Discovered plane value
        steps: List to append comparison steps to

    Returns:
        True if canonical forms match
    """
    from datum.reconcile.domain import canonical

    declared_canonical = canonical(declared_val)
    discovered_canonical = canonical(discovered_val)

    steps.append("Mode: exact_value")
    steps.append(f"Declared canonical: {declared_canonical}")
    steps.append(f"Discovered canonical: {discovered_canonical}")

    is_equal = declared_canonical == discovered_canonical
    steps.append(f"Result: {is_equal}")

    return is_equal


def _compare_exact_string(
    declared_val: Any,
    discovered_val: Any,
    steps: list[str],
) -> bool:
    """Compare numeric values using string representation.

    This requires the string forms to match exactly: 3 != 3.0.
    Useful when the distinction between int and float carries meaning.

    Args:
        declared_val: Declared plane value
        discovered_val: Discovered plane value
        steps: List to append comparison steps to

    Returns:
        True if string representations match
    """
    declared_str = str(declared_val)
    discovered_str = str(discovered_val)

    steps.append("Mode: exact_string")
    steps.append(f"Declared string: {declared_str}")
    steps.append(f"Discovered string: {discovered_str}")

    is_equal = declared_str == discovered_str
    steps.append(f"Result: {is_equal}")

    return is_equal


def _compare_tolerance(
    declared_val: Any,
    discovered_val: Any,
    tolerance: float,
    steps: list[str],
) -> bool:
    """Compare numeric values allowing for tolerance.

    Requires both values to be numeric (int or float). Calculates absolute
    difference and compares to tolerance threshold.

    Args:
        declared_val: Declared plane value
        discovered_val: Discovered plane value
        tolerance: Maximum allowed absolute difference
        steps: List to append comparison steps to

    Returns:
        True if |declared - discovered| <= tolerance

    Raises:
        ValueError: If values cannot be converted to numbers
    """
    try:
        declared_num = float(declared_val)
        discovered_num = float(discovered_val)
    except (ValueError, TypeError) as e:
        steps.append(f"Error converting to float: {e}")
        return False

    difference = abs(declared_num - discovered_num)

    steps.append(f"Mode: tolerance({tolerance})")
    steps.append(f"Declared: {declared_num}")
    steps.append(f"Discovered: {discovered_num}")
    steps.append(f"Absolute difference: {difference}")
    steps.append(f"Tolerance threshold: {tolerance}")

    is_equal = difference <= tolerance
    steps.append(f"Result: {is_equal}")

    return is_equal


def compare_string(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare string fields using mode-specific logic.

    Modes:
        - exact: No transformations, compare as-is
        - lowercase: Convert both sides to lowercase before comparing
        - trim: Strip leading/trailing whitespace from both sides
        - normalize: lowercase + trim + collapse internal whitespace

    Args:
        declared: Value from declared plane
        discovered: Value from discovered plane
        field_config: Configuration with mode and parameters

    Returns:
        (is_equal, audit_log_entry) tuple
    """
    mode: Any = field_config.comparison.get("mode")
    kind_name = field_config.comparison.get("_kind_name", "unknown")
    steps: list[str] = []

    # Handle absent/null cases
    def get_value(plane_val: PlaneValue) -> Any:
        return plane_val.resolve(
            on_absent=lambda: None,
            on_present=lambda v: v,
        )

    declared_val = get_value(declared)
    discovered_val = get_value(discovered)

    # If either is None/absent, they must both be None/absent to match
    if declared_val is None or discovered_val is None:
        is_equal = declared_val == discovered_val
        steps.append(f"Null/absent handling: declared={declared_val}, discovered={discovered_val}")
        return (
            is_equal,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="string",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=is_equal,
                steps=steps,
            ),
        )

    # Convert to string if not already
    declared_str = str(declared_val)
    discovered_str = str(discovered_val)

    # Mode-specific comparison
    if mode == "exact":
        declared_transformed = declared_str
        discovered_transformed = discovered_str
        is_equal = _compare_exact_string_mode(declared_transformed, discovered_transformed, steps)
    elif mode == "lowercase":
        declared_transformed = declared_str.lower()
        discovered_transformed = discovered_str.lower()
        is_equal = _compare_lowercase(
            declared_str, declared_transformed, discovered_str, discovered_transformed, steps
        )
    elif mode == "trim":
        declared_transformed = declared_str.strip()
        discovered_transformed = discovered_str.strip()
        is_equal = _compare_trim(
            declared_str, declared_transformed, discovered_str, discovered_transformed, steps
        )
    elif mode == "normalize":
        declared_transformed = _normalize_string(declared_str)
        discovered_transformed = _normalize_string(discovered_str)
        is_equal = _compare_normalize(
            declared_str, declared_transformed, discovered_str, discovered_transformed, steps
        )
    else:
        steps.append(f"Unknown mode: {mode}")
        is_equal = False
        declared_transformed = declared_str
        discovered_transformed = discovered_str

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="string",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=declared_transformed,
            discovered_raw=discovered_val,
            discovered_transformed=discovered_transformed,
            result=is_equal,
            steps=steps,
        ),
    )


def _compare_exact_string_mode(
    declared_str: str,
    discovered_str: str,
    steps: list[str],
) -> bool:
    """Compare strings with no transformation.

    Args:
        declared_str: Declared plane string
        discovered_str: Discovered plane string
        steps: List to append comparison steps to

    Returns:
        True if strings match exactly
    """
    steps.append("Mode: exact")
    steps.append(f"Declared: {declared_str!r}")
    steps.append(f"Discovered: {discovered_str!r}")

    is_equal = declared_str == discovered_str
    steps.append(f"Result: {is_equal}")

    return is_equal


def _compare_lowercase(
    declared_raw: str,
    declared_transformed: str,
    discovered_raw: str,
    discovered_transformed: str,
    steps: list[str],
) -> bool:
    """Compare strings after converting to lowercase.

    Args:
        declared_raw: Raw declared value
        declared_transformed: Lowercase declared value
        discovered_raw: Raw discovered value
        discovered_transformed: Lowercase discovered value
        steps: List to append comparison steps to

    Returns:
        True if lowercase versions match
    """
    steps.append("Mode: lowercase")
    steps.append(f"Declared raw: {declared_raw!r}")
    steps.append(f"Declared transformed: {declared_transformed!r}")
    steps.append(f"Discovered raw: {discovered_raw!r}")
    steps.append(f"Discovered transformed: {discovered_transformed!r}")

    is_equal = declared_transformed == discovered_transformed
    steps.append(f"Result: {is_equal}")

    return is_equal


def _compare_trim(
    declared_raw: str,
    declared_transformed: str,
    discovered_raw: str,
    discovered_transformed: str,
    steps: list[str],
) -> bool:
    """Compare strings after stripping whitespace.

    Args:
        declared_raw: Raw declared value
        declared_transformed: Trimmed declared value
        discovered_raw: Raw discovered value
        discovered_transformed: Trimmed discovered value
        steps: List to append comparison steps to

    Returns:
        True if trimmed versions match
    """
    steps.append("Mode: trim")
    steps.append(f"Declared raw: {declared_raw!r}")
    steps.append(f"Declared transformed: {declared_transformed!r}")
    steps.append(f"Discovered raw: {discovered_raw!r}")
    steps.append(f"Discovered transformed: {discovered_transformed!r}")

    is_equal = declared_transformed == discovered_transformed
    steps.append(f"Result: {is_equal}")

    return is_equal


def _normalize_string(text: str) -> str:
    """Normalize string: lowercase + trim + collapse internal whitespace.

    Args:
        text: String to normalize

    Returns:
        Normalized string
    """
    # lowercase + trim + collapse internal whitespace
    return " ".join(text.lower().split())


def _compare_normalize(
    declared_raw: str,
    declared_transformed: str,
    discovered_raw: str,
    discovered_transformed: str,
    steps: list[str],
) -> bool:
    """Compare strings after normalization.

    Normalization: lowercase + trim + collapse internal whitespace.

    Args:
        declared_raw: Raw declared value
        declared_transformed: Normalized declared value
        discovered_raw: Raw discovered value
        discovered_transformed: Normalized discovered value
        steps: List to append comparison steps to

    Returns:
        True if normalized versions match
    """
    steps.append("Mode: normalize")
    steps.append(f"Declared raw: {declared_raw!r}")
    steps.append(f"Declared transformed: {declared_transformed!r}")
    steps.append(f"Discovered raw: {discovered_raw!r}")
    steps.append(f"Discovered transformed: {discovered_transformed!r}")

    is_equal = declared_transformed == discovered_transformed
    steps.append(f"Result: {is_equal}")

    return is_equal


def compare_list(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare list fields using mode-specific logic.

    Modes:
        - ordered: Lists match only if elements are in same order
        - unordered_multiset: Lists match if same elements with same counts (order ignored)
        - set: Lists match if same unique elements (duplicates ignored, order ignored)

    Args:
        declared: Value from declared plane
        discovered: Value from discovered plane
        field_config: Configuration with mode and parameters

    Returns:
        (is_equal, audit_log_entry) tuple
    """
    mode: Any = field_config.comparison.get("mode")
    kind_name = field_config.comparison.get("_kind_name", "unknown")
    steps: list[str] = []

    # Handle absent/null cases
    def get_value(plane_val: PlaneValue) -> Any:
        return plane_val.resolve(
            on_absent=lambda: None,
            on_present=lambda v: v,
        )

    declared_val = get_value(declared)
    discovered_val = get_value(discovered)

    # If either is None/absent, they must both be None/absent to match
    if declared_val is None or discovered_val is None:
        is_equal = declared_val == discovered_val
        steps.append(f"Null/absent handling: declared={declared_val}, discovered={discovered_val}")
        return (
            is_equal,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="list",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=is_equal,
                steps=steps,
            ),
        )

    # Ensure values are lists
    if not isinstance(declared_val, list):
        steps.append(f"Declared value is not a list: {type(declared_val).__name__}")
        return (
            False,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="list",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=False,
                steps=steps,
            ),
        )

    if not isinstance(discovered_val, list):
        steps.append(f"Discovered value is not a list: {type(discovered_val).__name__}")
        return (
            False,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="list",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=False,
                steps=steps,
            ),
        )

    # Mode-specific comparison
    if mode == "ordered":
        is_equal = _compare_ordered_list(declared_val, discovered_val, steps)
        transformed_declared = declared_val
        transformed_discovered = discovered_val
    elif mode == "unordered_multiset":
        transformed_declared = sorted(declared_val, key=_list_sort_key)
        transformed_discovered = sorted(discovered_val, key=_list_sort_key)
        is_equal = _compare_multiset(
            declared_val, discovered_val, transformed_declared, transformed_discovered, steps
        )
    elif mode == "set":
        transformed_declared = sorted(
            set(_make_hashable(v) for v in declared_val), key=_list_sort_key
        )
        transformed_discovered = sorted(
            set(_make_hashable(v) for v in discovered_val), key=_list_sort_key
        )
        is_equal = _compare_set(
            declared_val,
            discovered_val,
            list(transformed_declared),
            list(transformed_discovered),
            steps,
        )
    else:
        steps.append(f"Unknown mode: {mode}")
        is_equal = False
        transformed_declared = declared_val
        transformed_discovered = discovered_val

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="list",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=transformed_declared,
            discovered_raw=discovered_val,
            discovered_transformed=transformed_discovered,
            result=is_equal,
            steps=steps,
        ),
    )


def _make_hashable(val: Any) -> Any:
    """Convert a value to a hashable form for use in sets.

    Args:
        val: Value to convert

    Returns:
        Hashable representation of the value
    """
    if isinstance(val, dict):
        return tuple(sorted(val.items()))
    elif isinstance(val, list):
        return tuple(_make_hashable(v) for v in val)
    elif isinstance(val, set):
        return frozenset(_make_hashable(v) for v in val)
    else:
        return val


def _list_sort_key(val: Any) -> tuple:
    """Create a sort key for list elements (handles mixed types).

    Args:
        val: Value to create sort key for

    Returns:
        Tuple (type_order, canonical_form) for sorting
    """
    from datum.reconcile.domain import canonical

    type_order = {
        type(None): 0,
        bool: 1,
        int: 2,
        float: 3,
        str: 4,
        list: 5,
        dict: 6,
    }
    type_key = type_order.get(type(val), 99)
    return (type_key, canonical(val))


def _compare_ordered_list(
    declared: list,
    discovered: list,
    steps: list[str],
) -> bool:
    """Compare lists in order.

    Args:
        declared: Declared list
        discovered: Discovered list
        steps: List to append comparison steps to

    Returns:
        True if lists are equal and in same order
    """
    from datum.reconcile.domain import canonical

    steps.append("Mode: ordered")
    steps.append(f"Declared: {declared}")
    steps.append(f"Discovered: {discovered}")

    is_equal = len(declared) == len(discovered)
    if is_equal:
        for i, (d, disc) in enumerate(zip(declared, discovered, strict=False)):
            if canonical(d) != canonical(disc):
                is_equal = False
                steps.append(f"Mismatch at index {i}: {d!r} != {disc!r}")
                break

    steps.append(f"Result: {is_equal}")
    return is_equal


def _compare_multiset(
    declared: list,
    discovered: list,
    declared_sorted: list,
    discovered_sorted: list,
    steps: list[str],
) -> bool:
    """Compare lists as multisets (order independent, duplicates matter).

    Args:
        declared: Raw declared list
        discovered: Raw discovered list
        declared_sorted: Sorted declared list
        discovered_sorted: Sorted discovered list
        steps: List to append comparison steps to

    Returns:
        True if lists have same elements with same counts (order ignored)
    """
    from datum.reconcile.domain import canonical

    steps.append("Mode: unordered_multiset")
    steps.append(f"Declared raw: {declared}")
    steps.append(f"Discovered raw: {discovered}")
    steps.append(f"Declared sorted: {declared_sorted}")
    steps.append(f"Discovered sorted: {discovered_sorted}")

    is_equal = len(declared_sorted) == len(discovered_sorted)
    if is_equal:
        for d, disc in zip(declared_sorted, discovered_sorted, strict=False):
            if canonical(d) != canonical(disc):
                is_equal = False
                break

    steps.append(f"Result: {is_equal}")
    return is_equal


def _compare_set(
    declared: list,
    discovered: list,
    declared_set: list,
    discovered_set: list,
    steps: list[str],
) -> bool:
    """Compare lists as sets (order and duplicates ignored).

    Args:
        declared: Raw declared list
        discovered: Raw discovered list
        declared_set: Set as sorted list (unique elements)
        discovered_set: Set as sorted list (unique elements)
        steps: List to append comparison steps to

    Returns:
        True if lists have same unique elements (order and duplicates ignored)
    """
    from datum.reconcile.domain import canonical

    steps.append("Mode: set")
    steps.append(f"Declared raw: {declared}")
    steps.append(f"Discovered raw: {discovered}")
    steps.append(f"Declared as set: {declared_set}")
    steps.append(f"Discovered as set: {discovered_set}")

    is_equal = len(declared_set) == len(discovered_set)
    if is_equal:
        for d, disc in zip(declared_set, discovered_set, strict=False):
            if canonical(d) != canonical(disc):
                is_equal = False
                break

    steps.append(f"Result: {is_equal}")
    return is_equal


def _compare_semantic_timestamp(
    declared_val: Any,
    discovered_val: Any,
    precision: str,
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare timestamps in semantic mode (parse, convert to UTC, truncate).

    Args:
        declared_val: Timestamp value from declared plane
        discovered_val: Timestamp value from discovered plane
        precision: Precision level (day, hour, minute, second)
        steps: List to append comparison steps to

    Returns:
        (is_equal, transformed_declared, transformed_discovered) tuple
    """
    from dateutil import parser as dateutil_parser

    try:
        declared_dt = dateutil_parser.parse(str(declared_val))
        discovered_dt = dateutil_parser.parse(str(discovered_val))

        # Convert to UTC
        if declared_dt.tzinfo is None:
            declared_dt = declared_dt.replace(tzinfo=UTC)
        else:
            declared_dt = declared_dt.astimezone(UTC)

        if discovered_dt.tzinfo is None:
            discovered_dt = discovered_dt.replace(tzinfo=UTC)
        else:
            discovered_dt = discovered_dt.astimezone(UTC)

        # Truncate to precision level
        if precision == "day":
            declared_cmp = declared_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            discovered_cmp = discovered_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        elif precision == "hour":
            declared_cmp = declared_dt.replace(minute=0, second=0, microsecond=0)
            discovered_cmp = discovered_dt.replace(minute=0, second=0, microsecond=0)
        elif precision == "minute":
            declared_cmp = declared_dt.replace(second=0, microsecond=0)
            discovered_cmp = discovered_dt.replace(second=0, microsecond=0)
        else:  # second
            declared_cmp = declared_dt.replace(microsecond=0)
            discovered_cmp = discovered_dt.replace(microsecond=0)

        is_equal = declared_cmp == discovered_cmp
        transformed_declared = str(declared_cmp)
        transformed_discovered = str(discovered_cmp)
        return (is_equal, transformed_declared, transformed_discovered)
    except Exception as e:
        steps.append(f"Error parsing timestamp: {e}")
        return (False, str(declared_val), str(discovered_val))


def compare_timestamp(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare timestamp fields using mode-specific logic.

    Modes:
        - string: Exact string matching (ignore timezone representation differences)
        - semantic_utc: Parse timestamps, convert to UTC, compare at precision level
        - semantic_resource_tz: Parse timestamps, compare in resource timezone at precision

    Precision levels: day, hour, minute, second

    Args:
        declared: Value from declared plane
        discovered: Value from discovered plane
        field_config: Configuration with mode and parameters

    Returns:
        (is_equal, audit_log_entry) tuple
    """
    mode: Any = field_config.comparison.get("mode")
    kind_name = field_config.comparison.get("_kind_name", "unknown")
    precision: Any = field_config.comparison.get("precision", "second")
    steps: list[str] = []

    # Handle absent/null cases
    def get_value(plane_val: PlaneValue) -> Any:
        return plane_val.resolve(
            on_absent=lambda: None,
            on_present=lambda v: v,
        )

    declared_val = get_value(declared)
    discovered_val = get_value(discovered)

    # If either is None/absent, they must both be None/absent to match
    if declared_val is None or discovered_val is None:
        is_equal = declared_val == discovered_val
        steps.append(f"Null/absent handling: declared={declared_val}, discovered={discovered_val}")
        return (
            is_equal,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="timestamp",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=declared_val,
                discovered_raw=discovered_val,
                discovered_transformed=discovered_val,
                result=is_equal,
                steps=steps,
            ),
        )

    # Mode-specific comparison
    if mode == "string":
        is_equal = str(declared_val) == str(discovered_val)
        transformed_declared = str(declared_val)
        transformed_discovered = str(discovered_val)
        steps.append("Mode: string")
        steps.append(f"Declared: {transformed_declared!r}")
        steps.append(f"Discovered: {transformed_discovered!r}")
        steps.append(f"Result: {is_equal}")
    elif mode in ("semantic_utc", "semantic_resource_tz"):
        is_equal, transformed_declared, transformed_discovered = _compare_semantic_timestamp(
            declared_val, discovered_val, precision, steps
        )
        steps.append(f"Mode: {mode}")
        steps.append(f"Precision: {precision}")
        steps.append(f"Declared UTC: {transformed_declared}")
        steps.append(f"Discovered UTC: {transformed_discovered}")
        steps.append(f"Result: {is_equal}")
    else:
        steps.append(f"Unknown mode: {mode}")
        is_equal = False
        transformed_declared = str(declared_val)
        transformed_discovered = str(discovered_val)

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="timestamp",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=transformed_declared,
            discovered_raw=discovered_val,
            discovered_transformed=transformed_discovered,
            result=is_equal,
            steps=steps,
        ),
    )
