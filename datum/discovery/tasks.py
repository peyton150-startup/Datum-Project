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

        # WBS 1.4.4 adds the one-run-per-collector-per-tenant lock here. Until
        # it does, two overlapping runs race on the same rows harmlessly: the
        # upsert is keyed on the discovered natural key, so the loser rewrites
        # the same values rather than duplicating them.
        run = run_collector(collector, tenant_id)
    except Exception:
        logger.exception(
            "kubernetes collection failed unexpectedly for tenant %s; "
            "the schedule continues and the next run retries",
            tenant_id,
        )
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
