"""Scheduled discovery (WBS 1.4.1).

The task's whole contract is that it never raises: a scheduled task that dies
takes every later run with it, so each failure mode here has to end in a return
rather than an exception.
"""

import pytest

from datum.discovery.models import CollectorRun
from datum.discovery.tasks import collect_kubernetes
from datum.enums import CollectorRunStatus

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
MULTI_FIXTURE = "fixtures/k8s/deployments-multi.json"

pytestmark = pytest.mark.django_db


def test_unconfigured_source_skips_without_running_anything(settings):
    """The default. An unset source means there is no cluster to read, which is
    not an error and must not record a run."""
    settings.KUBERNETES_SOURCE = ""

    assert collect_kubernetes() is None
    assert not CollectorRun.objects.exists()


def test_configured_source_runs_the_collector_and_returns_the_run_id(settings):
    settings.KUBERNETES_SOURCE = FIXTURE

    run_id = collect_kubernetes()

    run = CollectorRun.objects.get(id=run_id)
    assert run.status == CollectorRunStatus.SUCCESS
    assert run.resources_written == 1


def test_a_partial_read_is_still_a_completed_task(settings):
    """A partial run is a valid outcome, not a task failure. The task returns
    the run so the counts are reachable rather than swallowing them."""
    settings.KUBERNETES_SOURCE = MULTI_FIXTURE

    run = CollectorRun.objects.get(id=collect_kubernetes())

    assert run.status == CollectorRunStatus.PARTIAL
    assert (run.resources_read, run.resources_written, run.errors) == (3, 2, 1)


def test_an_unreachable_provider_does_not_raise_out_of_the_task(settings, tmp_path):
    """The provider being down is the ordinary case this schedule exists to
    survive: a FAILED run is recorded and the next tick still fires."""
    settings.KUBERNETES_SOURCE = str(tmp_path / "absent.json")

    run = CollectorRun.objects.get(id=collect_kubernetes())

    assert run.status == CollectorRunStatus.FAILED


def test_an_unexpected_failure_is_swallowed_rather_than_killing_the_schedule(settings, monkeypatch):
    """A bug in Datum must not take the schedule down with it.

    Distinct from the case above: this is not a provider condition the
    framework knows how to record, so there is nothing to return but None.
    """
    settings.KUBERNETES_SOURCE = FIXTURE

    def explode(*args, **kwargs):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr("datum.discovery.tasks.run_collector", explode)

    assert collect_kubernetes() is None


def test_the_task_is_idempotent_across_ticks(settings):
    """Two ticks against an unchanged estate leave one row per resource, which
    is what makes a five-minute schedule safe to leave running."""
    settings.KUBERNETES_SOURCE = MULTI_FIXTURE

    collect_kubernetes()
    collect_kubernetes()

    from datum.discovery.models import DiscoveredResource

    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2
    assert CollectorRun.objects.count() == 2


def test_cluster_mode_builds_a_live_collector_without_a_source_path(settings, monkeypatch):
    """Cluster mode takes its credentials from the environment, so an unset
    source path must not read as "not configured" and skip the run."""
    settings.KUBERNETES_MODE = "cluster"
    settings.KUBERNETES_SOURCE = ""
    settings.KUBERNETES_NAMESPACE = "production"

    built: list[object] = []

    def capture(namespace):
        built.append(namespace)
        raise RuntimeError("stop before touching a cluster")

    monkeypatch.setattr("datum.discovery.tasks.from_cluster", capture)

    assert collect_kubernetes() is None
    assert built == ["production"]


def test_recorded_mode_is_the_default(settings):
    """Nothing reaches for a cluster that was never configured."""
    settings.KUBERNETES_MODE = "recorded"
    settings.KUBERNETES_SOURCE = FIXTURE

    run = CollectorRun.objects.get(id=collect_kubernetes())

    assert run.status == CollectorRunStatus.SUCCESS
