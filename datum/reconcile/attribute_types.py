"""What type an attribute may be, stated once (issue #53).

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

from collections.abc import Callable, Mapping

# What an intent document may write, and how to recognise it.
#
# `type(v) is int` rather than isinstance, deliberately: bool is a subclass of
# int in Python, so isinstance would quietly accept `replicas: true` as an
# integer. A declaration that says "three replicas" and a declaration that says
# "yes replicas" must not validate the same way.
DECLARED_TYPES: Mapping[str, Callable[[object], bool]] = {
    "int": lambda value: type(value) is int,
    "str": lambda value: type(value) is str,
    "bool": lambda value: type(value) is bool,
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
DECLARED_TYPE_NAMES = frozenset(DECLARED_TYPES)

# The field types no declared value can reach. Named so a caller can say so
# rather than re-deriving it from a `None`.
DISCOVERED_ONLY_FIELD_TYPES = frozenset(
    field_type for field_type, declared in FIELD_TYPES.items() if declared is None
)


__all__ = [
    "DECLARED_TYPES",
    "DECLARED_TYPE_NAMES",
    "DISCOVERED_ONLY_FIELD_TYPES",
    "FIELD_TYPES",
    "VALID_FIELD_TYPES",
]
