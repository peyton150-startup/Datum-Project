import json

from datum.reconcile.domain import ResourceSnapshot


class MalformedProviderData(Exception):
    """A provider record could not be normalized to a Deployment snapshot."""


def read_deployment_fixture(path: str, tenant_id: str = "") -> list[ResourceSnapshot]:
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
