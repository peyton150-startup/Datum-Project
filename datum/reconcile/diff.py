from collections.abc import Mapping

from datum.enums import DiscrepancyType
from datum.reconcile.audit import AuditLogWriter
from datum.reconcile.comparison import compare_field
from datum.reconcile.domain import (
    DiscrepancySet,
    FieldDiscrepancy,
    MatchedPair,
    MatchResult,
    OrphanDiscrepancy,
    ResourceSnapshot,
)
from datum.reconcile.schema import ComparisonSchema, MissingFieldConfig


def reconcile(
    match_result: MatchResult,
    schema_map: Mapping[str, ComparisonSchema],
    audit_writer: AuditLogWriter | None = None,
) -> DiscrepancySet:
    """Turn matched pairs and orphans into the set of differences between planes.

    Each pair is compared over the union of both sides' attribute keys, so a key
    present on only one side is a difference rather than being skipped. Orphans
    become `DECLARED_MISSING` (declared, never provisioned) or
    `DISCOVERED_UNDECLARED` (found in the estate, nobody asked for it).

    Deterministic: pairs are walked in natural-key order, attribute keys in
    sorted order, and orphans in natural-key order within each direction.

    An absent key and an explicit null are compared *and reported* distinctly:
    each side of a field is a `PlaneValue` carrying presence alongside value.
    Configured comparisons decide drift. Missing policy is an undecidable
    queue item, even when the plane values happen to look equal. The optional
    run-scoped writer observes comparisons without influencing their verdict.
    """
    field_discrepancies: list[FieldDiscrepancy] = []
    for pair in sorted(match_result.pairs, key=lambda p: p.declared.natural_key):
        field_discrepancies.extend(_field_discrepancies(pair, schema_map, audit_writer))

    orphans = tuple(
        _orphans(match_result.declared_orphans, DiscrepancyType.DECLARED_MISSING.value)
        + _orphans(match_result.discovered_orphans, DiscrepancyType.DISCOVERED_UNDECLARED.value)
    )
    return DiscrepancySet(tuple(field_discrepancies), orphans)


def _field_discrepancies(
    pair: MatchedPair,
    schema_map: Mapping[str, ComparisonSchema],
    audit_writer: AuditLogWriter | None,
) -> list[FieldDiscrepancy]:
    assert pair.declared.kind == pair.discovered.kind, "matched kinds disagree"
    schema = schema_map.get(pair.declared.kind)
    keys = sorted(set(pair.declared.attributes) | set(pair.discovered.attributes))
    result: list[FieldDiscrepancy] = []
    for key in keys:
        declared = pair.declared.plane_value(key)
        discovered = pair.discovered.plane_value(key)
        discrepancy_type = _comparison_outcome(pair, key, schema, audit_writer)
        if discrepancy_type is not None:
            result.append(
                FieldDiscrepancy(
                    natural_key=pair.declared.natural_key,
                    field_name=key,
                    declared=declared,
                    discovered=discovered,
                    discrepancy_type=discrepancy_type,
                )
            )
    return result


def _comparison_outcome(
    pair: MatchedPair,
    key: str,
    schema: ComparisonSchema | None,
    audit_writer: AuditLogWriter | None,
) -> str | None:
    if schema is None:
        return DiscrepancyType.MISSING_COMPARISON_POLICY.value
    assert schema.kind_name == pair.declared.kind, "comparison schema identity disagrees"
    try:
        config = schema.get_field_config(key)
    except MissingFieldConfig:
        return DiscrepancyType.MISSING_COMPARISON_POLICY.value
    agrees, entry = compare_field(
        pair.declared.plane_value(key), pair.discovered.plane_value(key), config
    )
    if audit_writer is not None:
        audit_writer.write(entry, config)
    return None if agrees else DiscrepancyType.FIELD.value


def _orphans(
    snapshots: tuple[ResourceSnapshot, ...], discrepancy_type: str
) -> list[OrphanDiscrepancy]:
    ordered = sorted(snapshots, key=lambda s: s.natural_key)
    return [
        OrphanDiscrepancy(natural_key=s.natural_key, discrepancy_type=discrepancy_type)
        for s in ordered
    ]
