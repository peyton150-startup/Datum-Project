"""The Null / Missing / Empty table, enforced across every comparison type.

DIFF_SEMANTICS.md, Core Principles:

    | missing | null    | discrepancy | Field absence is not an explicit null |
    | null    | null    | match       | Both sides agree on null              |
    | ""      | missing | discrepancy | Empty string is not field absence     |
    | []      | null    | discrepancy | Empty list is not null                |
    | []      | []      | match       | Both sides agree on empty list        |
    | {}      | {}      | match       | Both sides agree on empty object      |

Phases 2B through 2E each read their two planes through

    resolve(on_absent=lambda: None, on_present=lambda v: v)

and then compared the results, which makes `PlaneValue.absent()` and
`PlaneValue.of(None)` indistinguishable and turns row one of that table into a
match. Nothing caught it: every phase tested both-absent and both-null, and no
phase tested one against the other. `test_null_versus_absent.py` holds the rule
against `diff.py`, which still compares with `PlaneValue.__eq__` -- so the
defect was latent, and would have gone live the moment Phase 2H routed
`_field_discrepancies` through these functions instead.

These tests are written against all five types together rather than per phase,
because the rule is one rule. A sixth comparison type added later has to be
added here to pass.
"""

import pytest

from datum.reconcile.comparison import (
    compare_list,
    compare_numeric,
    compare_object,
    compare_string,
    compare_timestamp,
)
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig

ABSENT = PlaneValue.absent()
NULL = PlaneValue.of(None)


def numeric_config():
    return FieldConfig("n", "numeric", {"mode": "exact_value"}, "discrepancy")


def string_config():
    return FieldConfig("s", "string", {"mode": "exact"}, "discrepancy")


def list_config():
    return FieldConfig(
        "l",
        "list",
        {"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
        "discrepancy",
    )


def timestamp_config():
    return FieldConfig("t", "timestamp", {"mode": "string"}, "discrepancy")


def object_config():
    return FieldConfig("o", "object", {"mode": "opaque"}, "discrepancy")


# (name, comparison function, config factory, a value of the right type)
COMPARISONS = [
    ("numeric", compare_numeric, numeric_config, 3),
    ("string", compare_string, string_config, "nginx"),
    ("list", compare_list, list_config, [1, 2]),
    ("timestamp", compare_timestamp, timestamp_config, "2026-07-30T00:00:00Z"),
    ("object", compare_object, object_config, {"a": 1}),
]

# The empty-but-present value each type can hold, where it has one.
EMPTY_VALUES = [
    ("string", compare_string, string_config, ""),
    ("list", compare_list, list_config, []),
    ("object", compare_object, object_config, {}),
    ("numeric", compare_numeric, numeric_config, 0),
]


@pytest.mark.parametrize(("name", "compare", "make_config", "value"), COMPARISONS)
class TestAbsenceIsNotNull:
    """Row one of the table, in both directions, for every type."""

    def test_absent_against_null_is_a_discrepancy(self, name, compare, make_config, value):
        is_equal, log = compare(ABSENT, NULL, make_config())
        assert is_equal is False, f"{name}: absent against null read as a match"
        assert log.declared_transformed == "absent"
        assert log.discovered_transformed == "null"

    def test_null_against_absent_is_a_discrepancy(self, name, compare, make_config, value):
        """The mirrored direction, enumerated rather than inferred.

        A helper that handles the declared side properly and reuses a bare-None
        check for the discovered side passes the test above and fails this one.
        """
        is_equal, log = compare(NULL, ABSENT, make_config())
        assert is_equal is False, f"{name}: null against absent read as a match"
        assert log.declared_transformed == "null"
        assert log.discovered_transformed == "absent"

    def test_absent_on_both_sides_is_a_match(self, name, compare, make_config, value):
        is_equal, _ = compare(ABSENT, ABSENT, make_config())
        assert is_equal is True, f"{name}: two absences disagreed"

    def test_null_on_both_sides_is_a_match(self, name, compare, make_config, value):
        is_equal, _ = compare(NULL, NULL, make_config())
        assert is_equal is True, f"{name}: two nulls disagreed"

    def test_absent_against_a_real_value_is_a_discrepancy(self, name, compare, make_config, value):
        assert compare(ABSENT, PlaneValue.of(value), make_config())[0] is False
        assert compare(PlaneValue.of(value), ABSENT, make_config())[0] is False

    def test_null_against_a_real_value_is_a_discrepancy(self, name, compare, make_config, value):
        assert compare(NULL, PlaneValue.of(value), make_config())[0] is False
        assert compare(PlaneValue.of(value), NULL, make_config())[0] is False


@pytest.mark.parametrize(("name", "compare", "make_config", "empty"), EMPTY_VALUES)
class TestEmptyIsNotAbsentAndNotNull:
    """Presence is not truthiness. Zero, "", [], and {} are values."""

    def test_empty_against_null_is_a_discrepancy(self, name, compare, make_config, empty):
        assert compare(PlaneValue.of(empty), NULL, make_config())[0] is False
        assert compare(NULL, PlaneValue.of(empty), make_config())[0] is False

    def test_empty_against_absent_is_a_discrepancy(self, name, compare, make_config, empty):
        assert compare(PlaneValue.of(empty), ABSENT, make_config())[0] is False
        assert compare(ABSENT, PlaneValue.of(empty), make_config())[0] is False

    def test_empty_against_empty_is_a_match(self, name, compare, make_config, empty):
        assert compare(PlaneValue.of(empty), PlaneValue.of(empty), make_config())[0] is True


class TestTheRawValueStillCollapses:
    """A boundary of the fix, stated so nobody assumes more than it delivers.

    `declared_raw` on the audit entry is the value, and an absent plane has no
    value to record, so absent and null both land as None there. The statement
    is what distinguishes them, and the statement is what a reader sees. This
    test exists so the next person to widen the audit entry knows the raw pair
    was never the thing carrying presence.
    """

    def test_raw_is_none_for_both_but_the_statement_is_not(self):
        _, absent_log = compare_numeric(ABSENT, PlaneValue.of(3), numeric_config())
        _, null_log = compare_numeric(NULL, PlaneValue.of(3), numeric_config())

        assert absent_log.declared_raw is None
        assert null_log.declared_raw is None
        assert absent_log.declared_transformed != null_log.declared_transformed
