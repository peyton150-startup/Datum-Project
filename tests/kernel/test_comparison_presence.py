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
because the rule is one rule.

That last sentence used to promise a sixth type would have to be added here to
pass, and `boolean` then arrived without being added -- its presence rules are
held by `test_comparison_boolean.py` instead, so nothing here failed. The
promise is kept only by `ALL_COMPARISONS` at the foot of this file, which does
enumerate every type, and which a seventh type would have to join.
"""

import pytest

from datum.reconcile.comparison import (
    compare_boolean,
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


def boolean_config():
    return FieldConfig("b", "boolean", {"mode": "exact"}, "discrepancy")


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


# Every type, boolean included, because the entry is built at seven sites and a
# migration that reaches six of them is the failure this section guards.
ALL_COMPARISONS = [*COMPARISONS, ("boolean", compare_boolean, boolean_config, True)]


@pytest.mark.parametrize(("name", "compare", "make_config", "value"), ALL_COMPARISONS)
class TestTheEntryCarriesTheStatementItself:
    """The audit entry distinguishes absent from null, not just the transform.

    This class used to assert the opposite -- that `declared_raw` was None for
    both, with only the transformed field telling them apart -- and said so as
    a stated boundary of the fix. Phase 2G removed the boundary: the entry now
    holds the `PlaneValue`s, so the distinction survives onto the record rather
    than only into a rendering of it.

    **The bug each test excludes is a half-migration**: an entry built from
    `.resolve(on_absent=lambda: None, ...)`, or from a value pulled out of the
    plane, at any one of the seven construction sites. Under that bug the first
    test compares None against None and fails, and the second gets a bare value
    where a PlaneValue was asserted. Both are parametrized over every type
    because six migrated sites and one missed one is the shape of the mistake.
    """

    def test_absent_and_null_are_distinguishable_on_the_entry(
        self, name, compare, make_config, value
    ):
        """Exercises the shared unstated path, where neither side has a value."""
        _, absent_log = compare(ABSENT, PlaneValue.of(value), make_config())
        _, null_log = compare(NULL, PlaneValue.of(value), make_config())

        assert (
            absent_log.declared != null_log.declared
        ), f"{name}: the entry collapsed absent and null back into one value"
        assert absent_log.declared == ABSENT
        assert null_log.declared == NULL

    def test_a_stated_value_reaches_the_entry_as_a_plane_value(
        self, name, compare, make_config, value
    ):
        """Exercises each comparison function's own construction site.

        The test above cannot: absence routes every type through
        `_unstated_comparison`, so it would pass with all six of the per-type
        sites left un-migrated.
        """
        stated = PlaneValue.of(value)
        _, log = compare(stated, stated, make_config())

        assert log.declared == stated, f"{name}: declared did not reach the entry as stated"
        assert log.discovered == stated, f"{name}: discovered did not reach the entry as stated"
