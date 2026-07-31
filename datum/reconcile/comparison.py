"""Field comparison functions using schema-aware, type-specific logic.

This module implements comparison handlers for each field type defined in
Kind.attribute_schema. Each function:

1. Takes declared and discovered PlaneValue objects
2. Applies mode-specific transformation/comparison logic
3. Returns (is_equal, audit_log_entry) tuple

The audit_log_entry captures the decision trail for debugging and auditing.
"""

from dataclasses import dataclass
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
