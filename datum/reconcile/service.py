from django.db import transaction
from django.utils import timezone

from datum.discovery.models import DiscoveredResource
from datum.enums import (
    ACTIVE_MATCH_STATES,
    HUMAN_MATCH_STATES,
    DiscrepancyState,
    DiscrepancyType,
    MatchState,
    Plane,
)
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import DiscrepancySet, MatchDecision, MatchResult, NaturalKey, ResourceSnapshot
from datum.reconcile.matcher import match_resources
from datum.reconcile.models import Discrepancy, Match

# Either plane's row: both carry the natural-key columns and an attributes blob.
ResourceRow = DeclaredResource | DiscoveredResource


@transaction.atomic
def run_reconciliation(tenant_id: str) -> None:
    declared_rows = _active_declared(tenant_id)
    discovered_rows = _present_discovered(tenant_id)
    declared = [_snapshot(row) for row in declared_rows]
    discovered = [_snapshot(row) for row in discovered_rows]

    decisions = _stored_decisions(tenant_id)
    match_result = match_resources(declared, discovered, decisions)
    discrepancy_set = reconcile(match_result)

    _reset(tenant_id)
    # Before writing, not after. A decision whose anchor vanished frees that
    # resource to be matched some other way this run, and the new row would
    # collide with the stale one on the active-state unique index.
    _invalidate_unanchored(tenant_id, declared, discovered)
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


def _stored_decisions(tenant_id: str) -> list[MatchDecision]:
    """Every standing human decision, as the matcher's domain view of it.

    Terminal states are excluded: an invalidated decision is history, and
    reading it back would resurrect a binding whose resource is gone.
    """
    rows = Match.objects.filter(tenant_id=tenant_id, state__in=HUMAN_MATCH_STATES)
    return [
        MatchDecision(
            declared_key=(row.declared_kind, str(tenant_id), row.declared_scope, row.declared_name),
            # The check constraint guarantees a human decision has one.
            provider_id=assert_provider_id(row.discovered_provider_id),
            is_confirmed=row.state == MatchState.CONFIRMED,
        )
        for row in rows
    ]


def assert_provider_id(provider_id: str | None) -> str:
    assert provider_id is not None, "a confirmed or rejected match must carry a provider id"
    return provider_id


def _reset(tenant_id: str) -> None:
    """Clear what a run rebuilds, and nothing a human authored.

    Proposals and open discrepancies are recomputed from the two planes every
    run. Confirmed and rejected matches are inputs to that computation, and
    deleting them was CF-6.
    """
    Match.objects.filter(tenant_id=tenant_id, state=MatchState.PROPOSED).delete()
    Discrepancy.objects.filter(tenant_id=tenant_id, state=DiscrepancyState.OPEN).delete()


def _invalidate_unanchored(
    tenant_id: str,
    declared: list[ResourceSnapshot],
    discovered: list[ResourceSnapshot],
) -> None:
    """Retire every human decision whose subject no longer exists.

    Terminal and one-way: a resource that comes back is proposed afresh rather
    than inheriting a decision made about the instance that left (DESIGN 12).
    `INVALIDATED` is kept rather than deleted because it is the audit record of
    why a confirmed match stopped holding.
    """
    declared_keys = {snap.natural_key for snap in declared}
    provider_ids = {snap.provider_id for snap in discovered}
    stale = [
        row.pk
        for row in Match.objects.filter(tenant_id=tenant_id, state__in=HUMAN_MATCH_STATES)
        if (row.declared_kind, str(tenant_id), row.declared_scope, row.declared_name)
        not in declared_keys
        or row.discovered_provider_id not in provider_ids
    ]
    Match.objects.filter(pk__in=stale).update(
        state=MatchState.INVALIDATED, invalidated_at=timezone.now()
    )


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
        declared_present, declared_value = fd.declared.as_columns()
        discovered_present, discovered_value = fd.discovered.as_columns()
        Discrepancy.objects.create(
            tenant_id=tenant_id,
            discrepancy_type=DiscrepancyType.FIELD,
            kind_name=kind,
            scope=scope,
            name=name,
            field_name=fd.field_name,
            declared_present=declared_present,
            declared_value=declared_value,
            discovered_present=discovered_present,
            discovered_value=discovered_value,
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
