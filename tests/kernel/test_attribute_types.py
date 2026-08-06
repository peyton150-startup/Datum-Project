"""The tie between the two attribute-type vocabularies (issue #53).

`reconcile/attribute_types.py` holds two tables that have to agree. Holding them
adjacent makes the disagreement visible; these tests are what make it fail.

The bug excluded is drift: someone adds a field type and does not say which
declared type feeds it, or adds a declared type no comparison can name. Both
happened before the tables were joined -- `list` and `object` could never
receive a declared value, and `bool` could be declared while no field type could
describe how to compare it -- and nothing failed, because each table was correct
on its own terms.
"""

import pytest
import yaml

from datum.intent.documents import parse_document_set
from datum.intent.errors import InvalidRevision
from datum.reconcile.attribute_types import (
    DECLARED_TYPE_NAMES,
    DECLARED_TYPES,
    DISCOVERED_ONLY_FIELD_TYPES,
    FIELD_TYPES,
    VALID_FIELD_TYPES,
)
from datum.reconcile.schema import FieldConfig, InvalidComparisonMode


class TestTheTwoTablesAgree:
    """Neither table may name something the other has not heard of."""

    def test_every_field_type_names_a_declared_type_or_says_it_has_none(self):
        """The forward direction: no field type may point at a type that does not exist.

        Fails if a field type is added with a typo'd declared name, or with a
        declared name that was removed from the other table.
        """
        named = {declared for declared in FIELD_TYPES.values() if declared is not None}

        assert named <= DECLARED_TYPE_NAMES

    def test_every_declared_type_is_reachable_by_some_field_type(self):
        """The reverse direction, and the one that was broken.

        `bool` was declarable and named by nothing, so an author could write
        `enabled: bool` and `Kind.attribute_schema` had no way to say how to
        compare it. This is the assertion that would have caught it.
        """
        named = {declared for declared in FIELD_TYPES.values() if declared is not None}

        assert DECLARED_TYPE_NAMES <= named

    def test_the_discovered_only_types_are_derived_not_restated(self):
        """A third list of the same fact is what this module exists to prevent."""
        assert DISCOVERED_ONLY_FIELD_TYPES == {
            field_type for field_type, declared in FIELD_TYPES.items() if declared is None
        }

    def test_list_and_object_are_the_discovered_only_types(self):
        """Pins today's answer, so widening the declared vocabulary is a visible edit.

        Not an argument that these two *should* be discovered-only -- that is
        issue #53's open question. It is a record of which pairs the rest of the
        system may currently assume unreachable, so a change to it cannot be
        made by accident.
        """
        assert DISCOVERED_ONLY_FIELD_TYPES == {"list", "object"}


class TestEveryFieldTypeIsValidatable:
    """A field type with no validator is a KeyError at configuration time."""

    @pytest.mark.parametrize("field_type", sorted(VALID_FIELD_TYPES))
    def test_a_nonsense_mode_is_refused_rather_than_crashing(self, field_type):
        """The bug excluded: adding a field type and forgetting its mode validator.

        `_validate_comparison_config` dispatches through a dict keyed on field
        type. A type present in the vocabulary and absent from that dict raises
        `KeyError`, not `InvalidComparisonMode` -- so this asserts the exception
        type rather than merely that it raised.

        A nonsense mode rather than a valid one on purpose: naming a valid mode
        per type here would be a fresh encoding of the mode vocabulary, which is
        the mistake one directory up.
        """
        with pytest.raises(InvalidComparisonMode):
            FieldConfig(
                field_name="whatever",
                field_type=field_type,
                comparison={"mode": "no-such-mode"},
                logging="discrepancy",
            )


# Sample values for the declared types, chosen by asking the predicates rather
# than by pairing each name to a value here -- that pairing would be a second
# encoding of the vocabulary, which is the mistake this module exists to remove.
# A declared type that no candidate satisfies fails loudly, and that failure is
# itself the drift signal.
_CANDIDATE_VALUES = (3, "three", True, 1.5, [1], {"a": 1})


def _witness_for(type_name: str) -> object:
    predicate = DECLARED_TYPES[type_name]
    for candidate in _CANDIDATE_VALUES:
        if predicate(candidate):
            return candidate
    pytest.fail(f"no candidate value satisfies the {type_name!r} predicate; add one")


def _document(attribute_value: object) -> tuple[str, str]:
    return (
        "doc.yaml",
        yaml.safe_dump(
            {
                "apiVersion": "datum.dev/v1",
                "kind": "Deployment",
                "metadata": {"name": "api", "scope": "default"},
                "attributes": {"enabled": attribute_value},
            }
        ),
    )


class TestTheDeclaredBarricadeReadsTheSharedTable:
    """`documents.py` validates against the table rather than restating it."""

    @pytest.mark.parametrize("type_name", sorted(DECLARED_TYPE_NAMES))
    def test_every_type_the_table_holds_is_accepted(self, type_name):
        """The drift detector, and the only test here that a stale copy would fail.

        The bug excluded is `documents.py` keeping its own copy of the table.
        A copy that is identical today passes this, as it must -- what it cannot
        survive is the copy going stale: add a type to `DECLARED_TYPES` and this
        parametrization grows a case that a private table would reject. That is
        the moment the old code failed silently and this one does not.

        Deliberately *not* demonstrated by the `float` case below, which a
        private `int`/`str`/`bool` table would have refused just as readily.
        """
        witness = _witness_for(type_name)

        snapshots = parse_document_set(
            [_document(witness)], "t", {"Deployment": {"enabled": type_name}}
        )

        assert snapshots[0].attributes == {"enabled": witness}

    def test_a_type_the_table_does_not_hold_is_refused(self):
        """A guard against an over-broad table, not evidence of single-sourcing.

        `float` is the name worth guarding: `numeric` comparison, `tolerance(N)`
        in particular, is written for floats, so a widening that reached for the
        obvious name would land here. The previous private table refused `float`
        too, so this passes with the single source reverted -- it borrows no
        credit for that change, and the parametrized test above is what earns it.
        """
        document = (
            "doc.yaml",
            "apiVersion: datum.dev/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: api\n"
            "  scope: default\n"
            "attributes:\n"
            "  replicas: 1.5\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"Deployment": {"replicas": "float"}})

        assert "float" in str(caught.value)

    def test_each_declared_type_recognises_its_own_values_and_no_others(self):
        """bool is not an int, which isinstance would have got wrong.

        The predicates use `type(v) is int` for exactly this: a declaration
        reading `replicas: true` must not validate as an integer.
        """
        values = {"int": 3, "str": "three", "bool": True}

        for type_name, predicate in DECLARED_TYPES.items():
            for other_name, value in values.items():
                assert predicate(value) is (
                    type_name == other_name
                ), f"{type_name} predicate disagreed about {value!r}"
