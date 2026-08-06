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
from datum.reconcile.attribute_types import ATTRIBUTE_TYPES, UnacceptableLiteral
from datum.reconcile.domain import ResourceSnapshot, unstorable_attribute

FORMAT_VERSION = "datum.dev/v1"

# Matches the `name` and `scope` column widths on declared_resource. Validating
# against the storage limit here means a document can never be accepted and then
# fail to persist.
MAX_IDENTIFIER_LENGTH = 253

PROVIDER_ID_KEY = "provider_id"

ATTRIBUTES_KEY = "attributes"

# YAML's merge key. Reading attributes as nodes means merges are not expanded
# for us the way `safe_load` expands them, so a document using one would see an
# attribute literally named `<<`. Refused by name rather than left to surface as
# a baffling "unknown attribute" -- and refused rather than expanded here,
# because expanding it would be a second implementation of a PyYAML feature,
# and because an attribute assembled from elsewhere in the file is not what the
# author is looking at when they read their own declaration.
MERGE_KEY = "<<"

# The one scalar text that states a declared null, and only unquoted. `~`,
# `Null` and `NULL` are ordinary content read by the declared type -- treating
# them as null would hand the authority back to YAML's implicit resolver
# through the one door this barricade closes (issue #55).
DECLARED_NULL = "null"

# What a non-scalar node is called when one turns up where a value belongs.
# A lookup rather than a branch: there are exactly three node kinds and the
# scalar one has already been excluded by the time this is read.
NODE_SHAPES: Mapping[type, str] = {
    yaml.SequenceNode: "sequence",
    yaml.MappingNode: "mapping",
}

# The closed type vocabulary a Kind.attribute_schema may draw on lives in
# `reconcile.attribute_types`, next to the comparison field types it has to
# agree with (issue #53). This barricade validates against it rather than
# restating it: the two sets are related but not equal, and when they were
# written in two places the relation was nobody's job to keep.
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

    return ResourceSnapshot(
        kind=kind,
        tenant_id=tenant_id,
        scope=scope,
        name=name,
        # Intent is authored before the resource exists, so it never carries a
        # provider identity. DESIGN section 12.
        provider_id=None,
        attributes=_validated_attributes(kind, _attribute_nodes(text), kind_schemas),
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
    except ValueError as exc:
        # Not every failure inside PyYAML is a `YAMLError`. Its constructor
        # calls `int()` on a scalar it resolved as an integer, and CPython caps
        # int-from-string at `sys.get_int_max_str_digits()` -- 4300 by default
        # since 3.11 -- raising a bare `ValueError` past it.
        #
        # This barricade promises `InvalidDocument` or nothing. Without this the
        # `ValueError` escaped `parse_document_set` entirely, past
        # `ingest_revision`, and reached the polling task's catch-all for the
        # unanticipated: the author saw a silently dropped revision rather than
        # an error naming their file. Predates the node-level parser -- the same
        # text has escaped here for as long as `safe_load_all` has been the
        # syntax layer.
        raise InvalidDocument(f"unparseable YAML: {exc}") from exc

    if len(documents) != 1:
        raise InvalidDocument(
            f"expected exactly one document, found {len(documents)}; "
            "one document declares one resource"
        )
    document = documents[0]
    if not isinstance(document, dict):
        raise InvalidDocument(f"document is not a mapping, got {type(document).__name__}")
    return document


def _attribute_nodes(text: str) -> Mapping[str, yaml.Node]:
    """The attributes mapping as YAML nodes rather than as loaded values.

    Declared values are read from their nodes because `safe_load` has already
    thrown away the two things that decide what a scalar means: whether it was
    quoted, and what its author actually typed. `enabled: null` and
    `enabled: "null"` are indistinguishable by the time it returns, and so are
    `port: 1:30` and `port: 90` (issue #55).

    This is the only reader of the attributes mapping. `_mapping_field` is not
    also asked about it, because a key set derived in two places is a key set
    that can come to disagree with itself.
    """
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    # `_single_mapping` parsed this same text first and proved it holds exactly
    # one document and that the document is a mapping, so composing it again
    # can neither fail nor yield anything else. The loader is pinned to the same
    # one it used rather than left to default, so that this is a fact about the
    # code and not about two loaders happening to share a resolver today.
    assert isinstance(root, yaml.MappingNode)

    _reject_repeated_key(root, ())

    for key_node, value_node in root.value:
        if not isinstance(key_node, yaml.ScalarNode) or key_node.value != ATTRIBUTES_KEY:
            continue
        if not isinstance(value_node, yaml.MappingNode):
            raise InvalidDocument(
                f"{ATTRIBUTES_KEY} must be a mapping, got a "
                f"{NODE_SHAPES.get(type(value_node), 'scalar')}",
                path=(ATTRIBUTES_KEY,),
            )
        return _named_nodes(value_node)

    raise InvalidDocument(f"{ATTRIBUTES_KEY} must be a mapping, got None", path=(ATTRIBUTES_KEY,))


def _reject_repeated_key(mapping_node: yaml.MappingNode, path: KeyPath) -> None:
    """A key written twice is refused rather than silently resolved.

    The document is read two ways -- loaded for the envelope, composed for the
    attributes -- and those two ways resolve a repeated key differently: a dict
    built from the loader keeps the last occurrence, and a scan of the composed
    node list reaches the first. So `attributes:` written twice made the two
    readings disagree about which block was authoritative, with no error either
    way. The declared plane held values from one block while the document said
    the other.

    Aligning the two readings on "last wins" would have made both correct today
    and left them free to drift, which is the trade CLAUDE.md names. Refusing
    the input removes the question instead: with no repeated key there is no
    occurrence to choose between, so the two readings cannot disagree whatever
    either one does next.

    Refused for every key at both levels, not only for `attributes`. `kind:`
    written twice resolves to the last silently today, and so did a repeated
    attribute name; those are the same trap waiting for the next reader to be
    added, and a rule with an exception in it is the thing that was already
    going wrong here.
    """
    seen: set[str] = set()
    for key_node, _ in mapping_node.value:
        # Hashable by the time this runs -- see `_named_nodes`.
        assert isinstance(key_node, yaml.ScalarNode)
        name = str(key_node.value)
        if name in seen:
            raise InvalidDocument(
                f"{_label(path)} states {name!r} more than once; a repeated key is "
                "refused rather than resolved, because which occurrence wins is "
                "not visible to the person reading the file",
                path=(*path, name),
            )
        seen.add(name)


def _named_nodes(mapping_node: yaml.MappingNode) -> dict[str, yaml.Node]:
    """One mapping node's entries, keyed by name.

    A repeated name is refused above rather than resolved here, so the dict
    below cannot be silently dropping an entry the author wrote.
    """
    _reject_repeated_key(mapping_node, (ATTRIBUTES_KEY,))

    named: dict[str, yaml.Node] = {}
    for key_node, value_node in mapping_node.value:
        # A non-scalar key cannot arrive here: YAML's own constructor needs a
        # hashable key, so `_single_mapping` has already rejected `? [a, b]` as
        # unparseable. Asserted rather than handled, because a rejection written
        # for it would be a branch no document can reach -- and an unreachable
        # handler reads as a guarantee that something checks this, when nothing
        # here does.
        assert isinstance(key_node, yaml.ScalarNode)
        # Unquoted only, for the same reason the declared null is unquoted only:
        # `"<<"` is an ordinary key to YAML, not a merge, and refusing it here
        # would apply the quoting rule in one place and ignore it in the other.
        if key_node.style is None and key_node.value == MERGE_KEY:
            raise InvalidDocument(
                f"{ATTRIBUTES_KEY} uses the YAML merge key {MERGE_KEY!r}, which intent "
                "documents may not do; every attribute a resource declares is written "
                "in the resource's own document",
                path=(ATTRIBUTES_KEY,),
            )
        named[str(key_node.value)] = value_node
    return named


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
    attribute_nodes: Mapping[str, yaml.Node],
    kind_schemas: KindSchemas,
) -> dict[str, object]:
    """Referential layer for the kind, then the schema layer for its attributes."""
    schema = kind_schemas.get(kind)
    if schema is None:
        raise InvalidDocument(
            f"unknown kind {kind!r}; known kinds are {sorted(kind_schemas)}", path=("kind",)
        )

    declared = set(attribute_nodes)
    expected = set(schema)
    if declared != expected:
        raise InvalidDocument(
            f"attributes do not match the schema for kind {kind!r}: "
            f"missing={sorted(expected - declared)} unknown={sorted(declared - expected)}",
            path=("attributes",),
        )

    # Document order, not schema order, so that the first attribute reported is
    # the first one an author reading their own file would reach.
    return {
        name: _declared_attribute(kind, name, node, schema[name])
        for name, node in attribute_nodes.items()
    }


def _declared_attribute(
    kind: str,
    attribute_name: str,
    node: yaml.Node,
    type_name: str,
) -> object:
    path = ("attributes", attribute_name)
    parse = ATTRIBUTE_TYPES.get(type_name)
    if parse is None:
        raise InvalidDocument(
            f"kind {kind!r} declares attribute {attribute_name!r} with type {type_name!r}, "
            f"which is not one of {sorted(ATTRIBUTE_TYPES)} -- the kind is wrong, "
            "not the document",
            path=path,
        )

    try:
        value = _stated_value(node, parse)
    except UnacceptableLiteral as exc:
        raise InvalidDocument(
            f"attribute {attribute_name!r} of kind {kind!r} {exc}", path=path
        ) from exc

    # Being the right type is not the same as being storable, and this table
    # only ever answered the first question. A `str` carrying a NUL or an
    # unpaired surrogate passed here, then raised `DataError` out of projection
    # -- past `ingest_revision`, which catches `IntegrityError` and
    # `OperationalError` and promises a contract of two domain errors, and into
    # the task's catch-all, whose comment says it only ever sees the
    # unanticipated. The author got a silently dropped revision instead of an
    # `InvalidDocument` naming the file and the field.
    #
    # Shared with the discovered plane rather than restated, so the two cannot
    # come to disagree about what a storable value is -- and so that widening
    # this type table (issue #53) inherits the guarantee rather than needing to
    # remember it.
    unstorable = unstorable_attribute({attribute_name: value})
    if unstorable is not None:
        raise InvalidDocument(
            f"attribute {attribute_name!r} of kind {kind!r} cannot be stored: {unstorable}",
            path=path,
        )
    return value


def _stated_value(node: yaml.Node, parse: Callable[[str], object]) -> object:
    """Node shape, then value state, then declared type -- in that order.

    The first two layers answer identically for every declared type, so a type
    added to `ATTRIBUTE_TYPES` inherits the structural rules rather than
    restating them. Only the third consults the schema, and it receives the
    scalar text alone: a parser is never handed the node, so it cannot read the
    implicit resolver's tag even by accident. That is the mechanism by which
    "the schema decides the type" stays true rather than being a convention
    (issue #55).

    Quoting is load-bearing here, and only here. `null` states a declared null;
    `"null"` and `'null'` state the three-character string. That is a real cost
    -- this barricade exists partly because `NO` and `"NO"` look identical in
    review -- and it is unavoidable while both states are expressible at all.
    """
    if not isinstance(node, yaml.ScalarNode):
        raise UnacceptableLiteral(f"must be a scalar, got a {NODE_SHAPES.get(type(node), 'node')}")

    if node.style is not None:
        # Quoted: the author said "this is content", so no keyword is read out
        # of it and the empty string is a legitimate value.
        return parse(node.value)

    if node.value == "":
        raise UnacceptableLiteral(
            'has an implicit empty value; write `null` for a declared null, or "" for '
            "an empty string -- a declared null has to be conspicuous"
        )

    if node.value == DECLARED_NULL:
        return None

    return parse(node.value)


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
