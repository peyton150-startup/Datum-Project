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

from datum.intent.documents import parse_document_set
from datum.intent.errors import InvalidRevision
from datum.reconcile.attribute_types import (
    ATTRIBUTE_TYPES,
    DECLARED_TYPE_NAMES,
    DISCOVERED_ONLY_FIELD_TYPES,
    FIELD_TYPES,
    VALID_FIELD_TYPES,
    UnacceptableLiteral,
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


# Candidate scalar *texts*, offered to the parsers rather than paired with type
# names here -- that pairing would be a second encoding of the vocabulary, which
# is the mistake this module exists to remove. A declared type no candidate
# satisfies fails loudly, and that failure is itself the drift signal.
#
# Texts rather than Python values because after issue #55 a declared value is
# established from what the author wrote, not from what YAML resolved it to.
_CANDIDATE_TEXTS = ("3", "three", "true", "1.5", "-7")


def _witness_for(type_name: str) -> tuple[str, object]:
    """The first candidate text this type's parser accepts, and what it yields."""
    parse = ATTRIBUTE_TYPES[type_name]
    for candidate in _CANDIDATE_TEXTS:
        try:
            return candidate, parse(candidate)
        except UnacceptableLiteral:
            continue
    pytest.fail(f"no candidate text is a valid {type_name!r} literal; add one")


def _document(attribute_text: str) -> tuple[str, str]:
    """A document with the witness written literally, not dumped from a value.

    `yaml.safe_dump` would quote or re-render the scalar according to its own
    rules, which is the authority this barricade no longer takes instruction
    from -- a test that went through it would be asking the wrong question.
    """
    return (
        "doc.yaml",
        "apiVersion: datum.dev/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: api\n"
        "  scope: default\n"
        "attributes:\n"
        f"  enabled: {attribute_text}\n",
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
        text, expected = _witness_for(type_name)

        snapshots = parse_document_set(
            [_document(text)], "t", {"Deployment": {"enabled": type_name}}
        )

        assert snapshots[0].attributes == {"enabled": expected}

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

    def test_the_parsers_do_not_defer_to_python_coercion(self):
        """`bool("3")` is True, and a parser written that way would take anything.

        The bug excluded is a parser implemented as the Python constructor for
        its type. `int("true")` happens to raise, so an int parser written as
        `int(text)` looks correct until it meets `1_000`, which Python accepts
        and this vocabulary does not. `bool(text)` is worse: every non-empty
        string is True, so `is_public: NO` would be True rather than rejected --
        the Norway problem with its sign flipped.

        Not a claim about `str`, which accepts any scalar text on purpose.
        """
        assert ATTRIBUTE_TYPES["str"]("true") == "true"

        for text in ("true", "1_000", "007"):
            with pytest.raises(UnacceptableLiteral):
                ATTRIBUTE_TYPES["int"](text)

        for text in ("3", "1", "NO", "yes", ""):
            with pytest.raises(UnacceptableLiteral):
                ATTRIBUTE_TYPES["bool"](text)
