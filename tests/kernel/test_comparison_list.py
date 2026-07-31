"""Tests for list field comparison.

Covers all list modes: ordered, unordered_multiset, set.
Tests null/absent/missing cases, duplicates, nested lists, and adversarial corpus.
"""

from datum.reconcile.comparison import compare_list
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


class TestListOrdered:
    """Tests for ordered mode: position matters."""

    def test_equal_lists(self):
        """[1, 2, 3] == [1, 2, 3] in ordered mode."""
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of([1, 2, 3])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_different_order(self):
        """[1, 2, 3] != [3, 2, 1] in ordered mode."""
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of([3, 2, 1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_empty_lists(self):
        """[] == [] in all modes."""
        declared = PlaneValue.of([])
        discovered = PlaneValue.of([])
        config = FieldConfig(
            field_name="items",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_different_length(self):
        """[1, 2, 3] != [1, 2]."""
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of([1, 2])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_string_lists(self):
        """String lists should compare by order."""
        declared = PlaneValue.of(["a", "b", "c"])
        discovered = PlaneValue.of(["a", "b", "c"])
        config = FieldConfig(
            field_name="tags",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True


class TestListMultiset:
    """Tests for unordered_multiset mode: order ignored, duplicates matter."""

    def test_different_order_same_elements(self):
        """[1, 2, 3] == [3, 2, 1] in multiset mode."""
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of([3, 2, 1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={
                "mode": "unordered_multiset",
                "element_comparison": {"mode": "exact_value"},
            },
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_duplicates_matter(self):
        """[1, 1, 2] != [1, 2] in multiset mode."""
        declared = PlaneValue.of([1, 1, 2])
        discovered = PlaneValue.of([1, 2])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={
                "mode": "unordered_multiset",
                "element_comparison": {"mode": "exact_value"},
            },
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_same_duplicates_different_order(self):
        """[1, 1, 2] == [2, 1, 1] in multiset mode."""
        declared = PlaneValue.of([1, 1, 2])
        discovered = PlaneValue.of([2, 1, 1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={
                "mode": "unordered_multiset",
                "element_comparison": {"mode": "exact_value"},
            },
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_empty_lists_multiset(self):
        """[] == [] in multiset mode."""
        declared = PlaneValue.of([])
        discovered = PlaneValue.of([])
        config = FieldConfig(
            field_name="items",
            field_type="list",
            comparison={
                "mode": "unordered_multiset",
                "element_comparison": {"mode": "exact_value"},
            },
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True


class TestListSet:
    """Tests for set mode: order and duplicates ignored."""

    def test_different_order_duplicates_ignored(self):
        """[1, 1, 2, 3, 3] == [3, 2, 1] in set mode."""
        declared = PlaneValue.of([1, 1, 2, 3, 3])
        discovered = PlaneValue.of([3, 2, 1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_set_ignores_duplicates(self):
        """[1, 1, 1] == [1] in set mode."""
        declared = PlaneValue.of([1, 1, 1])
        discovered = PlaneValue.of([1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_different_elements_in_set(self):
        """[1, 2] != [1, 3] in set mode."""
        declared = PlaneValue.of([1, 2])
        discovered = PlaneValue.of([1, 3])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_empty_lists_set(self):
        """[] == [] in set mode."""
        declared = PlaneValue.of([])
        discovered = PlaneValue.of([])
        config = FieldConfig(
            field_name="items",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True


class TestListNullAndAbsent:
    """Tests for null, absent, and missing value handling."""

    def test_both_absent(self):
        """Both absent should match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.absent()
        config = FieldConfig(
            field_name="optional_list",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_both_null(self):
        """Both null should match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of(None)
        config = FieldConfig(
            field_name="optional_list",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_null_vs_value(self):
        """Null vs list should not match."""
        declared = PlaneValue.of(None)
        discovered = PlaneValue.of([1, 2])
        config = FieldConfig(
            field_name="optional_list",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_absent_vs_value(self):
        """Absent vs list should not match."""
        declared = PlaneValue.absent()
        discovered = PlaneValue.of([1, 2])
        config = FieldConfig(
            field_name="optional_list",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_not_a_list_declared(self):
        """Non-list value should not match."""
        declared = PlaneValue.of("not a list")
        discovered = PlaneValue.of([1, 2])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_not_a_list_discovered(self):
        """Non-list value should not match."""
        declared = PlaneValue.of([1, 2])
        discovered = PlaneValue.of({"a": 1})
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False


class TestListNested:
    """Tests for nested and mixed-type lists."""

    def test_nested_lists_ordered(self):
        """Nested lists should be compared in order."""
        declared = PlaneValue.of([[1, 2], [3, 4]])
        discovered = PlaneValue.of([[1, 2], [3, 4]])
        config = FieldConfig(
            field_name="matrix",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_nested_lists_different_order(self):
        """[[1, 2], [3, 4]] != [[3, 4], [1, 2]] in ordered mode."""
        declared = PlaneValue.of([[1, 2], [3, 4]])
        discovered = PlaneValue.of([[3, 4], [1, 2]])
        config = FieldConfig(
            field_name="matrix",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_nested_lists_set_mode(self):
        """Nested lists in set mode."""
        declared = PlaneValue.of([[1, 2], [3, 4]])
        discovered = PlaneValue.of([[3, 4], [1, 2]])
        config = FieldConfig(
            field_name="matrix",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_mixed_type_list_ordered(self):
        """Mixed type lists in ordered mode."""
        declared = PlaneValue.of([1, "two", 3.0, [4]])
        discovered = PlaneValue.of([1, "two", 3.0, [4]])
        config = FieldConfig(
            field_name="mixed",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_dict_in_list_set_mode(self):
        """Dicts in lists should work in set mode."""
        declared = PlaneValue.of([{"a": 1}, {"b": 2}])
        discovered = PlaneValue.of([{"b": 2}, {"a": 1}])
        config = FieldConfig(
            field_name="configs",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True


class TestListAdversarialCases:
    """Adversarial corpus cases from DIFF_SEMANTICS.md."""

    def test_single_element_list(self):
        """Single element lists."""
        declared = PlaneValue.of([42])
        discovered = PlaneValue.of([42])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_many_duplicates_multiset(self):
        """Many duplicates in multiset mode."""
        declared = PlaneValue.of([1, 1, 1, 1, 1])
        discovered = PlaneValue.of([1, 1, 1, 1, 1])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={
                "mode": "unordered_multiset",
                "element_comparison": {"mode": "exact_value"},
            },
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_all_none_list(self):
        """List of None values."""
        declared = PlaneValue.of([None, None, None])
        discovered = PlaneValue.of([None, None, None])
        config = FieldConfig(
            field_name="values",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_mixed_nulls_in_set(self):
        """Mixed nulls in set mode."""
        declared = PlaneValue.of([1, None, 1, None])
        discovered = PlaneValue.of([None, 1])
        config = FieldConfig(
            field_name="values",
            field_type="list",
            comparison={"mode": "set", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_string_numbers_vs_int(self):
        """String numbers vs integers should not match in exact comparison."""
        declared = PlaneValue.of([1, 2, 3])
        discovered = PlaneValue.of(["1", "2", "3"])
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is False

    def test_very_long_list(self):
        """Very long list comparison."""
        long_list = list(range(1000))
        declared = PlaneValue.of(long_list)
        discovered = PlaneValue.of(long_list)
        config = FieldConfig(
            field_name="ids",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        assert is_equal is True

    def test_float_int_in_list(self):
        """Floats vs ints in list (canonical form)."""
        declared = PlaneValue.of([1, 2.0, 3])
        discovered = PlaneValue.of([1.0, 2, 3.0])
        config = FieldConfig(
            field_name="values",
            field_type="list",
            comparison={"mode": "ordered", "element_comparison": {"mode": "exact_value"}},
            logging="discrepancy",
        )

        is_equal, log = compare_list(declared, discovered, config)
        # Canonical form should handle numeric equivalence
        assert log.result is False  # Because 1 != 1.0 in canonical form
