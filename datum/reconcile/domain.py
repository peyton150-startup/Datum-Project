from collections.abc import Mapping, Sequence
from dataclasses import dataclass

NaturalKey = tuple[str, str, str, str]  # (kind, tenant_id, scope, name)


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
    declared_value: object
    discovered_value: object


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
    "ResourceSnapshot",
    "MatchedPair",
    "MatchResult",
    "FieldDiscrepancy",
    "OrphanDiscrepancy",
    "DiscrepancySet",
    "Sequence",
]
