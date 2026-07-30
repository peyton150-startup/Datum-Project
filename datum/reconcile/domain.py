import json
from collections.abc import Callable, Mapping, Sequence
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
    """
    return json.dumps(value, sort_keys=True, default=str)


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
