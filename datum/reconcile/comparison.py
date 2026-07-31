"""Field comparison functions using schema-aware, type-specific logic.

This module implements comparison handlers for each field type defined in
Kind.attribute_schema. Each function:

1. Takes declared and discovered PlaneValue objects
2. Applies mode-specific transformation/comparison logic
3. Returns (is_equal, audit_log_entry) tuple

The audit_log_entry captures the decision trail for debugging and auditing.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from datum.reconcile.domain import PlaneValue, canonical
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
        - ordered: Position matters (like Python lists)
        - unordered_multiset: Order ignored, duplicates matter (like Counter)
        - set: Order and duplicates ignored (like set)

    Args:
        declared: Value from declared plane
        discovered: Value from discovered plane
        field_config: Configuration with mode and parameters

    Returns:
        (is_equal, audit_log_entry) tuple
    """
    from datum.reconcile.domain import canonical

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

    # Both must be lists
    if not isinstance(declared_val, list) or not isinstance(discovered_val, list):
        is_equal = False
        steps.append(
            f"Type check failed: declared is {type(declared_val).__name__}, "
            f"discovered is {type(discovered_val).__name__}"
        )
        return (
            is_equal,
            AuditLogEntry(
                kind_name=kind_name,
                field_name=field_config.field_name,
                field_type="list",
                comparison_mode=mode,
                declared_raw=declared_val,
                declared_transformed=str(declared_val),
                discovered_raw=discovered_val,
                discovered_transformed=str(discovered_val),
                result=is_equal,
                steps=steps,
            ),
        )

    # Mode-specific comparison
    def list_sort_key(item: Any) -> tuple[int, str]:
        """Sort key for mixed-type list elements."""
        type_order = {
            type(None): 0,
            bool: 1,
            int: 2,
            float: 3,
            str: 4,
            list: 5,
            dict: 6,
        }
        item_type = type(item)
        order = type_order.get(item_type, 99)
        return (order, canonical(item))

    if mode == "ordered":
        is_equal = _compare_ordered_list(declared_val, discovered_val, steps)
    elif mode == "unordered_multiset":
        declared_sorted = sorted(declared_val, key=list_sort_key)
        discovered_sorted = sorted(discovered_val, key=list_sort_key)
        is_equal = _compare_multiset(
            declared_val, discovered_val, declared_sorted, discovered_sorted, steps
        )
    elif mode == "set":
        declared_set = sorted(
            list({_make_hashable(item): item for item in declared_val}.values()),
            key=list_sort_key,
        )
        discovered_set = sorted(
            list({_make_hashable(item): item for item in discovered_val}.values()),
            key=list_sort_key,
        )
        is_equal = _compare_set(declared_val, discovered_val, declared_set, discovered_set, steps)
    else:
        is_equal = False
        steps.append(f"Unknown mode: {mode}")

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="list",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=str(declared_val),
            discovered_raw=discovered_val,
            discovered_transformed=str(discovered_val),
            result=is_equal,
            steps=steps,
        ),
    )


def _make_hashable(item: Any) -> Any:
    """Convert item to hashable form for set deduplication.

    Args:
        item: Item to convert

    Returns:
        Hashable representation of item
    """
    if isinstance(item, dict):
        return tuple(sorted(item.items()))
    elif isinstance(item, list):
        return tuple(_make_hashable(i) for i in item)
    else:
        return item


def _compare_ordered_list(
    declared: list[Any],
    discovered: list[Any],
    steps: list[str],
) -> bool:
    """Compare lists in ordered mode (position matters).

    Args:
        declared: Raw declared list
        discovered: Raw discovered list
        steps: List to append comparison steps to

    Returns:
        True if lists have same elements in same order
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
    declared: list[Any],
    discovered: list[Any],
    declared_sorted: list[Any],
    discovered_sorted: list[Any],
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
    declared: list[Any],
    discovered: list[Any],
    declared_set: list[Any],
    discovered_set: list[Any],
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
        - string: Exact string matching (no parsing or timezone conversion)
        - semantic_utc: Parse timestamps, convert to UTC, compare at precision level
        - semantic_resource_tz: Parse timestamps, compare in resource timezone

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


# --- Object comparison (WBS 1.5.2 Phase 2F) ----------------------------------
#
# DIFF_SEMANTICS.md section 5. Five modes, four of them fixed and one
# parameterized, so the fixed four are a table rather than an if-chain: adding
# a mode is a row, and no branch can be reached that the table does not name.

OBJECT_MODE_IGNORE = "ignore"
OBJECT_VERSION_KEY = "version"
OBJECT_IDENTITY_KEY = "id"
RECURSE_PREFIX = "recurse("
FULL_RECURSION = -1

# What each plane said about a field, for the cases where at least one side is
# not a comparable object. These are the plane's *statement*, not its value:
# absence and null are two statements, and collapsing them is the defect
# PlaneValue exists to prevent (DESIGN section 13).
STATEMENT_ABSENT = "absent"
STATEMENT_NULL = "null"
STATEMENT_OBJECT = "object"

# Two planes agree only when they made the same statement AND that statement is
# one a comparison can affirm. Two sides that both hold a non-object under an
# object config agree by accident, not by comparison: the configured semantics
# never ran, so the result is a discrepancy rather than a match. Same rule
# compare_list applies to a non-list.
AFFIRMABLE_STATEMENTS = frozenset({STATEMENT_ABSENT, STATEMENT_NULL})

ROOT_PATH = "<root>"
IGNORED_MARKER = "<ignored>"
MISSING_KEY_MARKER = "<missing>"


def compare_object(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare object fields using mode-specific logic.

    Modes:
        - opaque: Structure-preserving hash of the whole object; no drilling
        - version: Compare the `version` key only, as strings
        - identity: Compare the `id` key only, as strings
        - ignore: Always matches; never produces a discrepancy
        - recurse(depth): Drill `depth` levels, then compare what is left
          opaquely. recurse(0) is opaque, recurse(-1) is fully recursive.

    Key order never affects the result, in any mode and at any depth.

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

    declared_state = _presence_and_value(declared)
    discovered_state = _presence_and_value(discovered)

    is_equal, declared_transformed, discovered_transformed = _object_comparison(
        mode, declared_state, discovered_state, steps
    )

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="object",
            comparison_mode=mode,
            declared_raw=declared_state[1],
            declared_transformed=declared_transformed,
            discovered_raw=discovered_state[1],
            discovered_transformed=discovered_transformed,
            result=is_equal,
            steps=steps,
        ),
    )


def _presence_and_value(plane_val: PlaneValue) -> tuple[bool, Any]:
    """Split a plane's statement into whether it spoke and what it said.

    Keeps presence alongside the value rather than folding absence into None,
    so `missing` and `null` stay two facts all the way into the comparison.
    """
    return plane_val.resolve(
        on_absent=lambda: (False, None),
        on_present=lambda v: (True, v),
    )


def _object_comparison(
    mode: Any,
    declared_state: tuple[bool, Any],
    discovered_state: tuple[bool, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Route one object comparison to its mode, returning result and audit forms.

    Args:
        mode: Comparison mode from the field config
        declared_state: (present, value) for the declared plane
        discovered_state: (present, value) for the discovered plane
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_transformed, discovered_transformed) tuple
    """
    if mode == OBJECT_MODE_IGNORE:
        return _ignored_comparison(steps)

    if not _is_stated_object(*declared_state) or not _is_stated_object(*discovered_state):
        return _unstated_comparison(declared_state, discovered_state, steps)

    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    handler = OBJECT_MODE_HANDLERS.get(mode)
    if handler is not None:
        return handler(declared_val, discovered_val, steps)

    if _is_recursion_mode(mode):
        depth = int(mode[len(RECURSE_PREFIX) : -1])
        return _compare_recursively(declared_val, discovered_val, depth, steps)

    steps.append(f"Unknown mode: {mode}")
    return (False, canonical(declared_val), canonical(discovered_val))


def _is_stated_object(present: bool, value: Any) -> bool:
    """True when a plane both mentioned the field and gave it an object value."""
    return present and isinstance(value, dict)


def _is_recursion_mode(mode: Any) -> bool:
    """True for the parameterized recurse(N) mode."""
    return isinstance(mode, str) and mode.startswith(RECURSE_PREFIX)


def _plane_statement(present: bool, value: Any) -> str:
    """Name what one plane said about the field, presence included."""
    if not present:
        return STATEMENT_ABSENT
    if value is None:
        return STATEMENT_NULL
    if isinstance(value, dict):
        return STATEMENT_OBJECT
    return f"non-object:{type(value).__name__}"


def _ignored_comparison(steps: list[str]) -> tuple[bool, str, str]:
    """Report a match without reading either side.

    `ignore` short-circuits ahead of presence and type handling on purpose: a
    field configured as ignored must never produce a discrepancy, including
    when one plane omits it entirely.
    """
    steps.append("Mode: ignore")
    steps.append("Result: True (field ignored; never a discrepancy)")
    return (True, IGNORED_MARKER, IGNORED_MARKER)


def _unstated_comparison(
    declared_state: tuple[bool, Any],
    discovered_state: tuple[bool, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two planes when at least one did not state a comparable object.

    Agreement here is agreement on the *statement*: both absent, or both null.
    Absent against null is a discrepancy, because field absence and an explicit
    null are different claims (DIFF_SEMANTICS.md, Null / Missing / Empty).

    Args:
        declared_state: (present, value) for the declared plane
        discovered_state: (present, value) for the discovered plane
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_transformed, discovered_transformed) tuple
    """
    declared_statement = _plane_statement(*declared_state)
    discovered_statement = _plane_statement(*discovered_state)

    steps.append(f"Declared statement: {declared_statement}")
    steps.append(f"Discovered statement: {discovered_statement}")

    is_equal = (
        declared_statement == discovered_statement and declared_statement in AFFIRMABLE_STATEMENTS
    )
    steps.append(f"Result: {is_equal}")

    return (is_equal, declared_statement, discovered_statement)


def _structure_hash(value: Any) -> str:
    """Structure-preserving hash of a value.

    Built on `canonical`, which sorts keys at every level, so key order cannot
    change the hash and a change in structure or content always can.
    """
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _compare_opaque(
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two objects as opaque blobs, by structure-preserving hash.

    Args:
        declared_val: Declared plane object
        discovered_val: Discovered plane object
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_hash, discovered_hash) tuple
    """
    declared_hash = _structure_hash(declared_val)
    discovered_hash = _structure_hash(discovered_val)

    steps.append("Mode: opaque")
    steps.append(f"Declared hash: {declared_hash}")
    steps.append(f"Discovered hash: {discovered_hash}")

    is_equal = declared_hash == discovered_hash
    steps.append(f"Result: {is_equal}")

    return (is_equal, declared_hash, discovered_hash)


def _compare_by_key(
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    key: str,
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two objects by one key's value, as strings, ignoring the rest.

    A side missing the key is a discrepancy rather than a match: the comparison
    the config asked for could not be performed, and a kernel that cannot
    compare must not claim agreement.

    Args:
        declared_val: Declared plane object
        discovered_val: Discovered plane object
        key: The key carrying the value to compare
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_extracted, discovered_extracted) tuple
    """
    stated_on_both = key in declared_val and key in discovered_val
    declared_extracted = _extracted_key(declared_val, key)
    discovered_extracted = _extracted_key(discovered_val, key)

    steps.append(f"Comparing on key {key!r}")
    steps.append(f"Declared {key}: {declared_extracted}")
    steps.append(f"Discovered {key}: {discovered_extracted}")

    # Presence is checked on the key itself, not on the extracted string: an
    # object whose value happens to equal the marker must not read as missing.
    is_equal = stated_on_both and declared_extracted == discovered_extracted
    steps.append(f"Result: {is_equal}")

    return (is_equal, declared_extracted, discovered_extracted)


def _extracted_key(value: dict[str, Any], key: str) -> str:
    """The string form of one key's value, or a marker when the key is absent."""
    if key not in value:
        return MISSING_KEY_MARKER
    return str(value[key])


def _compare_version(
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two objects by their `version` key, ignoring contents."""
    steps.append("Mode: version")
    return _compare_by_key(declared_val, discovered_val, OBJECT_VERSION_KEY, steps)


def _compare_identity(
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two objects by their `id` key, ignoring contents."""
    steps.append("Mode: identity")
    return _compare_by_key(declared_val, discovered_val, OBJECT_IDENTITY_KEY, steps)


def _compare_recursively(
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    depth: int,
    steps: list[str],
) -> tuple[bool, str, str]:
    """Compare two objects by drilling `depth` levels, then comparing opaquely.

    Recursion changes what the audit trail can say, not what counts as equal:
    the result matches an opaque comparison at every depth, but a recursive run
    names the path that diverged instead of two hashes that differ.

    Args:
        declared_val: Declared plane object
        discovered_val: Discovered plane object
        depth: Levels to drill; FULL_RECURSION drills without limit
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_canonical, discovered_canonical) tuple
    """
    steps.append(f"Mode: recurse({depth})")

    divergence = _first_divergence(declared_val, discovered_val, depth, "")
    is_equal = divergence is None
    if not is_equal:
        steps.append(f"Diverged at: {divergence}")
    steps.append(f"Result: {is_equal}")

    return (is_equal, canonical(declared_val), canonical(discovered_val))


def _first_divergence(
    declared_val: Any,
    discovered_val: Any,
    depth: int,
    path: str,
) -> str | None:
    """The path of the first difference in sorted key order, or None if none.

    Keys are walked over the union of both sides in sorted order, so the answer
    does not depend on either side's insertion order and a key present on only
    one side is a difference rather than a skip.

    Args:
        declared_val: Declared plane value at this path
        discovered_val: Discovered plane value at this path
        depth: Levels still available to drill at this path
        path: Dotted path walked so far

    Returns:
        The dotted path of the first divergence, or None if the values agree
    """
    if not _is_drillable(declared_val, discovered_val, depth):
        return _opaque_divergence(declared_val, discovered_val, path)

    remaining = _remaining_depth(depth)
    for key in sorted(set(declared_val) | set(discovered_val)):
        child_path = _joined_path(path, key)
        if key not in declared_val or key not in discovered_val:
            return child_path
        found = _first_divergence(declared_val[key], discovered_val[key], remaining, child_path)
        if found is not None:
            return found
    return None


def _is_drillable(declared_val: Any, discovered_val: Any, depth: int) -> bool:
    """True when both sides are objects and there is depth left to spend.

    Depth 0 is what makes recurse(0) and opaque the same comparison.
    """
    return depth != 0 and isinstance(declared_val, dict) and isinstance(discovered_val, dict)


def _remaining_depth(depth: int) -> int:
    """Full recursion never runs out; a finite depth spends one level per drill."""
    if depth == FULL_RECURSION:
        return FULL_RECURSION
    return depth - 1


def _opaque_divergence(declared_val: Any, discovered_val: Any, path: str) -> str | None:
    """Compare two values whole, naming the path when they differ."""
    if canonical(declared_val) == canonical(discovered_val):
        return None
    return path or ROOT_PATH


def _joined_path(path: str, key: str) -> str:
    """Extend a dotted path by one key."""
    if not path:
        return key
    return f"{path}.{key}"


# The fixed modes, as a table. recurse(N) is parameterized and cannot be a row.
OBJECT_MODE_HANDLERS: dict[str, Callable[[Any, Any, list[str]], tuple[bool, str, str]]] = {
    "opaque": _compare_opaque,
    "version": _compare_version,
    "identity": _compare_identity,
}
