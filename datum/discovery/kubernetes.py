"""The Kubernetes collector: reads Deployments, normalizes them to snapshots.

An adapter, nothing more. It knows the shape of a Kubernetes Deployment and
nothing about planes, matching, revisions, or discrepancies. It does not loop
over its own records and it does not touch a run record -- `collector` owns
both, which is what keeps CF-1 unreachable from here.

Phase 3 reads a recorded payload. WBS 1.4.2 points `fetch` at a live cluster;
`normalize` does not change when it does, which is the point of the split.
"""

import json
from collections.abc import Mapping, Sequence

from datum.discovery.errors import MalformedProviderData, ProviderUnavailable
from datum.reconcile.domain import ResourceSnapshot

KIND_NAME = "Deployment"

# The natural-key components plus the one attribute this kind carries, paired
# with where they live in a Kubernetes Deployment. A table rather than a chain
# of `or` conditions so that a rejection can name every field it is missing
# instead of only the first.
_REQUIRED_FIELDS = (
    ("metadata.name", ("metadata", "name")),
    ("metadata.namespace", ("metadata", "namespace")),
    ("metadata.uid", ("metadata", "uid")),
    ("spec.replicas", ("spec", "replicas")),
)


class KubernetesCollector:
    """Reads Deployments from a recorded provider payload.

    The payload is a `DeploymentList` as the Kubernetes API returns one, so the
    normalizer built against it is the normalizer a live cluster will use.
    """

    name = "kubernetes"

    def __init__(self, source_path: str) -> None:
        self.source_path = source_path

    def fetch(self) -> Sequence[object]:
        """Every item in the recorded list, raw and unjudged.

        A payload that cannot be read at all is `ProviderUnavailable`: the run
        observed nothing, so it must not be allowed to imply anything about the
        estate. That is a different outcome from reading junk, which is one
        record's problem and is `normalize`'s to report.
        """
        try:
            with open(self.source_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise ProviderUnavailable(
                f"could not read the Kubernetes payload at {self.source_path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable(
                f"the Kubernetes payload at {self.source_path} is not valid JSON: {exc}"
            ) from exc

        items = payload.get("items")
        if not isinstance(items, list):
            # The envelope is wrong, so there is no record to reject one of.
            # Nothing was observed: unavailability, not junk.
            raise ProviderUnavailable(
                f"the Kubernetes payload at {self.source_path} has no 'items' list"
            )
        return items

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


__all__ = ["KIND_NAME", "KubernetesCollector"]
