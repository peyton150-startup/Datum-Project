import json
import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

NaturalKey = tuple[str, str, str, str]  # (kind, tenant_id, scope, name)

T = TypeVar("T")


def canonical(value: object) -> str:
    """The one canonical form, used for both comparison and value identity.

    Lives here rather than in `diff.py` because `PlaneValue` equality is
    defined over it, and `diff` imports `domain` -- the other direction is an
    import cycle. Canonicalization is a property of the value, not of the
    engine that happens to compare values.

    What it decides is deliberately narrow: two values are the same value if
    their canonical forms match, so `0` and `False` differ. What it does *not*
    decide is numeric and deep comparison semantics, which are open questions
    owned by WBS 1.5.2 (DESIGN section 13). Changing this function is how those
    get answered; nothing downstream should re-derive its own version.

    No `default=`, so a value JSON cannot represent raises here rather than
    being stringified into agreement with the literal string of its `__str__`
    (issue #47). That collision was real: `canonical({"a": Weird()})` equalled
    `canonical({"a": "weird-str"})`, and `PlaneValue` equality agreed.

    Raising is safe *because* both planes screen attributes against
    `unstorable_attribute` below before anything is written -- the declared side
    in `intent/documents.py`, the discovered side in `discovery/collector.py`.
    Without those, this would be a crash where there used to be a wrong answer,
    which is louder but not a fix. A `TypeError` from here therefore means a
    barricade was bypassed, not that a provider sent something unusual.
    """
    return json.dumps(value, sort_keys=True)


# --- What a plane may state about a field (issue #47) -------------------------
#
# Every attribute value reaching kernel comparison is JSON-native, recursively:
# leaves are `str`, `int`, `float`, `bool`, or `None`, and containers are `list`
# or `dict` with string keys whose contents satisfy the same rule.
#
# `canonical` is defined over this, and so are `PlaneValue` equality and hashing,
# the opaque structure hash, the `recurse(N)` modes, and the keyed
# `version`/`identity` comparisons.
#
# **It lives here because both planes need it, and a rule both planes need must
# not be written twice.** It was: the discovered side had this walk and the
# declared side had a type table that never looked at string contents, so a NUL
# in a declared string passed validation and raised `DataError` out of ingestion
# -- the same class the discovered-side barricade exists to close, reached
# through the door nobody had checked. Writing a second copy into
# `intent/documents.py` would have made both correct today and free to drift
# tomorrow, which is the shape CLAUDE.md names as this project's most productive
# bug family.
#
# Two rules here are not what "JSON-native" suggests, and both were measured
# against the real database rather than reasoned about:
#
# - **A non-finite float is refused.** `json.dumps` emits bare `NaN` and
#   `Infinity`, which are not valid JSON, and Postgres rejects them. A float is
#   JSON-native right up until it is not, and the failure arrives from the driver
#   rather than the encoder.
# - **A mapping key of the wrong type is refused.** `json.dumps` coerces
#   silently: `{1: "a"}` stores and reads back as `{"1": "a"}`, and
#   `{True: "a"}` as `{"true": "a"}`. Two structurally different objects become
#   one value with no error anywhere -- the case a guard written from a
#   traceback would miss, because there is no traceback.
#
#   **A key is checked for its contents as well as its type** (issue #56). It
#   was not, and the split was what hid it: the type question was asked where
#   keys are enumerated and the contents question in the walk, which never
#   visits a key. Both now live in `_unusable_key`, so the next rule about keys
#   has one place to go rather than two to remember.
#
# And one type is judged by its contents rather than by what it is: see
# `_unstorable_text`.
#
# The walk is iterative. A boundary that converts a crash into a domain error
# must not raise `RecursionError` on a deeply nested payload, which is the same
# defect in a new hat.

_JSON_NATIVE_SCALARS = (bool, int)

# Postgres `text`, which backs every string inside a `jsonb`, cannot hold a NUL.
# `json.dumps` escapes it to a six-character sequence quite happily and the
# driver then refuses the result.
_NUL = chr(0)


def unstorable_attribute(attributes: Mapping[str, object]) -> str | None:
    """Describe the first attribute that could not survive storage, or None.

    "First" means shallowest, and among equally shallow ones, first in insertion
    order -- the walk is breadth-first. It is not "first in insertion order"
    outright: a top-level `bytes` is reported ahead of a problem nested inside an
    earlier attribute. Stated precisely because the looser phrasing was here
    first and the test could not tell the two apart.

    The attribute names are checked for the same reason nested mapping keys are.
    `ResourceSnapshot.attributes` is annotated `Mapping[str, object]`, which is a
    promise the type checker cannot keep about a dict built at runtime.

    **The encoder is asked before the walk runs, and that ordering is the whole
    design.** See `_unencodable`.
    """
    unusable = _unusable_key(attributes)
    if unusable is not None:
        key, reason = unusable
        return f"the attribute name {key!r} {reason}"

    unencodable = _unencodable(attributes)
    if unencodable is not None:
        return unencodable

    pending: deque[tuple[str, object]] = deque(attributes.items())
    while pending:
        path, value = pending.popleft()
        problem, children = _inspected(path, value)
        if problem is not None:
            return problem
        pending.extend(children)
    return None


def _unencodable(attributes: Mapping[str, object]) -> str | None:
    """What the JSON encoder itself refuses. Asked, not modelled.

    The walk below started life as a description of what `json.dumps` accepts,
    and a description is a second encoding of a rule the encoder already owns.
    It drifted immediately, in three ways that only appeared under review:

    - **Depth.** `json.dumps` recurses, so a structure past the recursion limit
      raises `RecursionError` while the iterative walk said nothing. The walk was
      made iterative precisely so a deep payload could not crash it, and that
      turned it into the one component in the chain that survived input the next
      component could not.
    - **Cycles.** `json.dumps` detects them and raises cleanly. The walk looped
      forever, holding the collector's advisory lock -- a hang traded for a
      crash, which is the worse of the two.
    - **Magnitude.** `10**5000` is an `int` and passes any type check, then
      raises `ValueError` on the 4300-digit conversion limit.

    None of those three are `django.db.Error`, and psycopg serializes the JSONB
    parameter **client-side, before any SQL is sent**, so none of them were
    caught by the write's own guard either. They escaped the collector loop and
    took every later record in the batch with them.

    So this runs first and the walk runs second, on a structure the encoder has
    already proved finite, acyclic, and serializable. The walk's job is now
    exactly the documented gap between "JSON accepts it" and "Postgres accepts
    it": NULs, unpaired surrogates, non-finite floats, and mapping keys JSON
    would silently coerce.

    The cost, stated rather than hidden: for a value of a type JSON cannot
    represent at all, the message names the type but not the path, because the
    encoder does not report one. A path is not worth a hang.
    """
    try:
        canonical(attributes)
    except (TypeError, ValueError, RecursionError) as exc:
        return f"the JSON encoder refused it: {exc}"
    return None


def _unusable_key(keys: Iterable[object]) -> tuple[object, str] | None:
    """The first key that cannot be used and why, or None when every key can.

    **Both halves in one function on purpose.** A key must be a string *and* be
    a storable one, and the previous version checked only the first. That gap
    was invisible precisely because it was split: the type question was asked
    here and the contents question was asked in the walk, which never visits a
    key. A NUL inside a key therefore passed both barricades and was refused by
    Postgres, which reports the driver's text instead of a path (issue #56).

    One encoding, two callers: an attribute name and a nested mapping key are
    the same constraint reported in two different sentences, and the constraint
    is the part that must not drift. The reasons read as predicates so either
    caller can put its own subject in front of them.

    Takes the keys rather than the mapping because `Mapping` is invariant in its
    key type, so a `Mapping[str, object]` is not a `Mapping[object, object]` --
    and iterating is all this needs.
    """
    for key in keys:
        if not isinstance(key, str):
            return (
                key,
                f"is {type(key).__name__} rather than a string, "
                "and JSON silently renders it as one",
            )
        problem = _text_problem(key)
        if problem is not None:
            return (key, problem)
    return None


def _inspected(path: str, value: object) -> tuple[str | None, list[tuple[str, object]]]:
    """One value: what is wrong with it, and what it holds that still needs looking at."""
    if value is None or isinstance(value, _JSON_NATIVE_SCALARS):
        return (None, [])
    if isinstance(value, str):
        return (_unstorable_text(path, value), [])
    if isinstance(value, float):
        if not math.isfinite(value):
            return (f"{path} is {value!r}, which JSON cannot represent", [])
        return (None, [])
    if isinstance(value, list | tuple):
        return (None, [(f"{path}[{index}]", item) for index, item in enumerate(value)])
    if isinstance(value, dict):
        return _inspected_mapping(path, value)
    return (f"{path} is {type(value).__name__}, which is not a JSON type", [])


def _unstorable_text(path: str, value: str) -> str | None:
    """A string value JSON accepts that Postgres will not store, or None."""
    problem = _text_problem(value)
    return None if problem is None else f"{path} {problem}"


def _text_problem(value: str) -> str | None:
    """Why this string cannot survive storage, as a predicate, or None.

    The gap this closes: every other type here is judged by what it *is*, and a
    string was waved through on that basis while being the one type whose
    *contents* can fail. `json.dumps` escapes a NUL and an unpaired surrogate
    without complaint, and the driver then rejects both.

    Pathless on purpose, so a key and a value can ask the same question. A key
    is not addressable by the path that would name its value, and giving each
    its own copy of this rule is how the key half came to be missing.
    """
    if _NUL in value:
        return "contains a NUL, which Postgres cannot store in text"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "contains an unpaired surrogate, which is not valid UTF-8"
    return None


def _inspected_mapping(
    path: str, value: dict[object, object]
) -> tuple[str | None, list[tuple[str, object]]]:
    """A mapping's keys must be usable before its values are worth walking."""
    unusable = _unusable_key(value)
    if unusable is not None:
        key, reason = unusable
        return (f"{path} has key {key!r}, which {reason}", [])
    return (None, [(f"{path}.{key}", item) for key, item in value.items()])


@dataclass(frozen=True, eq=False)
class PlaneValue:
    """One plane's statement about one field: whether it says anything, and what.

    Absence and null are two facts, not one. A field a plane never mentions and
    a field it explicitly sets to null are different claims, and collapsing them
    into `None` is the defect this type exists to prevent (DESIGN section 13).

    Construct through `absent()` and `of()`. There is no public value accessor:
    reading goes through `resolve`, which cannot be called without saying what
    absence does. That does not make the collapse impossible -- `on_absent`
    can always return `None`, and at the database layer that is the correct
    answer -- but it makes the decision visible at every call site, where a
    reviewer can see it. The private field is held by ruff's SLF rule rather
    than by convention.
    """

    _present: bool
    _value: object

    def __post_init__(self) -> None:
        if not self._present and self._value is not None:
            raise ValueError(
                f"an absent PlaneValue cannot carry a value, got {self._value!r}; "
                "use PlaneValue.absent()"
            )

    @classmethod
    def absent(cls) -> "PlaneValue":
        return cls(_present=False, _value=None)

    @classmethod
    def of(cls, value: object) -> "PlaneValue":
        return cls(_present=True, _value=value)

    def resolve(self, *, on_absent: Callable[[], T], on_present: Callable[[object], T]) -> T:
        if not self._present:
            return on_absent()
        return on_present(self._value)

    def as_columns(self) -> tuple[bool, object]:
        """The storage pair, so the absent-implies-NULL rule is stated once.

        The database holds the same invariant as a check constraint. Defining
        the pair here means the two cannot disagree, and spares every write
        site a hand-written lambda that discards its argument.
        """
        return (self._present, self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlaneValue):
            return NotImplemented
        return (self._present, canonical(self._value)) == (other._present, canonical(other._value))

    def __hash__(self) -> int:
        return hash((self._present, canonical(self._value)))

    def __repr__(self) -> str:
        if not self._present:
            return "PlaneValue.absent()"
        return f"PlaneValue.of({self._value!r})"


@dataclass(frozen=True)
class ResourceSnapshot:
    kind: str
    tenant_id: str
    scope: str
    name: str
    provider_id: str | None
    attributes: Mapping[str, object]

    @property
    def natural_key(self) -> NaturalKey:
        return (self.kind, self.tenant_id, self.scope, self.name)

    def plane_value(self, field_name: str) -> PlaneValue:
        """This plane's statement about one field, absence included."""
        if field_name not in self.attributes:
            return PlaneValue.absent()
        return PlaneValue.of(self.attributes[field_name])


@dataclass(frozen=True)
class MatchDecision:
    """A human's standing decision about one pairing, as the matcher sees it.

    Anchored on durable facts rather than row identity, because neither plane's
    rows are durable: `DeclaredResource` is rebuilt per intent revision, so a
    foreign key to one binds a decision to a commit rather than to a resource
    (CF-6). The declared side is anchored on its natural key as of the decision;
    the discovered side on the provider's own identifier, which survives the
    rename that breaks the natural key -- which is the whole reason a stored
    binding outranks one.

    `is_confirmed` False means rejected: a human said these are not the same
    resource, and the matcher must not propose the pairing again.
    """

    declared_key: NaturalKey
    provider_id: str
    is_confirmed: bool

    @property
    def pairing(self) -> tuple[NaturalKey, str]:
        return (self.declared_key, self.provider_id)


@dataclass(frozen=True)
class MatchedPair:
    declared: ResourceSnapshot
    discovered: ResourceSnapshot
    strategy: str
    confidence: str


@dataclass(frozen=True)
class MatchResult:
    pairs: tuple[MatchedPair, ...]
    declared_orphans: tuple[ResourceSnapshot, ...]
    discovered_orphans: tuple[ResourceSnapshot, ...]


@dataclass(frozen=True)
class FieldDiscrepancy:
    natural_key: NaturalKey
    field_name: str
    declared: PlaneValue
    discovered: PlaneValue


@dataclass(frozen=True)
class OrphanDiscrepancy:
    natural_key: NaturalKey
    discrepancy_type: str


@dataclass(frozen=True)
class DiscrepancySet:
    field_discrepancies: tuple[FieldDiscrepancy, ...]
    orphans: tuple[OrphanDiscrepancy, ...]


__all__ = [
    "NaturalKey",
    "PlaneValue",
    "canonical",
    "ResourceSnapshot",
    "MatchDecision",
    "MatchedPair",
    "MatchResult",
    "FieldDiscrepancy",
    "OrphanDiscrepancy",
    "DiscrepancySet",
    "Sequence",
]
