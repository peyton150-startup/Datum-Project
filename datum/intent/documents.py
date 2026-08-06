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
from yaml.constructor import SafeConstructor

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

# YAML's merge key. The document is composed and never loaded, and a composer
# does not expand merges the way a loader does, so a document using one would
# see a key literally named `<<` -- silently, and anywhere in the file. Refused
# by name rather than left to surface as a baffling "unknown attribute", and
# refused rather than expanded, because expanding it would be a second
# implementation of a PyYAML feature, and because a value assembled from
# elsewhere in the file is not what the author is looking at when they read
# their own declaration.
MERGE_KEY = "<<"

# The one scalar text that states a declared null, and only unquoted. `~`,
# `Null` and `NULL` are ordinary content read by the declared type -- treating
# them as null would hand the authority back to YAML's implicit resolver
# through the one door this barricade closes (issue #55).
DECLARED_NULL = "null"

# What a node is called when one turns up somewhere it does not belong. A
# lookup rather than a branch: there are exactly three node kinds, and naming
# them in one table means an error message cannot learn a fourth spelling.
NODE_SHAPES: Mapping[type, str] = {
    yaml.SequenceNode: "sequence",
    yaml.MappingNode: "mapping",
    yaml.ScalarNode: "scalar",
}

# The tag YAML resolves onto a collection nobody has tagged by hand. An envelope
# collection is walked here rather than handed to the constructor, and a walk
# reads the entries and not the tag -- so `!!set`, `!!omap` and every unsafe
# `!!python/...` tag would quietly become an ordinary mapping or sequence, where
# the constructor either built something else out of them or refused outright.
# Checked rather than ignored: a tag that changes what a value is must not stop
# being read the moment the reader changes.
COLLECTION_TAGS: Mapping[type, str] = {
    yaml.MappingNode: "tag:yaml.org,2002:map",
    yaml.SequenceNode: "tag:yaml.org,2002:seq",
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
    """Apply the syntax, envelope, referential, and schema layers to one document.

    The document is read exactly once, as nodes. Envelope values are constructed
    from those nodes under YAML's own rules; attribute values are not constructed
    at all until their declared type has been looked up (issue #55).
    """
    document, attributes = _document_view(_single_mapping_node(text))

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
        attributes=_validated_attributes(kind, _attribute_nodes(attributes), kind_schemas),
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
        # Pinned to the loader `_single_mapping_node` used, not left to default.
        # Two composers with two resolvers could disagree about the tree they
        # are describing, and this one exists to describe that one's.
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        # The text did not survive a second parse either. The caller falls back
        # to naming the file alone, which is still better than nothing.
        return {}

    lines: dict[KeyPath, int] = {}
    _collect_key_lines(root, (), lines, set())
    return lines


def _collect_key_lines(
    node: object, prefix: KeyPath, lines: dict[KeyPath, int], visited: set[int]
) -> None:
    """Walk the composed tree recording where each key was written.

    `visited` is not an optimisation. A self-referential alias (`&a {k: *a}`)
    composes to a node graph with a cycle in it, and walking that graph without
    the guard exhausts the stack -- which this routine did, on the failure path,
    turning a rejected document into a `RecursionError` out of a barricade whose
    contract is `InvalidDocument` or nothing. Revisiting a node adds no lines
    that are not already recorded, so skipping it costs nothing.
    """
    if not isinstance(node, yaml.MappingNode) or id(node) in visited:
        return
    visited.add(id(node))
    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        path = (*prefix, str(key_node.value))
        lines[path] = key_node.start_mark.line + 1
        _collect_key_lines(value_node, path, lines, visited)


# --------------------------------------------------------------------------
# Syntax layer
# --------------------------------------------------------------------------


def _shape(node: yaml.Node) -> str:
    """What to call a node in an error message."""
    return NODE_SHAPES[type(node)]


def _single_mapping_node(text: str) -> yaml.MappingNode:
    """Parseable YAML holding exactly one mapping, composed and not constructed.

    Composing stops at the parse tree. It resolves each scalar's implicit tag
    but converts nothing, which is the whole point: `2024-13-45` and a 4301-digit
    integer are scalars here, and only become a `date()` call and an `int()` call
    if something later asks for them. Loading the document instead ran those
    conversions over every value in the file -- including values whose declared
    type is `str`, where the conversion is not merely unwanted but is the defect
    issue #55 is about. A document was then rejected as "unparseable YAML"
    because of a value YAML was never entitled to interpret.
    """
    try:
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise InvalidDocument(f"unparseable YAML: {exc}", line=_yaml_error_line(exc)) from exc

    if len(documents) != 1:
        raise InvalidDocument(
            f"expected exactly one document, found {len(documents)}; "
            "one document declares one resource"
        )
    root = documents[0]
    if not isinstance(root, yaml.MappingNode):
        raise InvalidDocument(f"document is not a mapping, got a {_shape(root)}")
    return root


def _document_view(root: yaml.MappingNode) -> tuple[Mapping[str, object], yaml.Node | None]:
    """The envelope as values, and the attributes mapping as unconstructed nodes.

    The split is the boundary this module exists to hold. Everything outside
    `attributes` is Datum's own envelope, whose meaning YAML has always decided
    and goes on deciding: `metadata.name: 007` is the integer 7 here, and is
    then rejected for not being a string. Everything inside `attributes` is the
    author's declared data, whose type the kind schema decides, so it is handed
    on as nodes and not touched until the declared type is known.

    Constructing the root wholesale is what this replaces, and it cannot be
    narrowed by asking the constructor nicely: constructing a mapping constructs
    everything under it, `attributes` included.
    """
    envelope: dict[str, object] = {}
    attributes: yaml.Node | None = None
    for name, value_node in _entries(root, ()):
        if name == ATTRIBUTES_KEY:
            attributes = value_node
        else:
            envelope[name] = _constructed(value_node, (name,), frozenset({id(root)}))
    return envelope, attributes


def _entries(mapping_node: yaml.MappingNode, path: KeyPath) -> list[tuple[str, yaml.Node]]:
    """One mapping's entries by name, with the rules every mapping obeys applied.

    Every mapping in the document comes through here, envelope and attributes
    alike, so the three rules below are stated once and cannot acquire an
    exception in the corner of the document nobody re-reads. The keys of a
    mapping are all checked before any of its values are looked at, so the
    error an author sees names the structural problem rather than whatever the
    first broken value happened to be.
    """
    entries: list[tuple[str, yaml.Node]] = []
    seen: set[str] = set()
    for key_node, value_node in mapping_node.value:
        name = _key_name(key_node, path)
        if name in seen:
            raise InvalidDocument(
                f"{_label(path)} states {name!r} more than once; a repeated key is "
                "refused rather than resolved, because which occurrence wins is "
                "not visible to the person reading the file",
                path=(*path, name),
            )
        seen.add(name)
        entries.append((name, value_node))
    return entries


def _key_name(key_node: yaml.Node, path: KeyPath) -> str:
    """The name a key states, or a refusal.

    A complex key (`? [a, b]`) is legal YAML and is refused here as Datum's own
    rule. It used to be refused by PyYAML's constructor, which needs a hashable
    key -- so the rejection arrived as "unparseable YAML", blaming the syntax
    for something that is syntactically fine. Now that nothing constructs the
    mapping, that accident is gone and the rule has to be written down. Keeping
    the old wording would have been the more misleading of the two options.
    """
    if not isinstance(key_node, yaml.ScalarNode):
        raise InvalidDocument(
            f"{_label(path)} uses a {_shape(key_node)} as a key; a key in an intent "
            "document is a plain name, not a structure",
            path=path,
        )
    # Unquoted only, for the same reason the declared null is unquoted only:
    # `"<<"` is an ordinary key to YAML, not a merge, and reading the quoted
    # form as a merge would apply the quoting rule in one place and ignore it
    # in the other.
    if key_node.style is None and key_node.value == MERGE_KEY:
        raise InvalidDocument(
            f"{_label(path)} uses the YAML merge key {MERGE_KEY!r}, which intent "
            "documents may not do; everything a resource declares is written in "
            "the resource's own document",
            path=path,
        )
    return str(key_node.value)


def _constructed(node: yaml.Node, path: KeyPath, ancestors: frozenset[int]) -> object:
    """An envelope node as the value YAML says it is.

    Collections are walked here rather than handed to the constructor, so that
    `_entries` governs every mapping in the envelope too. The scalars underneath
    are still converted by YAML itself, which is what keeps the envelope's
    behaviour exactly what it was.

    `ancestors` carries the nodes currently being built, which is what stops a
    self-referential alias (`&a {k: *a}`) from recursing forever. An alias to a
    node that is merely *finished* is not a cycle and is built again normally.
    """
    if id(node) in ancestors:
        raise InvalidDocument(
            f"{_label(path)} contains itself through a YAML alias; an intent document "
            "is a finite declaration, and a value defined in terms of itself has no "
            "text an author could have meant",
            path=path,
        )
    within = ancestors | {id(node)}

    if isinstance(node, yaml.ScalarNode):
        return _scalar_value(node, path)

    _reject_retagged_collection(node, path)
    if isinstance(node, yaml.MappingNode):
        return {
            name: _constructed(value_node, (*path, name), within)
            for name, value_node in _entries(node, path)
        }
    return [_constructed(item, path, within) for item in node.value]


def _reject_retagged_collection(node: yaml.Node, path: KeyPath) -> None:
    """A collection an author has tagged into something else is refused.

    Scalars keep YAML's tag handling, because that is the envelope's behaviour
    and `!!str 7` genuinely is the string. Collections cannot: they are walked
    rather than constructed, and a walk cannot honour `!!set` without becoming a
    second implementation of the constructor. Refusing is the honest third
    option, and it is narrower than what it replaces only in saying so.
    """
    if node.tag != COLLECTION_TAGS[type(node)]:
        raise InvalidDocument(
            f"{_label(path)} carries the explicit tag {node.tag!r}; an intent document "
            f"states a plain {_shape(node)} and lets its kind schema say what the "
            "values mean",
            path=path,
        )


def _scalar_value(node: yaml.Node, path: KeyPath) -> object:
    """One envelope scalar, converted by YAML exactly as it always was.

    A fresh constructor per scalar rather than one shared across the document:
    the only state it would carry between calls is a cache of constructed nodes,
    which no scalar needs and which would outlive the document that filled it.
    """
    try:
        return SafeConstructor().construct_object(node, deep=True)
    except yaml.YAMLError as exc:
        raise InvalidDocument(f"unparseable YAML: {exc}", line=_yaml_error_line(exc)) from exc
    except ValueError as exc:
        # Not every failure inside PyYAML is a `YAMLError`. It calls `int()` on
        # a scalar it resolved as an integer and `date()` on one it resolved as
        # a timestamp, and both raise a bare `ValueError` past it -- CPython
        # caps int-from-string at `sys.get_int_max_str_digits()`, 4300 by
        # default since 3.11, and `2024-13-45` matches the timestamp pattern
        # while being no date at all.
        #
        # This barricade promises `InvalidDocument` or nothing. Without this the
        # `ValueError` escaped `parse_document_set` entirely, past
        # `ingest_revision`, and reached the polling task's catch-all for the
        # unanticipated: the author saw "intent poll failed unexpectedly" with a
        # PyYAML traceback, and every later poll failed identically until the
        # document changed. It predates the node-level reader; what has changed
        # is how little text can still reach it, since a declared attribute is
        # no longer converted here at all.
        raise InvalidDocument(f"unparseable YAML: {exc}", path=path) from exc


def _attribute_nodes(node: yaml.Node | None) -> Mapping[str, yaml.Node]:
    """The attributes mapping as YAML nodes rather than as values.

    Declared values are read from their nodes because construction throws away
    the two things that decide what a scalar means: whether it was quoted, and
    what its author actually typed. `enabled: null` and `enabled: "null"` are
    indistinguishable once constructed, and so are `port: 1:30` and `port: 90`
    (issue #55).

    This is the only reader of the attributes mapping. `_mapping_field` is not
    also asked about it, because a key set derived in two places is a key set
    that can come to disagree with itself.
    """
    if node is None:
        raise InvalidDocument(
            f"{ATTRIBUTES_KEY} must be a mapping, got None", path=(ATTRIBUTES_KEY,)
        )
    if not isinstance(node, yaml.MappingNode):
        raise InvalidDocument(
            f"{ATTRIBUTES_KEY} must be a mapping, got a {_shape(node)}", path=(ATTRIBUTES_KEY,)
        )
    return dict(_entries(node, (ATTRIBUTES_KEY,)))


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
    scalar text alone rather than the node -- see `ATTRIBUTE_TYPES` for what
    that signature does and does not buy, which is stated there and not
    restated here.

    **The signature is the smaller half.** What actually keeps the declared type
    in charge is that the node reaching this routine has never been constructed:
    the document is composed, and nothing converts an attribute value until the
    line above has looked its type up. A parser that could not see the tag was
    never the hard part -- running YAML's conversion over the value before
    choosing a parser at all was, and that is what the order here prevents
    (issue #55).

    Quoting is load-bearing here, and only here. `null` states a declared null;
    `"null"` and `'null'` state the three-character string. That is a real cost
    -- this barricade exists partly because `NO` and `"NO"` look identical in
    review -- and it is unavoidable while both states are expressible at all.
    """
    if not isinstance(node, yaml.ScalarNode):
        raise UnacceptableLiteral(f"must be a scalar, got a {_shape(node)}")

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
