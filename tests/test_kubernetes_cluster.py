"""The live-cluster source, exercised without a cluster (WBS 1.4.2).

DESIGN section 11 puts the Kubernetes collector on a live k3s cluster and keeps
CI on fixtures, because a test that needs a cluster is not a unit test and does
not gate the build. What is testable hermetically is everything between the SDK
call and the snapshot: pagination, the retry schedule, and how a read that
stops halfway is classified. Those are where the defects live, so they are
tested here against a fake API rather than deferred to a cluster nobody runs in
CI.

A single test in `test_live_cluster.py` covers the SDK call itself and is
skipped unless a cluster is actually reachable.
"""

import json

import pytest

from datum.discovery.collector import run_collector
from datum.discovery.errors import PartialProviderRead, ProviderUnavailable
from datum.discovery.kubernetes import ClusterSource, KubernetesCollector, from_cluster
from datum.discovery.models import DiscoveredResource
from datum.discovery.retry import with_retry
from datum.enums import CollectorRunStatus

TENANT = "00000000-0000-0000-0000-000000000001"


class FakeResponse:
    """What the SDK hands back with `_preload_content=False`: raw bytes."""

    def __init__(self, payload: dict) -> None:
        self.data = json.dumps(payload).encode("utf-8")


class TransientError(Exception):
    """An SDK error carrying an HTTP status, as ApiException does."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def deployment(name: str, replicas: int = 1) -> dict:
    return {
        "metadata": {"name": name, "namespace": "default", "uid": f"uid-{name}"},
        "spec": {"replicas": replicas},
    }


def page(names: list[str], continue_token: str | None = None) -> dict:
    metadata = {"continue": continue_token} if continue_token else {}
    return {"kind": "DeploymentList", "metadata": metadata, "items": [deployment(n) for n in names]}


class FakeDeploymentsApi:
    """Serves a scripted list of pages, failing where told to."""

    def __init__(self, pages: list[object]) -> None:
        self.pages = pages
        self.calls: list[str | None] = []

    def list_deployment_for_all_namespaces(self, limit, _continue, _preload_content):
        self.calls.append(_continue)
        nxt = self.pages[len(self.calls) - 1]
        if isinstance(nxt, Exception):
            raise nxt
        return FakeResponse(nxt)

    def list_namespaced_deployment(self, namespace, limit, _continue, _preload_content):
        self.namespace_used = namespace
        return self.list_deployment_for_all_namespaces(limit, _continue, _preload_content)


def source_over(
    pages: list[object], namespace: str = ""
) -> tuple[ClusterSource, FakeDeploymentsApi]:
    api = FakeDeploymentsApi(pages)
    source = ClusterSource(namespace)
    source._deployments_api = lambda: api  # the SDK call is the one thing not under test
    return source, api


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_a_single_page_is_read_whole():
    source, _ = source_over([page(["web", "api"])])

    assert len(source.read()) == 2


def test_pages_are_followed_to_the_end():
    """The reason PAGE_SIZE exists at all: without paging, this path is never
    taken and a large estate silently truncates to one response."""
    source, api = source_over(
        [page(["a"], "token-1"), page(["b"], "token-2"), page(["c"])],
    )

    records = source.read()

    assert [r["metadata"]["name"] for r in records] == ["a", "b", "c"]
    assert api.calls == [None, "token-1", "token-2"]


def test_an_empty_continue_token_ends_the_read():
    """Kubernetes sends `continue: ""` rather than omitting it. Treating empty
    as "there is more" would loop forever against a real cluster."""
    source, api = source_over([page(["only"], continue_token="")])

    assert len(source.read()) == 1
    assert api.calls == [None]


def test_a_namespace_scopes_the_read():
    source, api = source_over([page(["web"])], namespace="production")

    source.read()

    assert api.namespace_used == "production"


# ---------------------------------------------------------------------------
# Where a read stops matters: nothing read vs something read
# ---------------------------------------------------------------------------


def test_failing_on_the_first_page_is_provider_unavailable():
    """Nothing was read, so the run must not imply anything about the estate."""
    source, _ = source_over([TransientError(503)] * 4)

    with pytest.raises(ProviderUnavailable):
        source.read()


def test_failing_on_a_later_page_is_a_partial_read_that_keeps_what_it_got():
    """The case section 11 names for exhausted retries.

    Throwing away page one because page two failed would contradict "always
    persist what parsed"; pretending the read was complete would turn the
    unread pages into resources that appear deleted.
    """
    source, _ = source_over([page(["a", "b"], "token-1")] + [TransientError(500)] * 4)

    with pytest.raises(PartialProviderRead) as caught:
        source.read()

    assert len(caught.value.records) == 2
    assert "page 2" in caught.value.reason


# ---------------------------------------------------------------------------
# A gap is not a rejection: how the run records it
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_partial_read_persists_its_records_and_marks_the_run_partial_with_a_gap():
    source, _ = source_over([page(["a", "b"], "token-1")] + [TransientError(500)] * 4)

    run = run_collector(KubernetesCollector(source), TENANT)

    assert run.status == CollectorRunStatus.PARTIAL
    assert run.has_gap is True
    assert (run.resources_read, run.resources_written, run.errors) == (2, 2, 0)
    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2


@pytest.mark.django_db
def test_a_gap_forces_partial_even_when_every_record_was_clean():
    """The mass-deletion reading in its most convincing disguise.

    Errors are zero, so a status derived from errors alone would call this a
    complete, successful read of a small estate -- and 1.4.4 would then be
    entitled to mark everything it did not see as absent.
    """
    source, _ = source_over([page(["a"], "token-1")] + [TransientError(429)] * 4)

    run = run_collector(KubernetesCollector(source), TENANT)

    assert run.errors == 0
    assert run.status == CollectorRunStatus.PARTIAL


@pytest.mark.django_db
def test_a_complete_read_records_no_gap():
    source, _ = source_over([page(["a"], "token-1"), page(["b"])])

    run = run_collector(KubernetesCollector(source), TENANT)

    assert run.status == CollectorRunStatus.SUCCESS
    assert run.has_gap is False


@pytest.mark.django_db
def test_a_gap_and_a_bad_record_are_both_recorded():
    """They are different failures and must not mask each other."""
    broken = {"metadata": {"name": "broken", "namespace": "default"}, "spec": {}}
    first = {
        "kind": "DeploymentList",
        "metadata": {"continue": "t"},
        "items": [deployment("a"), broken],
    }
    source, _ = source_over([first] + [TransientError(500)] * 4)

    run = run_collector(KubernetesCollector(source), TENANT)

    assert (run.resources_read, run.resources_written, run.errors) == (2, 1, 1)
    assert run.has_gap is True


# ---------------------------------------------------------------------------
# Retry schedule
# ---------------------------------------------------------------------------


def test_a_transient_failure_is_retried_and_can_succeed():
    source, api = source_over([TransientError(429), page(["web"])])

    # Two scripted responses, so success on the retry proves the first was not
    # simply swallowed.
    assert len(source.read()) == 1
    assert len(api.calls) == 2


def test_a_permanent_failure_is_not_retried():
    """403 will fail identically next time. Retrying it just delays the report
    and holds the collector lock while doing nothing."""
    source, api = source_over([TransientError(403)])

    with pytest.raises(ProviderUnavailable):
        source.read()

    assert len(api.calls) == 1


def test_retries_are_bounded():
    """Unbounded retry against a provider that is genuinely down converts a
    failed run into a hung one."""
    source, api = source_over([TransientError(503)] * 10)

    with pytest.raises(ProviderUnavailable):
        source.read()

    assert len(api.calls) == 4


def test_the_delay_grows_exponentially():
    slept: list[float] = []
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise TransientError(503)

    with pytest.raises(TransientError):
        with_retry(
            always_fails,
            is_transient=lambda exc: True,
            description="test call",
            sleep=slept.append,
        )

    assert attempts["n"] == 4
    assert slept == [0.5, 1.0, 2.0]


def test_a_call_that_succeeds_first_time_never_sleeps():
    slept: list[float] = []

    result = with_retry(
        lambda: "ok", is_transient=lambda exc: True, description="test call", sleep=slept.append
    )

    assert result == "ok"
    assert slept == []


def test_a_single_attempt_is_permitted_and_does_not_retry():
    """The boundary at max_attempts=1: it must run once, not zero times."""
    attempts = {"n": 0}

    def fails():
        attempts["n"] += 1
        raise TransientError(503)

    with pytest.raises(TransientError):
        with_retry(
            fails,
            is_transient=lambda exc: True,
            description="test call",
            max_attempts=1,
            sleep=lambda _: None,
        )

    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# Envelope handling
# ---------------------------------------------------------------------------


def test_a_malformed_envelope_after_a_good_page_keeps_that_page():
    """Regression: a page that arrives malformed has failed as surely as one
    that never arrived.

    Found in review. `_items_of` ran outside the guard that classifies a failed
    page, so a valid-JSON response with no `items` list -- a proxy error body, a
    truncated response -- raised straight past the classifier and discarded
    every page already read. The run then reported FAILED with zero counts
    while real, readable records had been in hand: the same whole-collection
    abort as CF-1, one layer further down.
    """
    good = page(["a", "b"], "token-1")
    malformed = {"kind": "Status", "metadata": {}}
    source, _ = source_over([good, malformed])

    with pytest.raises(PartialProviderRead) as caught:
        source.read()

    assert len(caught.value.records) == 2


@pytest.mark.django_db
def test_a_malformed_envelope_after_a_good_page_persists_and_marks_a_gap():
    """The same defect seen from the run record, which is what an operator reads."""
    good = page(["a", "b"], "token-1")
    malformed = {"kind": "Status", "metadata": {}}
    source, _ = source_over([good, malformed])

    run = run_collector(KubernetesCollector(source), TENANT)

    assert run.status == CollectorRunStatus.PARTIAL
    assert run.has_gap is True
    assert (run.resources_read, run.resources_written) == (2, 2)
    assert DiscoveredResource.objects.filter(tenant_id=TENANT).count() == 2


def test_a_malformed_envelope_on_the_first_page_is_still_unavailable():
    """The nearby case the fix must NOT change.

    Nothing had been read, so there is nothing to keep and no basis for any
    claim about the estate. A fix that turned this into a partial read would
    have made an unreachable provider look like a tiny estate.
    """
    source, _ = source_over([{"kind": "Status", "metadata": {}}])

    with pytest.raises(ProviderUnavailable):
        source.read()


def test_metadata_that_is_not_a_mapping_after_a_good_page_ends_the_read_cleanly():
    """The other nearby case: unreadable metadata is the end of the list, not a
    failure. There is no token to follow, which is the same answer as the last
    page, so the records already read are returned rather than raised over."""
    source, _ = source_over(
        [
            page(["a"], "token-1"),
            {"kind": "DeploymentList", "metadata": None, "items": [deployment("b")]},
        ]
    )

    records = source.read()

    assert [r["metadata"]["name"] for r in records] == ["a", "b"]


def test_a_response_with_no_items_list_is_unavailable_not_an_empty_estate():
    source, _ = source_over([{"kind": "DeploymentList", "metadata": {}}])

    with pytest.raises(ProviderUnavailable):
        source.read()


def test_an_empty_items_list_is_a_legitimately_empty_estate():
    source, _ = source_over([page([])])

    assert source.read() == []


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------


def test_in_cluster_credentials_are_preferred(monkeypatch):
    """How this runs in production: a service account mounted into the pod."""
    from kubernetes import config as kube_config

    loaded: list[str] = []
    monkeypatch.setattr(kube_config, "load_incluster_config", lambda: loaded.append("incluster"))
    monkeypatch.setattr(
        kube_config, "load_kube_config", lambda: pytest.fail("kubeconfig must not be consulted")
    )

    ClusterSource()._deployments_api()

    assert loaded == ["incluster"]


def test_a_kubeconfig_is_the_fallback(monkeypatch):
    """How this runs on a laptop against k3s."""
    from kubernetes import config as kube_config
    from kubernetes.config.config_exception import ConfigException

    loaded: list[str] = []

    def no_incluster():
        raise ConfigException("not in a cluster")

    monkeypatch.setattr(kube_config, "load_incluster_config", no_incluster)
    monkeypatch.setattr(kube_config, "load_kube_config", lambda: loaded.append("kubeconfig"))

    ClusterSource()._deployments_api()

    assert loaded == ["kubeconfig"]


def test_no_usable_credentials_is_provider_unavailable(monkeypatch):
    """Neither source of credentials worked, so nothing can be read.

    The message names the exception type and never the config itself: collector
    credentials must not reach logs, which is a stated non-functional
    requirement.
    """
    from kubernetes import config as kube_config
    from kubernetes.config.config_exception import ConfigException

    def no_incluster():
        raise ConfigException("not in a cluster")

    def no_kubeconfig():
        raise ConfigException("no kubeconfig at ~/.kube/config")

    monkeypatch.setattr(kube_config, "load_incluster_config", no_incluster)
    monkeypatch.setattr(kube_config, "load_kube_config", no_kubeconfig)

    with pytest.raises(ProviderUnavailable) as caught:
        ClusterSource()._deployments_api()

    assert "no usable Kubernetes credentials" in str(caught.value)
    assert "kubeconfig at" not in str(caught.value)


# ---------------------------------------------------------------------------
# Envelope edge cases
# ---------------------------------------------------------------------------


def test_a_response_that_is_not_a_mapping_is_unavailable():
    source, _ = source_over([["not", "an", "envelope"]])

    with pytest.raises(ProviderUnavailable):
        source.read()


def test_a_response_whose_metadata_is_not_a_mapping_ends_the_read():
    """A malformed metadata block must end paging rather than crash it: there
    is no token to follow, which is the same answer as the last page."""
    source, api = source_over([{"kind": "DeploymentList", "metadata": None, "items": []}])

    assert source.read() == []
    assert len(api.calls) == 1


def test_from_cluster_builds_a_collector_over_a_cluster_source():
    """Construction only -- nothing here reaches for credentials, which is what
    makes it safe for the beat task to build one before deciding to run it."""
    collector = from_cluster("production")

    assert collector.name == "kubernetes"
    assert isinstance(collector.source, ClusterSource)
    assert collector.source.namespace == "production"


def test_from_cluster_defaults_to_every_namespace():
    assert from_cluster().source.namespace == ""
