"""The Kubernetes collector: reads Deployments, normalizes them to snapshots.

An adapter, nothing more. It knows the shape of a Kubernetes Deployment and
nothing about planes, matching, revisions, or discrepancies. It does not loop
over its own records and it does not touch a run record -- `collector` owns
both, which is what keeps CF-1 unreachable from here.

**Where the records come from is a source, not a collector.** WBS 1.4.2 points
this at a live cluster, and the way it does that is by adding a second source
rather than a second collector. `normalize` is untouched by the change, which
is the payoff of the fetch/normalize split and the reason the recorded fixtures
stay honest: `ClusterSource` asks the API server for raw JSON rather than SDK
model objects, so a recorded payload and a live one are the same shape all the
way to the normalizer.

DESIGN section 11: the live cluster is how the SDK, real API shapes, and
pagination get exercised. CI stays on fixtures, because a test that needs a
cluster is not a unit test and does not gate the build.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Protocol

from datum.discovery.errors import (
    MalformedProviderData,
    PartialProviderRead,
    ProviderUnavailable,
)
from datum.discovery.recorded import RecordedSource, records_in
from datum.discovery.retry import with_retry
from datum.reconcile.domain import ResourceSnapshot

logger = logging.getLogger(__name__)

KIND_NAME = "Deployment"

# Where Kubernetes puts the list in a DeploymentList response.
ENVELOPE_KEY = "items"

# How many Deployments to ask for per page. Not a tuning knob so much as a way
# to make pagination reachable at all: without it the API server returns
# everything in one response and the paging path is never exercised.
PAGE_SIZE = 200

# Statuses worth trying again. 429 is the rate limit section 11 names; 5xx is
# the API server having a bad moment. Everything else -- 401, 403, 404, 422 --
# will fail identically on the next attempt.
#
# 410 Gone is deliberately absent. A continuation token encodes the
# resourceVersion the list began at, so paging is a consistent snapshot rather
# than a series of independent reads -- which is why a resource cannot be
# double-counted across pages, and why a slow read fails instead: the snapshot
# ages out and the API server answers 410. Retrying the expired token cannot
# help, and restarting the whole list from page one is not obviously right
# either, since a read slow enough to expire once will tend to expire again.
# Treating it as permanent gives a partial read with a recorded gap, which is
# honest and, being PARTIAL, can never license an absence inference.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# The natural-key components plus the one attribute this kind carries, paired
# with where they live in a Kubernetes Deployment. A table rather than a chain
# of `or` conditions so a rejection can name every field it is missing instead
# of only the first.
_REQUIRED_FIELDS = (
    ("metadata.name", ("metadata", "name")),
    ("metadata.namespace", ("metadata", "namespace")),
    ("metadata.uid", ("metadata", "uid")),
    ("spec.replicas", ("spec", "replicas")),
)


class DeploymentSource(Protocol):
    """Where a collector's records come from, and nothing about what they mean."""

    def read(self) -> Sequence[object]:
        """Every Deployment record available, raw.

        Raises `ProviderUnavailable` when nothing could be read, and
        `PartialProviderRead` when some records were read and the rest were not.
        """
        ...


class ClusterSource:
    """Reads Deployments from a live cluster, one page at a time.

    Asks for raw JSON (`_preload_content=False`) rather than letting the SDK
    build model objects. Two reasons, both load-bearing: the normalizer then
    sees exactly what a recorded fixture holds, so fixtures cannot drift into
    fiction; and Datum stops depending on the SDK's attribute naming, which is
    a second vocabulary to track for no benefit.
    """

    def __init__(self, namespace: str = "", page_size: int = PAGE_SIZE) -> None:
        # Empty namespace means every namespace, matching the API's own default
        # and the scope Datum wants: a resource nobody declared is exactly what
        # discovery exists to find, and namespace-scoping the read would hide it.
        self.namespace = namespace
        self.page_size = page_size

    def read(self) -> Sequence[object]:
        """Every Deployment in scope, following continuation tokens to the end.

        A page that fails after earlier pages succeeded is a `PartialProviderRead`
        rather than a failure: the records already in hand are real observations
        and section 11 requires persisting what parsed. A failure on the first
        page read nothing, so it is `ProviderUnavailable`.
        """
        api = self._deployments_api()
        records: list[object] = []
        continue_token: str | None = None
        page = 0

        while True:
            page += 1
            # Parsing the page is inside the guard, not only fetching it. A page
            # that arrives malformed has failed just as surely as one that never
            # arrived, and classifying only transport failures would throw away
            # every earlier page whenever a later envelope was junk -- the same
            # whole-collection abort as CF-1, one layer further down.
            try:
                payload = self._page(api, continue_token, page)
                items = records_in(payload, ENVELOPE_KEY, f"page {page}")
                next_token = _continue_token(payload)
            except Exception as exc:
                raise self._read_stopped(records, page, exc) from exc

            records.extend(items)
            continue_token = next_token
            if not continue_token:
                return records

    def _deployments_api(self):
        """Load credentials and return the apps/v1 client.

        In-cluster config first, then a kubeconfig: the first is how this runs in
        production and the second is how it runs on a developer's laptop against
        k3s. Credentials come from the environment and are never logged --
        nothing here formats the config into a message.
        """
        from kubernetes import client, config
        from kubernetes.config.config_exception import ConfigException

        try:
            config.load_incluster_config()
        except ConfigException:
            try:
                config.load_kube_config()
            except Exception as exc:
                raise ProviderUnavailable(
                    f"no usable Kubernetes credentials: {type(exc).__name__}"
                ) from exc
        return client.AppsV1Api()

    def _page(self, api, continue_token: str | None, page: int) -> Mapping[str, object]:
        """One page, retried on transient failures, parsed from raw JSON."""
        response = with_retry(
            lambda: api.list_deployment_for_all_namespaces(
                limit=self.page_size,
                _continue=continue_token,
                _preload_content=False,
            )
            if not self.namespace
            else api.list_namespaced_deployment(
                namespace=self.namespace,
                limit=self.page_size,
                _continue=continue_token,
                _preload_content=False,
            ),
            is_transient=_is_transient,
            description=f"Kubernetes Deployment list page {page}",
        )
        return json.loads(response.data)

    def _read_stopped(self, records: list[object], page: int, exc: Exception) -> Exception:
        """Classify a failed page by whether anything had already been read."""
        reason = f"page {page} failed: {type(exc).__name__}: {exc}"
        if records:
            return PartialProviderRead(records, reason)
        return ProviderUnavailable(f"could not read Deployments from the cluster; {reason}")


class KubernetesCollector:
    """Reads Deployments from whatever source it is given."""

    name = "kubernetes"
    kind = KIND_NAME

    def __init__(self, source: DeploymentSource) -> None:
        self.source = source

    def fetch(self) -> Sequence[object]:
        """Delegated wholesale. Nothing here judges a record."""
        return self.source.read()

    def normalize(self, record: object, tenant_id: str) -> ResourceSnapshot:
        """One Deployment to one snapshot, or a rejection.

        The discovery barricade (ADR-008): provider shapes stop here. A record
        missing any natural-key component is structurally invalid rather than
        merely incomplete -- it would match nothing, so the same resource would
        surface as one declared orphan plus one discovered orphan. Rejecting it
        is more honest than storing something that cannot be reconciled.

        `tenant_id` is a parameter rather than a default for the same reason: it
        is the fourth natural-key component.
        """
        if not isinstance(record, Mapping):
            raise MalformedProviderData(
                f"Deployment record is {type(record).__name__}, not a mapping: {record!r}"
            )

        found = {label: _dig(record, path) for label, path in _REQUIRED_FIELDS}
        missing = sorted(label for label, value in found.items() if value is None)
        if missing:
            raise MalformedProviderData(
                f"incomplete Deployment record, missing {', '.join(missing)}: {record!r}"
            )

        return ResourceSnapshot(
            kind=KIND_NAME,
            tenant_id=tenant_id,
            scope=str(found["metadata.namespace"]),
            name=str(found["metadata.name"]),
            provider_id=str(found["metadata.uid"]),
            attributes={"replicas": found["spec.replicas"]},
        )


def from_recording(path: str) -> KubernetesCollector:
    """The recorded-payload collector, which is what tests and CI use."""
    return KubernetesCollector(RecordedSource(path, ENVELOPE_KEY))


def from_cluster(namespace: str = "") -> KubernetesCollector:
    """The live-cluster collector, which is what a deployment uses."""
    return KubernetesCollector(ClusterSource(namespace))


def _continue_token(payload: Mapping[str, object]) -> str | None:
    """The token for the next page, or None at the end of the list."""
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    token = metadata.get("continue")
    return token if isinstance(token, str) and token else None


def _is_transient(exc: BaseException) -> bool:
    """Whether a failed API call is worth trying again.

    Rate limits and server errors pass; anything else is refused immediately,
    because retrying a request the server has already judged malformed just
    repeats the mistake more slowly.
    """
    status = getattr(exc, "status", None)
    return isinstance(status, int) and status in RETRYABLE_STATUSES


def _dig(record: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    """Follow a key path, returning None the moment it stops being a mapping.

    An absent key and a key whose parent is the wrong type are the same answer
    here -- the field is not readable -- and both are the caller's to report.
    """
    current: object = record
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


__all__ = [
    "KIND_NAME",
    "ENVELOPE_KEY",
    "PAGE_SIZE",
    "ClusterSource",
    "DeploymentSource",
    "KubernetesCollector",
    "RecordedSource",
    "from_cluster",
    "from_recording",
]
