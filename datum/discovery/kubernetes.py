import json

from datum.reconcile.domain import ResourceSnapshot


class MalformedProviderData(Exception):
    """A provider record could not be normalized to a Deployment snapshot."""


def read_deployment_fixture(path: str, tenant_id: str) -> list[ResourceSnapshot]:
    """Read a recorded Kubernetes Deployment list and normalize it to snapshots.

    This is the discovery barricade (ADR-008): provider JSON is validated here
    and converted to domain types, so nothing inward ever sees a raw provider
    dict. `tenant_id` is required rather than defaulted because it is one of the
    four components of the natural key; a snapshot carrying a blank tenant is
    structurally invalid and would match nothing.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    snapshots: list[ResourceSnapshot] = []
    for item in payload.get("items", []):
        snapshots.append(_normalize(item, tenant_id))
    return snapshots


def _normalize(item: dict, tenant_id: str) -> ResourceSnapshot:
    metadata = item.get("metadata", {})
    name = metadata.get("name")
    scope = metadata.get("namespace")
    provider_id = metadata.get("uid")
    replicas = item.get("spec", {}).get("replicas")
    if name is None or scope is None or provider_id is None or replicas is None:
        raise MalformedProviderData(f"incomplete Deployment record: {metadata!r}")
    return ResourceSnapshot(
        kind="Deployment",
        tenant_id=tenant_id,
        scope=scope,
        name=name,
        provider_id=provider_id,
        attributes={"replicas": replicas},
    )
