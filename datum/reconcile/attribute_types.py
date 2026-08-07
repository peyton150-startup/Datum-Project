"""What type an attribute may be, stated once (issues #53, #55).

Three modules used to answer this and they disagreed. `intent/documents.py`
admitted `int`, `str` and `bool`; `reconcile/schema.py` named `list`, `numeric`,
`string`, `timestamp` and `object`; and the discovered plane admitted whatever
JSON and Postgres would carry. Each was correct on its own terms, which is what
made the disagreement invisible -- nothing joined them and nothing failed when
they drifted. Two of the five comparison types could never receive a declared
value at all, and `bool` could be declared but named no comparison type, so
`Kind.attribute_schema` had no way to say how to compare it.

**The two vocabularies are different questions and both live here.** A declared
type is what an author writes in an intent document. A field type is what
`Kind.attribute_schema` names to select a comparison. They are not the same set
and neither contains the other: `str` carries two field types, and two field
types carry no declared value at all.

**Each declared type owns its literal parser, and that is the whole vocabulary
(issue #55).** There is no separate name list, predicate table, or branch
statement anywhere that enumerates the declared types a second time. Adding a
type means adding a parser here; there is no other place that would need to
learn about it, and therefore no other place that can forget.

The predicate table this module used to hold is gone. It answered "is this
already-parsed Python value of type X", which was the right question only while
YAML's implicit resolver decided what a declared value was. It no longer does:
the parser below establishes the type from the scalar text, so a predicate over
the result would restate the parser's own answer and be free to drift from it.

**What is deliberately not here: storability.** `unstorable_attribute` asks
whether a value survives the round trip through JSON and Postgres, which is a
question about the encoder rather than about the domain, and it makes no type
judgement on purpose. Folding it in would put a rule about `bytes` next to a
rule about `numeric` and make both harder to change. See `domain.py`.

**Adding a type means editing one literal, and the pair below is the point.**
`FIELD_TYPES` names the declared type each comparison can receive, or `None`
where no declared value can carry it. A new field type cannot be added without
answering that question, and `tests/kernel/test_attribute_types.py` fails if the
two tables stop agreeing in either direction. The alternative -- three constants
in three modules that happen to compose -- is what this replaces.
"""

import re
from collections.abc import Callable, Mapping


class UnacceptableLiteral(ValueError):
    """Scalar text that is not a valid literal for the declared type.

    Raised by the parsers below and converted to a document error at the
    barricade, which is the only layer that knows which file and line the text
    came from. Deliberately narrower than `ValueError` so that a genuine bug
    inside a parser is not mistaken for a rejected document.
    """


# Reserved scalar keywords are lowercase and exact, here and for the declared
# null the barricade recognises. Accepting `TRUE` while rejecting `NULL` would
# be two answers to one question inside one ruleset. A declared document is
# authored intent, not permissive user input, so there is no case to answer for
# the six spellings YAML would take.
BOOLEAN_LITERALS: Mapping[str, bool] = {"true": True, "false": False}

# Canonical signed decimal, no leading zeros. `007` is refused rather than read
# as 7: if the padding carries meaning the value is an identifier and belongs in
# a `str` field, and truncating it silently is the same defect as reading the
# YAML 1.1 sexagesimal `1:30` as 90. Both are why this parser exists.
INTEGER_LITERAL = re.compile(r"^[+-]?(0|[1-9][0-9]*)$")


def _parse_boolean(text: str) -> bool:
    literal = BOOLEAN_LITERALS.get(text)
    if literal is None:
        raise UnacceptableLiteral(
            f"expected {' or '.join(BOOLEAN_LITERALS)}, got {text!r}; "
            "boolean literals are lowercase and exact"
        )
    return literal


def _parse_integer(text: str) -> int:
    if INTEGER_LITERAL.match(text) is None:
        raise UnacceptableLiteral(
            f"expected a decimal integer, got {text!r}; leading zeros, underscores, "
            "hexadecimal and sexagesimal forms are refused -- if the exact text "
            "matters, declare the attribute as a str"
        )
    try:
        return int(text)
    except ValueError as exc:
        # Matching the grammar is not the same as being convertible. CPython
        # caps int-from-string at `sys.get_int_max_str_digits()` (4300 by
        # default since 3.11) and raises a bare `ValueError` past it, which
        # would escape this barricade entirely and reach the ingestion task's
        # catch-all as a silently dropped revision.
        #
        # The limit is asked for rather than restated as a length bound in the
        # grammar above: it is a runtime setting, so a second copy here would be
        # a number that is right until someone calls `set_int_max_str_digits`.
        raise UnacceptableLiteral(
            f"the integer written has {len(text)} digits, which this interpreter "
            f"will not convert: {exc}"
        ) from exc


def _parse_string(text: str) -> str:
    """The scalar content, unchanged.

    Not "the raw source text": quotes are already removed, escape sequences
    already decoded and block scalars already folded by the time a parser sees
    this. What this promises is that Datum adds no trimming, coercion or
    normalisation of its own on top.
    """
    return text


# What an intent document may write, and how to read each one from its scalar
# text. The parser receives the text alone and never the YAML node, so nothing
# it is given carries the implicit resolver's tag -- which is how "the schema
# decides the type" stays true in practice (issue #55).
#
# **This is a signature, not a capability boundary, and the difference is worth
# stating.** A parser determined to reach the node can still walk the stack to
# its caller's frame and read it; that was demonstrated against this interface
# rather than imagined. What the signature buys is that a parser cannot consult
# the tag by *writing the obvious code*, and that `_stated_value` is the single
# place the choice is made. Against careless data it holds; against a determined
# parser author it does not, and no signature would.
#
# A plain callable rather than a record with one field: there is nothing else a
# declared type needs today, and a record whose only member is the parser is the
# parser wearing a hat.
ATTRIBUTE_TYPES: Mapping[str, Callable[[str], object]] = {
    "int": _parse_integer,
    "str": _parse_string,
    "bool": _parse_boolean,
}

# What `Kind.attribute_schema` may name, and which declared type can carry a
# value for it.
#
# `None` means **discovered-only**: the comparison exists and runs, but no intent
# document can produce a value for its declared side, so the field can only ever
# be compared against an absent declaration. That is a real configuration and
# not an error -- a provider reports container ports as a list whether or not
# anyone declared them -- but it is a fact worth stating as data rather than
# leaving for someone to discover by reading two modules.
#
# `str` appears twice because one declared type genuinely carries two
# comparisons: a timestamp is written as a string and compared as an instant.
FIELD_TYPES: Mapping[str, str | None] = {
    "numeric": "int",
    "string": "str",
    "timestamp": "str",
    "boolean": "bool",
    "list": None,
    "object": None,
}

# Derived, never written twice. `schema.py` validates against the first and
# `documents.py` against the second.
VALID_FIELD_TYPES = frozenset(FIELD_TYPES)
DECLARED_TYPE_NAMES = frozenset(ATTRIBUTE_TYPES)

# The field types no declared value can reach. Named so a caller can say so
# rather than re-deriving it from a `None`.
DISCOVERED_ONLY_FIELD_TYPES = frozenset(
    field_type for field_type, declared in FIELD_TYPES.items() if declared is None
)


__all__ = [
    "ATTRIBUTE_TYPES",
    "BOOLEAN_LITERALS",
    "DECLARED_TYPE_NAMES",
    "DISCOVERED_ONLY_FIELD_TYPES",
    "FIELD_TYPES",
    "INTEGER_LITERAL",
    "UnacceptableLiteral",
    "VALID_FIELD_TYPES",
]
