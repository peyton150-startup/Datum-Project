from django.db import transaction

from datum.discovery.models import DiscoveredResource
from datum.enums import DiscrepancyState, DiscrepancyType, Plane
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import DiscrepancySet, MatchResult, NaturalKey, ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key
from datum.reconcile.models import Discrepancy, Match

# Either plane's row: both carry the natural-key columns and an attributes blob.
ResourceRow = DeclaredResource | DiscoveredResource


@transaction.atomic
def run_reconciliation(tenant_id: str) -> None:
    declared_rows = _active_declared(tenant_id)
    discovered_rows = _present_discovered(tenant_id)
    declared = [_snapshot(row) for row in declared_rows]
    discovered = [_snapshot(row) for row in discovered_rows]

    match_result = match_by_natural_key(declared, discovered)
    discrepancy_set = reconcile(match_result)

    _reset(tenant_id)
    _write_matches(tenant_id, match_result, declared_rows, discovered_rows)
    _write_discrepancies(tenant_id, discrepancy_set)


def _present_discovered(tenant_id: str) -> list[DiscoveredResource]:
    """The discovered plane as it stands now, excluding what is known to be gone.

    An absent row is retained as evidence, not as a current observation. Feeding
    it to the matcher would pair it with its declared counterpart and report no
    difference at all -- the resource would be missing from the estate and
    perfectly reconciled at the same time.
    """
    return list(DiscoveredResource.objects.filter(tenant_id=tenant_id, is_absent=False))


def _active_declared(tenant_id: str) -> list[DeclaredResource]:
    revision = IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if revision is None:
        return []
    return list(DeclaredResource.objects.filter(tenant_id=tenant_id, revision=revision))


def _snapshot(row: ResourceRow) -> ResourceSnapshot:
    return ResourceSnapshot(
        kind=row.kind.name,
        tenant_id=str(row.tenant_id),
        scope=row.scope,
        name=row.name,
        provider_id=row.provider_id,
        attributes=dict(row.attributes),
    )


def _reset(tenant_id: str) -> None:
    Match.objects.filter(tenant_id=tenant_id).delete()
    Discrepancy.objects.filter(tenant_id=tenant_id, state=DiscrepancyState.OPEN).delete()


def _write_matches(
    tenant_id: str,
    match_result: MatchResult,
    declared_rows: list[DeclaredResource],
    discovered_rows: list[DiscoveredResource],
) -> None:
    declared_by_key: dict[NaturalKey, DeclaredResource] = {
        _snapshot(row).natural_key: row for row in declared_rows
    }
    discovered_by_key: dict[NaturalKey, DiscoveredResource] = {
        _snapshot(row).natural_key: row for row in discovered_rows
    }
    for pair in match_result.pairs:
        Match.objects.create(
            tenant_id=tenant_id,
            declared_resource=declared_by_key[pair.declared.natural_key],
            discovered_resource=discovered_by_key[pair.discovered.natural_key],
            strategy=pair.strategy,
            confidence=pair.confidence,
        )


def _write_discrepancies(tenant_id: str, discrepancy_set: DiscrepancySet) -> None:
    for fd in discrepancy_set.field_discrepancies:
        kind, _tenant, scope, name = fd.natural_key
        Discrepancy.objects.create(
            tenant_id=tenant_id,
            discrepancy_type=DiscrepancyType.FIELD,
            kind_name=kind,
            scope=scope,
            name=name,
            field_name=fd.field_name,
            declared_value=fd.declared_value,
            discovered_value=fd.discovered_value,
            authoritative_plane=Plane.DECLARED,
        )
    for orphan in discrepancy_set.orphans:
        kind, _tenant, scope, name = orphan.natural_key
        Discrepancy.objects.create(
            tenant_id=tenant_id,
            discrepancy_type=orphan.discrepancy_type,
            kind_name=kind,
            scope=scope,
            name=name,
            authoritative_plane=Plane.DECLARED,
        )
