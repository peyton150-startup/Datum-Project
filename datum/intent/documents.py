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

# YAML's merge key, identified the way YAML identifies it: by the tag its own
# resolver assigns. The document is composed and never loaded, and a composer
# does not expand merges the way a loader does, so a document using one would
# see a key literally named `<<` -- silently, and anywhere in the file. Refused
# rather than expanded, because expanding it would be a second implementation of
# a PyYAML feature, and because a value assembled from elsewhere in the file is
# not what the author is looking at when they read their own declaration.
#
# **Asked, not modelled, and it was modelled first.** This started as
# `style is None and value == "<<"`, which is a hand-rolled restatement of a
# rule the resolver has already applied -- and it disagreed with YAML in both
# directions. `!!str <<` is an author explicitly saying "this is a string key",
# which `main` accepts and the spelling test rejected; `!!merge other` is a
# merge key that is not spelled `<<`, which the spelling test missed and then
# reported as the baffling "unknown attribute" the rule exists to prevent.
#
# The tag subsumes quoting, so the `style` clause is gone rather than kept
# beside this: `"<<"` resolves to `str` already, and having both would be two
# encodings of one question with the resolver as tie-breaker for neither.
MERGE_TAG = "tag:yaml.org,2002:merge"

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

    Sequences are descended too, and the path grows by the item's position. A
    mapping *inside* a sequence has keys, and stopping here left them with no
    line at all; descending without recording *which* item was worse, because
    two list items reusing a key name then collided on one path and the last one
    visited overwrote the line of the one that actually failed. The rejection
    stayed correct and pointed at a blameless entry.

    So the position is part of the path, in this walk and in the value walk,
    from the one helper both call.

    **Stated limit: one shared node gets one path, and it may not be the path
    the error names.** `visited` is marked on first arrival, so a node reachable
    by two non-cyclic routes records keys under the first only. Where that
    differs from the route the value walk took -- an anchor defined inside
    `attributes` and aliased into the envelope, say -- the rejection is still
    correct and `_locate` falls back to naming the file with no line.

    Deliberate, and the alternative is worse. Recording every route means
    dropping this set, and this set is what keeps the walk linear: the alias
    fan-out that cost 1.8 s in the value walk would reappear here, on the
    failure path, for a document already being rejected. A bounded loss of
    precision in a diagnostic beats an unbounded cost to produce it, and
    `_locate` is written to degrade exactly this way.
    """
    if id(node) in visited:
        return
    if isinstance(node, yaml.SequenceNode):
        visited.add(id(node))
        for index, item in enumerate(node.value):
            _collect_key_lines(item, (*prefix, _index_step(index)), lines, visited)
        return
    if not isinstance(node, yaml.MappingNode):
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
    """What to call a node in an error message.

    Defaulted rather than indexed. The table is total over the three node kinds
    a composer produces, so the fallback is unreachable today -- but this runs
    on the rejection path, and a `KeyError` raised while wording someone's error
    message would break the same contract the error exists to keep.
    """
    return NODE_SHAPES.get(type(node), "node")


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
    # One cache for the whole document, so a node shared between two envelope
    # keys is built once. Scoped to this call and not to the module: it is keyed
    # on `id()`, and node objects from a finished parse are free to be collected
    # and have their addresses reused by the next one.
    built: dict[int, object] = {}
    for name, value_node in _entries(root, ()):
        if name == ATTRIBUTES_KEY:
            attributes = value_node
        else:
            envelope[name] = _constructed(value_node, (name,), frozenset({id(root)}), built)
    return envelope, attributes


def _entries(mapping_node: yaml.MappingNode, path: KeyPath) -> list[tuple[str, yaml.Node]]:
    """One mapping's entries by name, with the rules every mapping obeys applied.

    **Every mapping the document *retains* comes through here** -- the root,
    `metadata`, every nested envelope mapping including inside a sequence, one
    reached through an alias, and `attributes` itself. The two positions that do
    not reach it are refused before anything reads them: a mapping used as a
    *key* by `_key_name` above, and a mapping as an *attribute value* by
    `_stated_value`. That distinction is worth stating rather than claiming
    "every mapping", because the claim is what has to be audited when the next
    rule is added here, and an overstated one audits as true.

    So the rules below are stated once and cannot acquire an exception in
    the corner of the document nobody re-reads. The keys of a mapping are all
    checked before any of its values are looked at, so the error an author sees
    names the structural problem rather than whatever the first broken value
    happened to be.

    **The tag check lives here, and that placement is the point.** It was first
    written into the value walk alone, which reaches every *nested* mapping and
    neither of the two that are structural: the document root and `attributes`
    itself. Both were then accepted with their tag ignored -- `attributes:
    !something-typoed` and a retagged root parsed silently where the previous
    revision refused them. One rule reaching two of the three places its subject
    occurs is the failure family this file keeps meeting, so the rule moved to
    the routine every mapping already had to pass through rather than gaining a
    third and fourth call site to forget.
    """
    _reject_retagged_collection(mapping_node, path)

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

    **Stated limit: a key is its source text, never its resolved type.** A key
    written `~` or `true` arrives as the string `"~"` or `"true"`, where the
    loader produced `None` and `True` -- so `true` and `"true"` are one key here
    where the loader kept them apart.

    **This does reject a document that used to be accepted**, and two earlier
    drafts of this paragraph said otherwise. It claimed "unobservable", which was
    wrong, and then "cannot change a document that ingests successfully", which
    was also wrong and merely harder to disprove. The counterexample:

        metadata:
          name: n
          scope: s
          true: 1
          "true": 2

    Extra `metadata` keys are ignored, so this is otherwise valid, and under the
    loader it held four distinct keys and ingested. Here the last two are one
    key written twice, and the document is refused as ambiguous.

    That is the right outcome -- it is the duplicate rule doing exactly what it
    says, on a document where which value wins is invisible to its author -- but
    it is a **narrowing, not a no-op**, and the honest guarantee is only this: no
    key Datum reads (`apiVersion`, `kind`, `metadata`, `provider_id`, `name`,
    `scope`) collides with a YAML keyword, so nothing is silently read from the
    wrong place. A rejection is never silent; that is the whole of the claim.
    """
    if not isinstance(key_node, yaml.ScalarNode):
        raise InvalidDocument(
            f"{_label(path)} uses a {_shape(key_node)} as a key; a key in an intent "
            "document is a plain name, not a structure",
            path=path,
        )
    # The resolver has already decided whether this is a merge; asking it is the
    # whole rule. See `MERGE_TAG` for what the spelling test got wrong in both
    # directions, and why quoting no longer needs its own clause here.
    if key_node.tag == MERGE_TAG:
        raise InvalidDocument(
            f"{_label(path)} uses the YAML merge key {str(key_node.value)!r}, which "
            "intent documents may not do; everything a resource declares is written "
            "in the resource's own document",
            path=path,
        )
    return str(key_node.value)


def _constructed(
    node: yaml.Node, path: KeyPath, ancestors: frozenset[int], built: dict[int, object]
) -> object:
    """An envelope node as the value YAML says it is.

    Collections are walked here rather than handed to the constructor, so that
    `_entries` governs every mapping in the envelope too. The scalars underneath
    are still converted by YAML itself, which is what keeps the envelope's
    behaviour exactly what it was.

    **Aliases meet one rule, in two halves, and both halves are needed.**
    `ancestors` is the nodes on the path currently being built and answers "am I
    inside myself", which is a cycle. `built` is every node this document has
    already produced a value for and answers "have I done this", which is a
    repeat. A node graph is a DAG with sharing in it, so both questions are
    real: `&a {k: *a}` is a cycle and must be refused, while `[*a, *a]` is
    ordinary reuse and must be built once and handed out twice.

    Answering only the first is what a composed graph punishes. An alias
    referenced `K` times at each of `N` nested levels reaches the same node
    `K**N` times -- exponential in fan-out *and* depth, not in depth alone.
    Nothing large is materialised, so this is not quite "billion laughs": the
    composed graph stays small and the cost is entirely in rebuilding nodes
    already built. PyYAML is not vulnerable to it precisely *because*
    `BaseConstructor.construct_object` caches by node, so replacing the
    constructor with a walk and keeping only the cycle half of its guard removed
    a protection that was never written down here.

    Scale, with the measurement named so it can be repeated rather than taken on
    trust: at `K=6`, against `parse_document_set` as it stood before this cache
    existed, a 335-byte document (`N=7`) took **1.8 s** and each further level
    multiplied that by six, while `yaml.safe_load` on the identical text stayed
    at 3 ms. Absolute figures are machine- and version-dependent; the sixfold
    step per level is the part that matters and the part to re-derive.

    `test_an_alias_fan_out_does_not_take_exponential_time` is the executable
    version of this paragraph.

    `built` is therefore not an optimisation. It is the second half of the rule
    `_collect_key_lines` states in one piece, and it is threaded the same way
    for the same reason: one mutable map, keyed on node identity, living as long
    as the walk. Two walks over one graph, one discipline.
    """
    if id(node) in ancestors:
        raise InvalidDocument(
            f"{_label(path)} contains itself through a YAML alias; an intent document "
            "is a finite declaration, and a value defined in terms of itself has no "
            "text an author could have meant",
            path=path,
        )
    # Membership, not truthiness: a declared scalar legitimately builds to
    # `None`, and `built.get(...) is None` would rebuild every null forever.
    if id(node) in built:
        return built[id(node)]

    within = ancestors | {id(node)}
    value: object
    if isinstance(node, yaml.ScalarNode):
        value = _scalar_value(node, path)
    elif isinstance(node, yaml.MappingNode):
        # Tag checked inside `_entries`, with every other mapping in the file.
        value = {
            name: _constructed(value_node, (*path, name), within, built)
            for name, value_node in _entries(node, path)
        }
    else:
        _reject_retagged_collection(node, path)
        value = [
            _constructed(item, (*path, _index_step(index)), within, built)
            for index, item in enumerate(node.value)
        ]

    # Cached only on success. A node that raised has no value to hand out, and
    # a second reference to it must reach the same refusal rather than a hole.
    built[id(node)] = value
    return value


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


def _index_step(index: int) -> str:
    """One sequence position, as a path component.

    **Written once and used by both walks, which is the whole point.** The value
    walk and the line walk each descend into sequences, and if either omitted the
    position both would still agree with each other -- they did, and that shared
    silence is what let two unrelated list items collide on one path. The line
    recorded for the item that failed was then overwritten by a later, blameless
    one, and a rejection pointed at a well-formed entry. A degradation to "no
    line at all" is honest; a confident wrong line is not.

    Bracketed rather than bare so a sequence position cannot be confused with a
    mapping key that happens to be spelt `0`.
    """
    return f"[{index}]"


def _label(path: KeyPath) -> str:
    if not path:
        return "document"
    rendered = path[0]
    for step in path[1:]:
        rendered += step if step.startswith("[") else f".{step}"
    return rendered


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
