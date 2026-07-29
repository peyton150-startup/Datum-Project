"""The collector framework: DESIGN section 11.

Collectors are the robustness zone. The quality objectives rank robustness
first here and explicitly sacrifice strict correctness: a provider will return
junk, time out, and rate-limit, and the collector's job is to keep going and
record what it could not read.

**Why the interface is split the way it is.** An adapter supplies two things: a
`fetch` that reads the whole provider, and a `normalize` that converts exactly
one record. It never sees the loop between them. That is deliberate, and it is
the fix for CF-1, in which the phase 1 collector wrapped a whole-payload parse
in one `try` and so discarded every healthy record that shared a payload with a
bad one. An adapter written against this interface *cannot* reintroduce that
defect, because it is never handed the collection to abort. Per-record failure
is structural here, not a rule an adapter has to remember to follow.

The framework owns everything that must be true of every collector: the run
record, the counts and their invariant, and the boundary that keeps a single
bad record from ending a read.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

from django.utils import timezone

from datum.discovery.errors import MalformedProviderData, ProviderUnavailable
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind
from datum.reconcile.domain import ResourceSnapshot

logger = logging.getLogger(__name__)


class Collector(Protocol):
    """One adapter per provider. Read, normalize, and nothing else.

    A collector is forbidden from writing to the estate, writing to the
    declared plane, deciding what a discrepancy is, or raising on a single bad
    record. The first three are enforced by this module never offering the
    means; the fourth is enforced by `normalize` being per record.
    """

    name: str

    def fetch(self) -> Sequence[object]:
        """Every record the provider returned, raw and unvalidated.

        Raises `ProviderUnavailable` if the provider could not be read at all.
        Must not raise for a single bad record -- judging a record is
        `normalize`'s job, and a record this method cannot judge is still a
        record the run observed.
        """
        ...

    def normalize(self, record: object, tenant_id: str) -> ResourceSnapshot:
        """Convert one provider record to one snapshot, or reject it.

        This is the discovery half of the barricade (ADR-008): raw provider
        shapes stop here and domain types continue inward. Raises
        `MalformedProviderData` for a record that cannot be normalized.
        """
        ...


def run_collector(collector: Collector, tenant_id: str) -> CollectorRun:
    """Read a provider once and persist everything that normalized.

    Always persists what parsed (DESIGN section 11). There is no error ratio
    above which the run is discarded wholesale, because any such threshold would
    be a number nobody can defend, and the failure it would guard against is
    already handled by absence semantics refusing to infer deletion from a run
    that is not SUCCESS.
    """
    run = _start(collector, tenant_id)

    try:
        # Materialized inside the try on purpose: a lazily-yielded record that
        # failed mid-iteration would otherwise escape the loop below and abort a
        # read this framework promises never to abort.
        records = list(collector.fetch())
    except ProviderUnavailable:
        logger.exception(
            "collector %s could not read the provider for tenant %s", collector.name, tenant_id
        )
        return _finish(run, read=0, written=0, errors=0, status=CollectorRunStatus.FAILED)

    kinds_by_name = {kind.name: kind for kind in Kind.objects.all()}
    read = written = errors = 0

    for record in records:
        # Counted before it is judged: `resources_read` is what the provider
        # returned, not what survived. Under-reporting it hides the fact that
        # anything was dropped, which is the silent half of CF-1.
        read += 1
        snapshot = _normalized(collector, record, tenant_id)
        if snapshot is None:
            errors += 1
            continue
        kind = kinds_by_name.get(snapshot.kind)
        if kind is None:
            _log_unknown_kind(collector, snapshot, tenant_id)
            errors += 1
            continue
        _upsert(tenant_id, kind, snapshot, run)
        written += 1

    return _finish(run, read=read, written=written, errors=errors, status=_status_for(errors))


def _start(collector: Collector, tenant_id: str) -> CollectorRun:
    """Open a run, provisionally FAILED.

    FAILED is the honest default rather than SUCCESS: if the process dies
    partway through, the row left behind claims nothing was completed, and
    absence semantics will refuse to read deletion into it. A run that opened
    optimistically would leave a crash looking like a clean read of an empty
    estate -- the exact mass-deletion reading DESIGN section 11 exists to
    prevent.
    """
    return CollectorRun.objects.create(
        tenant_id=tenant_id,
        collector_name=collector.name,
        status=CollectorRunStatus.FAILED,
    )


def _normalized(collector: Collector, record: object, tenant_id: str) -> ResourceSnapshot | None:
    """One record's worth of the barricade. None means rejected, never raised."""
    try:
        return collector.normalize(record, tenant_id)
    except MalformedProviderData:
        # Swallowed deliberately: this is the robustness rule, not an oversight.
        # The rejection is counted by the caller and logged here with the
        # exception's own message, which is the only thing that can identify the
        # record -- one that failed to normalize has no natural key yet.
        logger.warning(
            "collector %s rejected a record for tenant %s", collector.name, tenant_id, exc_info=True
        )
        return None


def _log_unknown_kind(collector: Collector, snapshot: ResourceSnapshot, tenant_id: str) -> None:
    """A normalized snapshot naming a kind that does not exist.

    Counted as a rejection rather than raised, because the robustness rule
    applies to whatever stops one record from being stored, and a run that
    keeps going still records every other resource it saw. Logged at error
    rather than warning because, unlike malformed provider data, this is
    Datum's own configuration being wrong: the kind was never seeded.
    """
    logger.error(
        "collector %s produced kind %r, which is not a known Kind; tenant %s, scope %s, name %s",
        collector.name,
        snapshot.kind,
        tenant_id,
        snapshot.scope,
        snapshot.name,
    )


def _upsert(tenant_id: str, kind: Kind, snapshot: ResourceSnapshot, run: CollectorRun) -> None:
    """Write one observation, keyed on the discovered natural key.

    Idempotent by construction: `uq_discovered_natural_key` plus
    update_or_create means running twice against an unchanged estate produces
    the same rows rather than duplicates.
    """
    DiscoveredResource.objects.update_or_create(
        tenant_id=tenant_id,
        kind=kind,
        scope=snapshot.scope,
        name=snapshot.name,
        defaults={
            "provider_id": snapshot.provider_id,
            "attributes": dict(snapshot.attributes),
            "run": run,
        },
    )


def _finish(run: CollectorRun, read: int, written: int, errors: int, status: str) -> CollectorRun:
    """Close the run with its counts and status."""
    # Interior of the barricade: the loop counts every record into exactly one
    # of written or errors, so a violation here is a bug in this module rather
    # than bad provider data. DESIGN section 11 states it as an invariant and
    # requires it tested as one.
    assert (
        read == written + errors
    ), f"run {run.id} counts do not balance: read={read} != written={written} + errors={errors}"

    run.resources_read = read
    run.resources_written = written
    run.errors = errors
    run.status = status
    run.finished_at = timezone.now()
    run.save(
        update_fields=["resources_read", "resources_written", "errors", "status", "finished_at"]
    )
    return run


def _status_for(errors: int) -> str:
    """A read that happened: PARTIAL if anything was rejected, else SUCCESS.

    Only reachable once `fetch` returned, so a zero-record run here is a
    genuinely empty estate rather than an unreachable provider, and SUCCESS is
    the truthful answer. The unreachable case never arrives here -- it is
    FAILED at the call site, because absence semantics must never be allowed to
    read one outage as the deletion of everything.
    """
    return CollectorRunStatus.PARTIAL if errors else CollectorRunStatus.SUCCESS


__all__ = [
    "Collector",
    "MalformedProviderData",
    "ProviderUnavailable",
    "run_collector",
]
