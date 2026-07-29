"""Absence semantics: the rule that prevents mass deletion (DESIGN section 11).

A resource missing from a run means one of two things and the collector cannot
tell them apart: it was deleted, or this run failed to read it. The whole of
this module exists to keep those two apart.

**Only a SUCCESS run without a gap may infer absence.** Getting this wrong means
Datum reporting that production resources were deleted when they were merely
unread -- one bad afternoon at a provider reading as the deletion of an estate.

**Inferring absence requires a complete read. Refuting it does not.** Absence is
inferred from silence, and only a complete read makes silence mean anything. But
a resource that was actually observed is direct evidence that it exists, and
that evidence is not weakened because some other record in the same payload was
malformed. So the clearing half of this module runs per observation, from any
run at all, while the marking half runs only after a complete one.
"""

import logging

from django.db.models import QuerySet
from django.utils import timezone

from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus

logger = logging.getLogger(__name__)


def may_infer_absence(run: CollectorRun) -> bool:
    """Whether this run is entitled to conclude that anything is gone.

    Written as a positive whitelist rather than as `!= FAILED`. The difference
    matters: a blacklist silently admits every status added later, and the cost
    of wrongly admitting one is reporting live resources as deleted.
    """
    if run.status != CollectorRunStatus.SUCCESS:
        return False
    # Belt as well as braces. A run cannot presently be SUCCESS while carrying a
    # gap -- the framework forces PARTIAL -- but `has_gap` is the more specific
    # signal, and if the two ever combine, absence must refuse rather than
    # quietly become wrong.
    return not run.has_gap


def mark_absent_after(run: CollectorRun) -> int:
    """Mark what `run` should have seen and did not. Returns the count marked.

    Absence is recorded, never destructive: rows are flagged, not deleted, so
    history survives and `reconcile` can still raise a "declared, missing"
    discrepancy. Deleting them would destroy the evidence the review queue
    exists to show.
    """
    if not may_infer_absence(run):
        logger.info(
            "run %s (%s, gap=%s) may not infer absence; nothing marked",
            run.id,
            run.status,
            run.has_gap,
        )
        return 0

    marked = _unseen_by(run).update(is_absent=True, absent_since=timezone.now())
    if marked:
        logger.info("run %s marked %s resource(s) absent", run.id, marked)
    return marked


def _unseen_by(run: CollectorRun) -> QuerySet[DiscoveredResource]:
    """Rows this run was responsible for and did not observe.

    Responsibility is `(tenant, collector)`, which is sound only because a
    collector owns exactly one kind -- an invariant the framework asserts rather
    than assumes. See DESIGN open question 8 for what a multi-kind collector
    would need instead.

    Already-absent rows are excluded so `absent_since` records when a resource
    went missing rather than when it was last confirmed still missing.
    """
    return DiscoveredResource.objects.filter(
        tenant_id=run.tenant_id,
        last_seen_run__collector_name=run.collector_name,
        is_absent=False,
    ).exclude(last_seen_run=run)


__all__ = ["mark_absent_after", "may_infer_absence"]
