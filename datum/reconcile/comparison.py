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
from typing import Any, NamedTuple

from datum.reconcile.domain import PlaneValue, canonical
from datum.reconcile.schema import (
    FULL_RECURSION,
    FieldConfig,
    recursion_depth_of,
    states_recursion_mode,
    states_tolerance_mode,
    tolerance_of,
)

# --- Degraded comparisons ----------------------------------------------------
#
# A configured comparison that cannot run reports one of two things, and every
# comparison function reports them through these two routines rather than
# formatting the text itself. The audit log is what an operator greps to find
# misconfigured fields, so a path that spells the same condition differently
# hides itself from the search that was looking for it -- which is what
# happened: the numeric path said "Unusable mode parameter" while the object
# path said "Unknown mode" for the same class of malformed mode, and an
# operator grepping for one silently missed every instance of the other.
#
# The distinction between them is worth keeping. "The name is not one I know"
# sends a reader to the mode table; "the name is one I know and its parameter
# is not usable" sends them to the parameter. Kept in two places, it drifts.


def _unknown_mode_step(mode: Any) -> str:
    """Audit text for a mode whose name this field type does not recognise."""
    return f"Unknown mode: {mode}"


def _unusable_parameter_step(mode: Any) -> str:
    """Audit text for a recognised mode name whose parameter states nothing usable."""
    return f"Unusable mode parameter: {mode}"


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


# --- Presence, shared by every comparison type -------------------------------
#
# What a plane *said* about a field, not what it holds. Absence and null are two
# statements, and collapsing them into None is the defect PlaneValue exists to
# prevent (DESIGN section 13, DIFF_SEMANTICS Null / Missing / Empty).
#
# Encoded once, for the reason diff.py gives for having no sentinel of its own:
# a second encoding of absence would have to agree with the first. Every
# comparison type routes its absent, null, and wrong-type cases through this
# section rather than restating the rule.

STATEMENT_ABSENT = "absent"
STATEMENT_NULL = "null"

# The two kinds a *stated* key value can be, for the keyed object modes. They
# are kinds rather than spellings because a spelling can be forged: `canonical`
# emits JSON, so an ordinary provider string reading `{"a": 1}` would otherwise
# agree with the object of that shape. Scalars compare across types on purpose
# -- `1` and `"1"` are the same version -- and that licence must stop at the
# structure boundary, which only a separate kind can enforce.
STATEMENT_SCALAR = "scalar"
STATEMENT_STRUCTURED = "structured"

# Two planes agree here only when they made the same statement AND that
# statement is one a comparison can affirm. Two sides that both hold a value the
# configured mode could not compare -- a string under a list config, an integer
# under an object config -- agree by accident, not by comparison. The configured
# semantics never ran, so the result is a discrepancy rather than a match.
AFFIRMABLE_STATEMENTS = frozenset({STATEMENT_ABSENT, STATEMENT_NULL})


def _presence_and_value(plane_val: PlaneValue) -> tuple[bool, Any]:
    """Split a plane's statement into whether it spoke and what it said.

    Keeps presence alongside the value rather than folding absence into None,
    so `missing` and `null` stay two facts all the way into the comparison.
    """
    return plane_val.resolve(
        on_absent=lambda: (False, None),
        on_present=lambda v: (True, v),
    )


def _states_a_value(present: bool, value: Any) -> bool:
    """True when a plane mentioned the field and gave it something but null."""
    return present and value is not None


def _states_a_list(present: bool, value: Any) -> bool:
    """True when a plane both mentioned the field and gave it a list value."""
    return _states_a_value(present, value) and isinstance(value, list)


def _states_an_object(present: bool, value: Any) -> bool:
    """True when a plane both mentioned the field and gave it an object value."""
    return _states_a_value(present, value) and isinstance(value, dict)


def _states_a_boolean(present: bool, value: Any) -> bool:
    """True when a plane both mentioned the field and gave it a boolean value.

    `type(value) is bool` rather than isinstance: bool is a subclass of int, so
    isinstance would read a discovered `1` as `True`. The discovered plane has
    no type barricade, so this is the only place that distinction gets made --
    and a provider reporting `1` where a declaration says `true` is exactly the
    drift a reconciler exists to surface, not to smooth over.

    **Not routed through `ATTRIBUTE_TYPES["bool"]`, and after #55 it could not
    be.** That parser reads a declared boolean from its *scalar text* via
    `BOOLEAN_LITERALS`; this asks what type a plane's already-built Python value
    has. Different inputs, different questions -- the table says what an author
    may write in an intent document, while this runs on the discovered plane,
    which has no declared barricade and is never going to get one. Binding them
    would mean widening the declared vocabulary -- issue #53's open half --
    silently redefining what the *discovered* side counts as a boolean.

    So the guarantee that a discovered `1` is not a declared `true` is made at
    two layers by two different mechanisms, as DESIGN section 13 records: this
    type check on the comparison side, and on the declared side the fact that a
    boolean can only have come from the parser and therefore cannot be an int.
    `_states_a_list` and `_states_an_object` write their own shape test for the
    same reason, and have no declared counterpart to route through at all.
    """
    return _states_a_value(present, value) and type(value) is bool


def _plane_statement(present: bool, value: Any) -> str:
    """Name what one plane said about a field, presence included."""
    if not present:
        return STATEMENT_ABSENT
    if value is None:
        return STATEMENT_NULL
    return f"value:{type(value).__name__}"


def _unstated_comparison(
    field_config: FieldConfig,
    field_type: str,
    declared_state: tuple[bool, Any],
    discovered_state: tuple[bool, Any],
    steps: list[str],
) -> tuple[bool, AuditLogEntry]:
    """Compare two planes when at least one did not state a comparable value.

    Agreement here is agreement on the *statement*: both absent, or both null.
    Absent against null is a discrepancy, because field absence and an explicit
    null are different claims.

    Args:
        field_config: Configuration the comparison was asked for
        field_type: Type name to record on the audit entry
        declared_state: (present, value) for the declared plane
        discovered_state: (present, value) for the discovered plane
        steps: List to append comparison steps to

    Returns:
        (is_equal, audit_log_entry) tuple
    """
    mode: Any = field_config.comparison.get("mode")
    declared_statement = _plane_statement(*declared_state)
    discovered_statement = _plane_statement(*discovered_state)

    steps.append(f"Declared statement: {declared_statement}")
    steps.append(f"Discovered statement: {discovered_statement}")

    is_equal = (
        declared_statement == discovered_statement and declared_statement in AFFIRMABLE_STATEMENTS
    )
    steps.append(f"Result: {is_equal}")

    return (
        is_equal,
        AuditLogEntry(
            kind_name=field_config.comparison.get("_kind_name", "unknown"),
            field_name=field_config.field_name,
            field_type=field_type,
            comparison_mode=mode,
            declared_raw=declared_state[1],
            declared_transformed=declared_statement,
            discovered_raw=discovered_state[1],
            discovered_transformed=discovered_statement,
            result=is_equal,
            steps=steps,
        ),
    )


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

    declared_state = _presence_and_value(declared)
    discovered_state = _presence_and_value(discovered)
    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    if not _states_a_value(*declared_state) or not _states_a_value(*discovered_state):
        return _unstated_comparison(
            field_config, "numeric", declared_state, discovered_state, steps
        )

    # Mode-specific comparison
    if mode == "exact_value":
        is_equal = _compare_exact_value(declared_val, discovered_val, steps)
    elif mode == "exact_string":
        is_equal = _compare_exact_string(declared_val, discovered_val, steps)
    elif states_tolerance_mode(mode):
        # A mode recognised by name may still state no usable parameter, if it
        # reached here without passing the barricade. That is the unknown-mode
        # case wearing a known name: same verdict, different audit text.
        tolerance = tolerance_of(mode)
        if tolerance is None:
            steps.append(_unusable_parameter_step(mode))
            is_equal = False
        else:
            is_equal = _compare_tolerance(declared_val, discovered_val, tolerance, steps)
    else:
        steps.append(_unknown_mode_step(mode))
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

    declared_state = _presence_and_value(declared)
    discovered_state = _presence_and_value(discovered)
    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    if not _states_a_value(*declared_state) or not _states_a_value(*discovered_state):
        return _unstated_comparison(field_config, "string", declared_state, discovered_state, steps)

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
        steps.append(_unknown_mode_step(mode))
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

    declared_state = _presence_and_value(declared)
    discovered_state = _presence_and_value(discovered)
    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    # Absent, null, and not-a-list all land here: none of them is a list this
    # mode can compare, and the statement rule decides all three the same way.
    if not _states_a_list(*declared_state) or not _states_a_list(*discovered_state):
        return _unstated_comparison(field_config, "list", declared_state, discovered_state, steps)

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
        steps.append(_unknown_mode_step(mode))

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

    declared_state = _presence_and_value(declared)
    discovered_state = _presence_and_value(discovered)
    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    if not _states_a_value(*declared_state) or not _states_a_value(*discovered_state):
        return _unstated_comparison(
            field_config, "timestamp", declared_state, discovered_state, steps
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
        steps.append(_unknown_mode_step(mode))
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

ROOT_PATH = "<root>"
IGNORED_MARKER = "<ignored>"
MISSING_KEY_MARKER = "<missing>"
NULL_KEY_MARKER = "<null>"


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

    # `ignore` short-circuits ahead of presence handling on purpose: a field
    # configured as ignored must never produce a discrepancy, including when
    # one plane omits it entirely.
    if mode == OBJECT_MODE_IGNORE:
        is_equal, declared_transformed, discovered_transformed = _ignored_comparison(steps)
    elif not _states_an_object(*declared_state) or not _states_an_object(*discovered_state):
        return _unstated_comparison(field_config, "object", declared_state, discovered_state, steps)
    else:
        is_equal, declared_transformed, discovered_transformed = _object_comparison(
            mode, declared_state[1], discovered_state[1], steps
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


def _object_comparison(
    mode: Any,
    declared_val: dict[str, Any],
    discovered_val: dict[str, Any],
    steps: list[str],
) -> tuple[bool, str, str]:
    """Route one object comparison to its mode, returning result and audit forms.

    Both sides are known to be objects by the time this runs.

    Args:
        mode: Comparison mode from the field config
        declared_val: Declared plane object
        discovered_val: Discovered plane object
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_transformed, discovered_transformed) tuple
    """
    handler = OBJECT_MODE_HANDLERS.get(mode)
    if handler is not None:
        return handler(declared_val, discovered_val, steps)

    if states_recursion_mode(mode):
        # A mode recognised by name may still state no usable depth, if it
        # reached here without passing the barricade. Same verdict as an
        # unrecognised name, and deliberately not the same audit text: the
        # reader is sent to the parameter rather than to the mode table.
        depth = recursion_depth_of(mode)
        if depth is not None:
            return _compare_recursively(declared_val, discovered_val, depth, steps)
        steps.append(_unusable_parameter_step(mode))
    else:
        steps.append(_unknown_mode_step(mode))

    return (False, canonical(declared_val), canonical(discovered_val))


def _ignored_comparison(steps: list[str]) -> tuple[bool, str, str]:
    """Report a match without reading either side.

    `ignore` short-circuits ahead of presence and type handling on purpose: a
    field configured as ignored must never produce a discrepancy, including
    when one plane omits it entirely.
    """
    steps.append("Mode: ignore")
    steps.append("Result: True (field ignored; never a discrepancy)")
    return (True, IGNORED_MARKER, IGNORED_MARKER)


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

    Compares what each side *said* about the key, not the string it renders to.
    Three statements are possible -- the key is absent, the key is null, or the
    key carries a value -- and two sides agree only when they made the same one.

    Absent on both sides is agreement (#35). The key is the whole meaning of the
    field here, so two objects that both omit it have said the same thing about
    it, which is row 4 of the Null / Missing / Empty table and the rule
    `AFFIRMABLE_STATEMENTS` states for every other comparison type. The reading
    this replaced called it a discrepancy on the grounds that nothing could be
    compared -- but it called null-on-both-sides a match, and null is no more
    comparable than absent. One rule had to cover both.

    Null is not the string spelling of itself (#34). `str(None)` is `"None"`,
    which a provider may legitimately emit as a value, so stringifying first
    made a declared null and a discovered `"None"` indistinguishable. Presence
    and nullity are read off the key, and only a stated value is ever rendered.

    Args:
        declared_val: Declared plane object
        discovered_val: Discovered plane object
        key: The key carrying the value to compare
        steps: List to append comparison steps to

    Returns:
        (is_equal, declared_extracted, discovered_extracted) tuple
    """
    declared_statement = _key_statement(declared_val, key)
    discovered_statement = _key_statement(discovered_val, key)

    declared_extracted = _rendered_key(declared_statement)
    discovered_extracted = _rendered_key(discovered_statement)

    # The kind is named alongside the rendering, because two statements of
    # different kinds can render alike -- a structured value and a string
    # spelling its canonical form both read as `{"a": 1}`. The decision already
    # accounts for the difference; without this the log shows two identical
    # values beside `Result: False` and cannot explain itself.
    steps.append(f"Comparing on key {key!r}")
    steps.append(f"Declared {key}: {declared_extracted} ({declared_statement.kind})")
    steps.append(f"Discovered {key}: {discovered_extracted} ({discovered_statement.kind})")

    # Statements are compared, never the rendered strings. The markers below are
    # for the audit log, so an object whose value happens to equal one of them
    # must not read as absent or null.
    is_equal = declared_statement == discovered_statement
    steps.append(f"Result: {is_equal}")

    return (is_equal, declared_extracted, discovered_extracted)


class KeyStatement(NamedTuple):
    """What one object said about one key: of what kind, and spelled how.

    The kind travels *beside* the rendering rather than inside it, for the
    reason `_compare_by_key` gives about statements generally: anything folded
    into a string can be spelled by data. A prefix would only move the forgery
    -- a provider emitting the prefix as an ordinary string would reach the
    same collision one step later.

    `rendering` is empty for the two unstated kinds. They carry no value to
    spell, and giving them one would invite comparing it.
    """

    kind: str
    rendering: str


def _key_statement(value: dict[str, Any], key: str) -> KeyStatement:
    """Name what one object said about one key, presence and nullity included.

    The inner-key counterpart of `_plane_statement`, and it reuses that
    section's constants rather than restating absence a second time.
    """
    if key not in value:
        return KeyStatement(STATEMENT_ABSENT, "")
    if value[key] is None:
        return KeyStatement(STATEMENT_NULL, "")
    return _stated_key_statement(value[key])


def _stated_key_statement(stated: Any) -> KeyStatement:
    """How a stated key value is compared: of what kind, and spelled how.

    **Scalars** are spelled with `str()` and compared across types, because
    that is what makes `1` and `"1"` the same version -- the configured
    semantics of these two modes, which compare a version *as a string*.

    **Structured values** are spelled with `canonical()`, because `str()` on a
    dict spells it in insertion order, and two structurally identical values
    built in different orders would then be reported as a discrepancy (#39).
    That would contradict the invariant `compare_object` states about itself,
    and which every other object mode keeps: key order never changes a result,
    at any depth, in any mode.

    **The two kinds never compare equal to each other**, whatever they spell.
    A dict is not a version string, and `canonical()` emits JSON, so a string
    that happens to spell `{"a": 1}` would otherwise agree with the object of
    that shape -- silently, and with an audit log showing both sides alike.
    Cross-type comparison is intended *within* scalars and wrong across this
    boundary, so the boundary is a separate kind rather than a spelling.

    A structured value here is out of spec rather than expected -- the spec
    says a version *field* compared *as strings* -- but out of spec is not the
    same as unreachable, and an order-dependent verdict, or a silent agreement
    with an unrelated string, are the two worst answers available about one.
    """
    if isinstance(stated, dict | list):
        return KeyStatement(STATEMENT_STRUCTURED, canonical(stated))
    return KeyStatement(STATEMENT_SCALAR, str(stated))


def _rendered_key(statement: KeyStatement) -> str:
    """The audit log's spelling of one key statement.

    Rendering is one-way on purpose: two different statements may never render
    alike into a *decision*, only into the log, because the decision was already
    made on the statements themselves.
    """
    if statement.kind == STATEMENT_ABSENT:
        return MISSING_KEY_MARKER
    if statement.kind == STATEMENT_NULL:
        return NULL_KEY_MARKER
    return statement.rendering


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


# --- Boolean -----------------------------------------------------------------


def compare_boolean(
    declared: PlaneValue,
    discovered: PlaneValue,
    field_config: FieldConfig,
) -> tuple[bool, AuditLogEntry]:
    """Compare boolean fields.

    One mode, `exact`. A boolean has two values and nothing to normalise
    between them, so there is no second mode to offer and no transformation to
    record -- the transformed values on the audit entry are the raw ones.

    Added with the type itself (issue #53): `bool` was declarable through intent
    ingestion while `Kind.attribute_schema` had no field type that could name
    it, so a declared boolean could be written and never compared.

    Anything that is not a boolean on either side lands in the statement rule
    rather than here, `_states_a_boolean` being where that is decided. That
    includes a discovered `1`, which is the case worth naming: the discovered
    plane has no type barricade, and reading `1` as `True` would silence exactly
    the drift this exists to report.

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
    declared_val = declared_state[1]
    discovered_val = discovered_state[1]

    if not _states_a_boolean(*declared_state) or not _states_a_boolean(*discovered_state):
        return _unstated_comparison(
            field_config, "boolean", declared_state, discovered_state, steps
        )

    if mode == "exact":
        # Spelled the way every other mode spells it. The audit log is what an
        # operator greps, and this path saying "-> False" where the rest of the
        # file says "Result: False" would hide every boolean discrepancy from
        # the search that was looking for it -- the exact failure the degraded
        # comparisons at the top of this file were consolidated to end.
        steps.append("Mode: exact")
        steps.append(f"Declared: {declared_val!r}")
        steps.append(f"Discovered: {discovered_val!r}")
        is_equal = declared_val == discovered_val
        steps.append(f"Result: {is_equal}")
    else:
        steps.append(_unknown_mode_step(mode))
        is_equal = False

    return (
        is_equal,
        AuditLogEntry(
            kind_name=kind_name,
            field_name=field_config.field_name,
            field_type="boolean",
            comparison_mode=mode,
            declared_raw=declared_val,
            declared_transformed=declared_val,
            discovered_raw=discovered_val,
            discovered_transformed=discovered_val,
            result=is_equal,
            steps=steps,
        ),
    )
