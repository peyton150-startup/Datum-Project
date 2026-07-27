"""The intent document validator: DESIGN section 10.

This is the barricade for the declared plane. Everything here treats its input
as hostile text; everything downstream of `parse_document_set` may assume it
holds valid domain types.

Pure by design -- no file system, no database, no Git. The caller supplies the
document texts and the kind schemas, which makes every validation layer
testable without a repository or a migration.
"""

from collections.abc import Callable, Mapping, Sequence

import yaml

from datum.intent.errors import DocumentError, InvalidDocument, InvalidRevision
from datum.reconcile.domain import ResourceSnapshot

FORMAT_VERSION = "datum.dev/v1"

# Matches the `name` and `scope` column widths on declared_resource. Validating
# against the storage limit here means a document can never be accepted and then
# fail to persist.
MAX_IDENTIFIER_LENGTH = 253

PROVIDER_ID_KEY = "provider_id"

# The closed type vocabulary a Kind.attribute_schema may draw on.
#
# `type(v) is int` rather than isinstance, deliberately: bool is a subclass of
# int in Python, so isinstance would quietly accept `replicas: true` as an
# integer. A declaration that says "three replicas" and a declaration that says
# "yes replicas" must not validate the same way.
_TYPE_PREDICATES: Mapping[str, Callable[[object], bool]] = {
    "int": lambda value: type(value) is int,
    "str": lambda value: type(value) is str,
    "bool": lambda value: type(value) is bool,
}

KindSchemas = Mapping[str, Mapping[str, str]]
DocumentSource = tuple[str, str]  # (source name, raw text)
KeyPath = tuple[str, ...]


def parse_document_set(
    sources: Sequence[DocumentSource],
    tenant_id: str,
    kind_schemas: KindSchemas,
) -> list[ResourceSnapshot]:
    """Validate a whole revision's documents, or reject the revision.

    Accumulates every error before raising, because an author fixing intent
    wants the full list, not the first item of it.
    """
    errors: list[DocumentError] = []
    parsed: list[tuple[str, ResourceSnapshot]] = []

    for source, text in sources:
        try:
            parsed.append((source, _parse_one(text, tenant_id, kind_schemas)))
        except InvalidDocument as exc:
            errors.append(DocumentError(source=source, line=_locate(text, exc), message=str(exc)))

    # Runs even when some documents failed: a duplicate among the documents that
    # did parse is still worth reporting in this pass.
    errors.extend(_duplicate_identity_errors(parsed))

    if errors:
        raise InvalidRevision(errors)
    return [snapshot for _, snapshot in parsed]


def _parse_one(text: str, tenant_id: str, kind_schemas: KindSchemas) -> ResourceSnapshot:
    """Apply the syntax, envelope, referential, and schema layers to one document."""
    document = _single_mapping(text)

    _reject_unsupported_version(document)
    _reject_provider_id(document, ())

    kind = _identifier(document, ("kind",))
    metadata = _mapping_field(document, ("metadata",))
    _reject_provider_id(metadata, ("metadata",))
    name = _identifier(metadata, ("metadata", "name"))
    scope = _identifier(metadata, ("metadata", "scope"))
    attributes = _mapping_field(document, ("attributes",))

    return ResourceSnapshot(
        kind=kind,
        tenant_id=tenant_id,
        scope=scope,
        name=name,
        # Intent is authored before the resource exists, so it never carries a
        # provider identity. DESIGN section 12.
        provider_id=None,
        attributes=_validated_attributes(kind, attributes, kind_schemas),
    )


# --------------------------------------------------------------------------
# Locating a failure in the source text (WBS 1.3.4)
# --------------------------------------------------------------------------


def _locate(text: str, exc: InvalidDocument) -> int | None:
    """Resolve a rejection to a 1-based line in the document that caused it.

    Only ever runs on the failure path, so the happy path pays nothing for the
    second parse this needs.
    """
    if exc.line is not None:
        return exc.line
    if exc.path is None:
        return None
    return _key_lines(text).get(exc.path)


def _key_lines(text: str) -> dict[KeyPath, int]:
    """Map every mapping key in the document to the line it is written on."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        # The text did not survive a second parse either. The caller falls back
        # to naming the file alone, which is still better than nothing.
        return {}

    lines: dict[KeyPath, int] = {}
    _collect_key_lines(root, (), lines)
    return lines


def _collect_key_lines(node: object, prefix: KeyPath, lines: dict[KeyPath, int]) -> None:
    if not isinstance(node, yaml.MappingNode):
        return
    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        path = (*prefix, str(key_node.value))
        lines[path] = key_node.start_mark.line + 1
        _collect_key_lines(value_node, path, lines)


# --------------------------------------------------------------------------
# Syntax layer
# --------------------------------------------------------------------------


def _single_mapping(text: str) -> Mapping[str, object]:
    """Parseable YAML holding exactly one mapping."""
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise InvalidDocument(f"unparseable YAML: {exc}", line=_yaml_error_line(exc)) from exc

    if len(documents) != 1:
        raise InvalidDocument(
            f"expected exactly one document, found {len(documents)}; "
            "one document declares one resource"
        )
    document = documents[0]
    if not isinstance(document, dict):
        raise InvalidDocument(f"document is not a mapping, got {type(document).__name__}")
    return document


def _yaml_error_line(exc: yaml.YAMLError) -> int | None:
    """PyYAML marks are 0-indexed; humans and editors count from 1."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        # Not every YAMLError carries a position -- a reader or resolver error
        # can be raised without one.
        return None
    line: int = mark.line
    return line + 1


# --------------------------------------------------------------------------
# Envelope layer
# --------------------------------------------------------------------------


def _label(path: KeyPath) -> str:
    return ".".join(path) if path else "document"


def _reject_unsupported_version(document: Mapping[str, object]) -> None:
    version = document.get("apiVersion")
    if version != FORMAT_VERSION:
        raise InvalidDocument(
            f"unsupported apiVersion {version!r}; this build accepts {FORMAT_VERSION!r}",
            path=("apiVersion",),
        )


def _reject_provider_id(container: Mapping[str, object], prefix: KeyPath) -> None:
    """Refuse rather than ignore: silently dropping it would let an author
    believe they had pinned an identity that Datum never read."""
    if PROVIDER_ID_KEY in container:
        raise InvalidDocument(
            f"{_label(prefix)} declares {PROVIDER_ID_KEY!r}, which intent may not carry; "
            "provider identity is observed by discovery, never declared",
            path=(*prefix, PROVIDER_ID_KEY),
        )


def _identifier(container: Mapping[str, object], path: KeyPath) -> str:
    label = _label(path)
    value = container.get(path[-1])
    if not isinstance(value, str) or not value:
        raise InvalidDocument(f"{label} must be a non-empty string, got {value!r}", path=path)
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise InvalidDocument(
            f"{label} is {len(value)} characters, over the {MAX_IDENTIFIER_LENGTH} limit",
            path=path,
        )
    return value


def _mapping_field(container: Mapping[str, object], path: KeyPath) -> Mapping[str, object]:
    value = container.get(path[-1])
    if not isinstance(value, dict):
        raise InvalidDocument(f"{_label(path)} must be a mapping, got {value!r}", path=path)
    return value


# --------------------------------------------------------------------------
# Schema layer
# --------------------------------------------------------------------------


def _validated_attributes(
    kind: str,
    attributes: Mapping[str, object],
    kind_schemas: KindSchemas,
) -> dict[str, object]:
    """Referential layer for the kind, then the schema layer for its attributes."""
    schema = kind_schemas.get(kind)
    if schema is None:
        raise InvalidDocument(
            f"unknown kind {kind!r}; known kinds are {sorted(kind_schemas)}", path=("kind",)
        )

    declared = set(attributes)
    expected = set(schema)
    if declared != expected:
        raise InvalidDocument(
            f"attributes do not match the schema for kind {kind!r}: "
            f"missing={sorted(expected - declared)} unknown={sorted(declared - expected)}",
            path=("attributes",),
        )

    for attribute_name, type_name in schema.items():
        _check_attribute_type(kind, attribute_name, attributes[attribute_name], type_name)
    return dict(attributes)


def _check_attribute_type(kind: str, attribute_name: str, value: object, type_name: str) -> None:
    path = ("attributes", attribute_name)
    is_expected_type = _TYPE_PREDICATES.get(type_name)
    if is_expected_type is None:
        raise InvalidDocument(
            f"kind {kind!r} declares attribute {attribute_name!r} with type {type_name!r}, "
            f"which is not one of {sorted(_TYPE_PREDICATES)} -- the kind is wrong, "
            "not the document",
            path=path,
        )
    if not is_expected_type(value):
        raise InvalidDocument(
            f"attribute {attribute_name!r} of kind {kind!r} must be {type_name}, got {value!r}",
            path=path,
        )


# --------------------------------------------------------------------------
# Referential layer across the document set
# --------------------------------------------------------------------------


def _duplicate_identity_errors(
    parsed: Sequence[tuple[str, ResourceSnapshot]],
) -> list[DocumentError]:
    """CF-2.

    Two documents claiming one identity is listed in DESIGN section 6 as a
    condition that legitimately happens and is rejected *here*. Before this
    check existed, a Postgres unique constraint caught it instead and raised
    IntegrityError across a module boundary, inverting ADR-008.
    """
    sources_by_key: dict[tuple[str, str, str, str], list[str]] = {}
    for source, snapshot in parsed:
        sources_by_key.setdefault(snapshot.natural_key, []).append(source)

    errors: list[DocumentError] = []
    for natural_key, sources in sorted(sources_by_key.items()):
        if len(sources) == 1:
            continue
        kind, tenant_id, scope, name = natural_key
        errors.append(
            DocumentError(
                source=sorted(sources)[0],
                line=None,
                message=(
                    f"natural key (tenant={tenant_id}, kind={kind}, scope={scope}, name={name}) "
                    f"is declared by {len(sources)} documents: {', '.join(sorted(sources))}"
                ),
            )
        )
    return errors


__all__ = [
    "FORMAT_VERSION",
    "MAX_IDENTIFIER_LENGTH",
    "DocumentError",
    "DocumentSource",
    "InvalidDocument",
    "InvalidRevision",
    "KindSchemas",
    "parse_document_set",
]
