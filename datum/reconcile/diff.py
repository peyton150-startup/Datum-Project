import json

from datum.enums import DiscrepancyType
from datum.reconcile.domain import (
    DiscrepancySet,
    FieldDiscrepancy,
    MatchedPair,
    MatchResult,
    OrphanDiscrepancy,
    ResourceSnapshot,
)

_ABSENT = object()


def reconcile(match_result: MatchResult) -> DiscrepancySet:
    field_discrepancies: list[FieldDiscrepancy] = []
    for pair in sorted(match_result.pairs, key=lambda p: p.declared.natural_key):
        field_discrepancies.extend(_field_discrepancies(pair))

    orphans = tuple(
        _orphans(match_result.declared_orphans, DiscrepancyType.DECLARED_MISSING.value)
        + _orphans(match_result.discovered_orphans, DiscrepancyType.DISCOVERED_UNDECLARED.value)
    )
    return DiscrepancySet(tuple(field_discrepancies), orphans)


def _field_discrepancies(pair: MatchedPair) -> list[FieldDiscrepancy]:
    keys = sorted(set(pair.declared.attributes) | set(pair.discovered.attributes))
    result: list[FieldDiscrepancy] = []
    for key in keys:
        declared_value = pair.declared.attributes.get(key, _ABSENT)
        discovered_value = pair.discovered.attributes.get(key, _ABSENT)
        if _canonical(declared_value) != _canonical(discovered_value):
            result.append(
                FieldDiscrepancy(
                    natural_key=pair.declared.natural_key,
                    field_name=key,
                    declared_value=_present(declared_value),
                    discovered_value=_present(discovered_value),
                )
            )
    return result


def _orphans(
    snapshots: tuple[ResourceSnapshot, ...], discrepancy_type: str
) -> list[OrphanDiscrepancy]:
    ordered = sorted(snapshots, key=lambda s: s.natural_key)
    return [
        OrphanDiscrepancy(natural_key=s.natural_key, discrepancy_type=discrepancy_type)
        for s in ordered
    ]


def _canonical(value: object) -> str:
    if value is _ABSENT:
        return "\0absent"
    return json.dumps(value, sort_keys=True, default=str)


def _present(value: object) -> object:
    return None if value is _ABSENT else value
