import yaml

from datum.reconcile.domain import ResourceSnapshot

EXPECTED_KIND = "Deployment"


class InvalidDocument(Exception):
    """An intent document is syntactically or structurally invalid."""


def parse_deployment_document(text: str, tenant_id: str) -> ResourceSnapshot:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidDocument(f"unparseable YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise InvalidDocument("document is not a mapping")
    if doc.get("kind") != EXPECTED_KIND:
        raise InvalidDocument(f"expected kind Deployment, got {doc.get('kind')!r}")
    metadata = doc.get("metadata") or {}
    name = metadata.get("name")
    scope = metadata.get("namespace")
    replicas = (doc.get("spec") or {}).get("replicas")
    if name is None or scope is None or not isinstance(replicas, int):
        raise InvalidDocument(
            f"incomplete Deployment: name={name} scope={scope} replicas={replicas}"
        )
    return ResourceSnapshot(
        kind=EXPECTED_KIND,
        tenant_id=tenant_id,
        scope=scope,
        name=name,
        provider_id=None,
        attributes={"replicas": replicas},
    )
