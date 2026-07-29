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
from datum.discovery.kubernetes import KubernetesCollector

logger = logging.getLogger(__name__)


@shared_task(name="datum.discovery.collect_kubernetes")
def collect_kubernetes() -> int | None:
    """Run the Kubernetes collector once and return its run id.

    Returns None when the collector is not configured, which is the default:
    an unset source means "no cluster to read", and the task logs and does
    nothing rather than failing every interval.

    Never raises. `run_collector` already turns an unreachable provider into a
    FAILED run and bad records into counted rejections, so anything escaping it
    is a bug in Datum rather than a condition the schedule should die on --
    and a scheduled task that dies takes every later run with it.
    """
    if not settings.KUBERNETES_SOURCE:
        logger.info("kubernetes collector not configured; skipping collection")
        return None

    tenant_id = settings.DEFAULT_TENANT_ID
    collector = KubernetesCollector(settings.KUBERNETES_SOURCE)
    try:
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
        "kubernetes collection finished: run=%s status=%s read=%s written=%s errors=%s",
        run.id,
        run.status,
        run.resources_read,
        run.resources_written,
        run.errors,
    )
    return int(run.id)
