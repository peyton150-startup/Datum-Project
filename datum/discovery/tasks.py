"""Scheduled discovery (WBS 1.4.1).

Collectors run on a schedule, like intent polling and for the same reason: no
inbound route, no shared secret, and nothing to expose through the free-tier
host. The cost is bounded staleness -- drift between a change in the estate and
its observation is at most one collection interval.

The task is a trigger, not a policy. Everything about what a run means lives in
`collector`; this module only decides when one starts and refuses to let a
failure take the schedule down with it.
"""

import logging

from celery import shared_task
from django.conf import settings

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import KubernetesCollector, from_cluster, from_recording
from datum.discovery.oci import from_recording as oci_from_recording

logger = logging.getLogger(__name__)

CLUSTER_MODE = "cluster"


def _configured_collector() -> KubernetesCollector | None:
    """The collector this deployment is configured for, or None for neither.

    Cluster mode needs no source path -- credentials come from the environment --
    so it is checked first. Recorded mode needs one, and an unset path is the
    default "nothing configured" rather than an error.
    """
    if settings.KUBERNETES_MODE == CLUSTER_MODE:
        return from_cluster(settings.KUBERNETES_NAMESPACE)
    if settings.KUBERNETES_SOURCE:
        return from_recording(settings.KUBERNETES_SOURCE)
    return None


@shared_task(name="datum.discovery.collect_kubernetes")
def collect_kubernetes() -> int | None:
    """Run the Kubernetes collector once and return its run id.

    Returns None when the collector is not configured, which is the default:
    no cluster mode and no recorded source means "no cluster to read", and the
    task logs and does nothing rather than failing every interval.

    Never raises. `run_collector` already turns an unreachable provider into a
    FAILED run and bad records into counted rejections, so anything escaping it
    is a bug in Datum rather than a condition the schedule should die on --
    and a scheduled task that dies takes every later run with it.
    """
    tenant_id = settings.DEFAULT_TENANT_ID
    try:
        # Inside the try, not before it. Choosing a collector is cheap but it is
        # still configuration being read, and "never raises" has to mean never
        # rather than never-once-the-easy-part-is-done.
        collector = _configured_collector()
        if collector is None:
            logger.info("kubernetes collector not configured; skipping collection")
            return None

        run = run_collector(collector, tenant_id)
    except Exception:
        logger.exception(
            "kubernetes collection failed unexpectedly for tenant %s; "
            "the schedule continues and the next run retries",
            tenant_id,
        )
        return None

    if run is None:
        # Skipped: another run of this collector held the lock. Not a failure and
        # not a run, so there is no id to return and nothing to log here -- the
        # skip is already counted against the run that caused it.
        #
        # This branch was missing when 1.4.4 gave `run_collector` a None return,
        # and the log line below is outside the try, so the first overlapping
        # tick would have raised AttributeError straight out of a task whose
        # contract is that it never raises.
        return None

    logger.info(
        "kubernetes collection finished: run=%s status=%s read=%s written=%s errors=%s gap=%s",
        run.id,
        run.status,
        run.resources_read,
        run.resources_written,
        run.errors,
        run.has_gap,
    )
    return int(run.id)


@shared_task(name="datum.discovery.collect_oci")
def collect_oci() -> int | None:
    """Run the Oracle Cloud collector once and return its run id.

    Recorded payloads only, per DESIGN section 11: the live read waits for
    credentials, and the normalizer -- the part that can be wrong -- is fully
    testable without them.

    Same contract as the Kubernetes task and for the same reasons: returns None
    when unconfigured, and never raises, because a scheduled task that dies
    takes every later run with it. The two share no code beyond `run_collector`
    on purpose; a generic "collect anything" task would have to know which
    settings belong to which provider, which is exactly the coupling the
    collector interface exists to avoid.
    """
    if not settings.OCI_SOURCE:
        logger.info("oci collector not configured; skipping collection")
        return None

    tenant_id = settings.DEFAULT_TENANT_ID
    try:
        run = run_collector(oci_from_recording(settings.OCI_SOURCE), tenant_id)
    except Exception:
        logger.exception(
            "oci collection failed unexpectedly for tenant %s; "
            "the schedule continues and the next run retries",
            tenant_id,
        )
        return None

    if run is None:
        return None

    logger.info(
        "oci collection finished: run=%s status=%s read=%s written=%s errors=%s gap=%s",
        run.id,
        run.status,
        run.resources_read,
        run.resources_written,
        run.errors,
        run.has_gap,
    )
    return int(run.id)
