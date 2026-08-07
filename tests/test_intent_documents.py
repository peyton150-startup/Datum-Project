"""Adversarial tests for the intent validator (DESIGN section 10, WBS 1.3.1).

The validator is the barricade for the declared plane, so these lean on trying
to get bad documents *through* it rather than confirming good ones pass. No
database and no Git: `parse_document_set` is pure, which is the point.
"""

import pytest
import yaml

from datum.intent.documents import MAX_IDENTIFIER_LENGTH, parse_document_set
from datum.intent.errors import InvalidRevision

TENANT = "00000000-0000-0000-0000-000000000001"
SCHEMAS = {
    "Deployment": {"replicas": "int"},
    "Bucket": {"name_prefix": "str", "is_public": "bool"},
}

VALID = """\
apiVersion: datum.dev/v1
kind: Deployment
metadata:
  name: web
  scope: default
attributes:
  replicas: 3
"""


def parse(text: str, source: str = "web.yaml"):
    return parse_document_set([(source, text)], TENANT, SCHEMAS)


def errors_from(text: str, source: str = "web.yaml"):
    with pytest.raises(InvalidRevision) as caught:
        parse(text, source)
    return caught.value.errors


# --------------------------------------------------------------------------
# The happy path, stated once so the negative cases have a baseline
# --------------------------------------------------------------------------


def test_valid_document_becomes_a_snapshot_carrying_no_provider_id():
    (snapshot,) = parse(VALID)
    assert snapshot.kind == "Deployment"
    assert snapshot.tenant_id == TENANT
    assert snapshot.scope == "default"
    assert snapshot.name == "web"
    assert snapshot.attributes == {"replicas": 3}
    # Intent is authored before the resource exists, so it never has one.
    assert snapshot.provider_id is None


def test_empty_document_set_is_a_valid_empty_revision():
    # An emptied intent repo is a legitimate declaration of "nothing", not an error.
    assert parse_document_set([], TENANT, SCHEMAS) == []


# --------------------------------------------------------------------------
# Syntax layer
# --------------------------------------------------------------------------


def test_unparseable_yaml_is_rejected_with_a_line_number():
    (error,) = errors_from("apiVersion: datum.dev/v1\nmetadata: [unclosed\n")
    assert "unparseable YAML" in error.message
    assert error.line is not None


def test_empty_file_reports_zero_documents():
    (error,) = errors_from("")
    assert "found 0" in error.message


def test_multi_document_stream_is_rejected():
    # One document declares one resource. A stream would silently declare two.
    (error,) = errors_from(VALID + "---\n" + VALID)
    assert "found 2" in error.message


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string\n", "42\n"])
def test_non_mapping_document_is_rejected(text):
    (error,) = errors_from(text)
    assert "not a mapping" in error.message


# --------------------------------------------------------------------------
# Envelope layer
# --------------------------------------------------------------------------


def test_kubernetes_manifest_is_no_longer_accepted():
    # The phase 1 format. Datum documents speak Datum's vocabulary now, and a
    # stale manifest must fail loudly rather than parse into something plausible.
    manifest = (
        "apiVersion: apps/v1\nkind: Deployment\n"
        "metadata:\n  name: web\n  namespace: default\nspec:\n  replicas: 3\n"
    )
    (error,) = errors_from(manifest)
    assert "unsupported apiVersion" in error.message


def test_missing_api_version_is_rejected():
    (error,) = errors_from(VALID.replace("apiVersion: datum.dev/v1\n", ""))
    assert "unsupported apiVersion None" in error.message


def test_provider_id_at_document_level_is_refused_not_ignored():
    (error,) = errors_from(VALID + "provider_id: ocid1.instance.oc1\n")
    assert "provider_id" in error.message


def test_provider_id_in_metadata_is_refused_not_ignored():
    text = VALID.replace("  scope: default\n", "  scope: default\n  provider_id: abc\n")
    (error,) = errors_from(text)
    assert "provider_id" in error.message


@pytest.mark.parametrize(
    ("original", "replacement", "expected"),
    [
        ("kind: Deployment\n", "", "kind must be a non-empty string"),
        ("kind: Deployment\n", "kind: ''\n", "kind must be a non-empty string"),
        ("kind: Deployment\n", "kind: 7\n", "kind must be a non-empty string"),
        ("  name: web\n", "", "metadata.name must be a non-empty string"),
        ("  name: web\n", "  name: ''\n", "metadata.name must be a non-empty string"),
        ("  scope: default\n", "", "metadata.scope must be a non-empty string"),
    ],
)
def test_missing_or_empty_identifiers_are_rejected(original, replacement, expected):
    (error,) = errors_from(VALID.replace(original, replacement))
    assert expected in error.message


def test_metadata_must_be_a_mapping():
    text = "apiVersion: datum.dev/v1\nkind: Deployment\nmetadata: 5\nattributes: {}\n"
    (error,) = errors_from(text)
    assert "metadata must be a mapping" in error.message


def test_attributes_must_be_a_mapping():
    (error,) = errors_from(VALID.replace("attributes:\n  replicas: 3\n", "attributes: 5\n"))
    assert "attributes must be a mapping" in error.message


# Boundary: just below, at, and just above the storage limit.
@pytest.mark.parametrize("length", [MAX_IDENTIFIER_LENGTH - 1, MAX_IDENTIFIER_LENGTH])
def test_name_at_or_below_the_length_limit_is_accepted(length):
    (snapshot,) = parse(VALID.replace("name: web", "name: " + "a" * length))
    assert len(snapshot.name) == length


def test_name_one_over_the_length_limit_is_rejected():
    over = MAX_IDENTIFIER_LENGTH + 1
    (error,) = errors_from(VALID.replace("name: web", "name: " + "a" * over))
    assert f"{over} characters" in error.message


# --------------------------------------------------------------------------
# Schema layer
# --------------------------------------------------------------------------


def test_unknown_kind_is_rejected_and_names_the_known_ones():
    (error,) = errors_from(VALID.replace("kind: Deployment", "kind: Sandwich"))
    assert "unknown kind 'Sandwich'" in error.message
    assert "Bucket" in error.message and "Deployment" in error.message


def test_missing_attribute_is_rejected():
    (error,) = errors_from(VALID.replace("attributes:\n  replicas: 3\n", "attributes: {}\n"))
    assert "missing=['replicas']" in error.message


def test_unknown_attribute_is_rejected():
    # Strict by design: convenience is the sacrificed quality attribute here.
    (error,) = errors_from(VALID.replace("  replicas: 3\n", "  replicas: 3\n  colour: blue\n"))
    assert "unknown=['colour']" in error.message


def test_wrong_attribute_type_is_rejected():
    (error,) = errors_from(VALID.replace("replicas: 3", "replicas: 'three'"))
    assert "decimal integer" in error.message
    assert "'three'" in error.message


def test_boolean_is_not_accepted_as_an_integer():
    # Guards the int parser against the text `true`, which is all this can
    # demonstrate now. The bug it was originally written for -- bool subclassing
    # int in Python, so an isinstance check reads "yes replicas" as "some number
    # of replicas" -- is no longer reachable to be tested for: the parser is
    # handed scalar text and never a Python bool, so there is no isinstance
    # check left to get wrong (issue #55).
    (error,) = errors_from(VALID.replace("replicas: 3", "replicas: true"))
    assert "decimal integer" in error.message
    assert "'true'" in error.message


def test_integer_is_not_accepted_as_a_boolean():
    text = (
        "apiVersion: datum.dev/v1\nkind: Bucket\n"
        "metadata:\n  name: assets\n  scope: prod\n"
        "attributes:\n  name_prefix: img-\n  is_public: 1\n"
    )
    (error,) = errors_from(text)
    assert "true or false" in error.message
    assert "'1'" in error.message


def test_every_scalar_type_in_the_vocabulary_round_trips():
    text = (
        "apiVersion: datum.dev/v1\nkind: Bucket\n"
        "metadata:\n  name: assets\n  scope: prod\n"
        "attributes:\n  name_prefix: img-\n  is_public: false\n"
    )
    (snapshot,) = parse(text)
    assert snapshot.attributes == {"name_prefix": "img-", "is_public": False}


def test_kind_declaring_an_unsupported_type_blames_the_kind_not_the_document():
    schemas = {"Deployment": {"replicas": "int64"}}
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("web.yaml", VALID)], TENANT, schemas)
    (error,) = caught.value.errors
    assert "the kind is wrong, not the document" in error.message


# --------------------------------------------------------------------------
# Referential layer across the document set -- CF-2
# --------------------------------------------------------------------------


def test_two_documents_claiming_one_identity_are_rejected_by_the_validator():
    # CF-2. Before this check, a Postgres unique constraint caught it and raised
    # IntegrityError across a module boundary, inverting ADR-008.
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("a.yaml", VALID), ("b.yaml", VALID)], TENANT, SCHEMAS)
    (error,) = caught.value.errors
    assert "is declared by 2 documents" in error.message
    # Naming only one file leaves the author guessing which two collided.
    assert "a.yaml" in error.message and "b.yaml" in error.message
    assert "name=web" in error.message and TENANT in error.message


def test_same_name_in_a_different_scope_is_not_a_duplicate():
    other_scope = VALID.replace("scope: default", "scope: staging")
    snapshots = parse_document_set([("a.yaml", VALID), ("b.yaml", other_scope)], TENANT, SCHEMAS)
    assert {s.scope for s in snapshots} == {"default", "staging"}


def test_same_name_under_a_different_kind_is_not_a_duplicate():
    bucket = (
        "apiVersion: datum.dev/v1\nkind: Bucket\n"
        "metadata:\n  name: web\n  scope: default\n"
        "attributes:\n  name_prefix: w-\n  is_public: false\n"
    )
    snapshots = parse_document_set([("a.yaml", VALID), ("b.yaml", bucket)], TENANT, SCHEMAS)
    assert {s.kind for s in snapshots} == {"Deployment", "Bucket"}


def test_three_documents_on_one_identity_report_all_three():
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set(
            [("a.yaml", VALID), ("b.yaml", VALID), ("c.yaml", VALID)], TENANT, SCHEMAS
        )
    (error,) = caught.value.errors
    assert "is declared by 3 documents" in error.message


# --------------------------------------------------------------------------
# Whole-revision accumulation
# --------------------------------------------------------------------------


def test_every_error_is_reported_in_one_pass():
    # One push should surface every problem, not the first one repeatedly.
    bad_kind = VALID.replace("kind: Deployment", "kind: Sandwich")
    bad_type = VALID.replace("name: web", "name: api").replace("replicas: 3", "replicas: 'x'")
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("a.yaml", bad_kind), ("b.yaml", bad_type)], TENANT, SCHEMAS)
    assert len(caught.value.errors) == 2
    assert {e.source for e in caught.value.errors} == {"a.yaml", "b.yaml"}


def test_a_duplicate_is_still_reported_when_another_document_failed():
    bad = VALID.replace("kind: Deployment", "kind: Sandwich")
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set(
            [("bad.yaml", bad), ("a.yaml", VALID), ("b.yaml", VALID)], TENANT, SCHEMAS
        )
    messages = [e.message for e in caught.value.errors]
    assert any("unknown kind" in m for m in messages)
    assert any("is declared by 2 documents" in m for m in messages)


# --------------------------------------------------------------------------
# Line-level location (WBS 1.3.4)
# --------------------------------------------------------------------------

# VALID, with 1-based line numbers:
#   1  apiVersion: datum.dev/v1
#   2  kind: Deployment
#   3  metadata:
#   4    name: web
#   5    scope: default
#   6  attributes:
#   7    replicas: 3


@pytest.mark.parametrize(
    ("original", "replacement", "expected_line"),
    [
        ("apiVersion: datum.dev/v1", "apiVersion: apps/v1", 1),
        ("kind: Deployment", "kind: Sandwich", 2),
        ("  name: web", "  name: ''", 4),
        ("  scope: default", "  scope: ''", 5),
        ("attributes:\n  replicas: 3\n", "attributes: {}\n", 6),
        ("replicas: 3", "replicas: 'three'", 7),
    ],
)
def test_each_layer_reports_the_line_it_failed_on(original, replacement, expected_line):
    (error,) = errors_from(VALID.replace(original, replacement))
    assert error.line == expected_line


def test_a_failure_on_an_absent_key_names_the_file_without_inventing_a_line():
    # `apiVersion` is not in the text, so there is no line to point at. Naming
    # the file is honest; guessing a line would not be.
    (error,) = errors_from(VALID.replace("apiVersion: datum.dev/v1\n", ""))
    assert error.line is None
    assert str(error) == f"web.yaml: {error.message}"


def test_provider_id_is_located_where_it_was_written():
    text = VALID.replace("  scope: default\n", "  scope: default\n  provider_id: abc\n")
    (error,) = errors_from(text)
    assert error.line == 6


def test_a_located_error_renders_as_file_and_line():
    (error,) = errors_from(VALID.replace("kind: Deployment", "kind: Sandwich"), "deploy/web.yaml")
    assert str(error).startswith("deploy/web.yaml:2: ")


# --------------------------------------------------------------------------
# Defensive branches
#
# Reached directly rather than through `parse_document_set`, because both guard
# against a YAML error the public path cannot currently produce. They are here
# so the branch is exercised and its intent is on the record, not because the
# helpers are part of the module's interface.
# --------------------------------------------------------------------------


def test_a_yaml_error_without_a_position_yields_no_line():
    from datum.intent.documents import _yaml_error_line

    assert _yaml_error_line(yaml.YAMLError("no mark on this one")) is None


def test_key_lines_of_unparseable_text_is_empty_rather_than_raising():
    from datum.intent.documents import _key_lines

    assert _key_lines("metadata: [unclosed\n") == {}


def test_key_lines_skips_keys_that_are_not_scalars():
    # YAML permits a sequence or mapping as a key. There is no dotted path to
    # such a key, so it is skipped rather than stringified into a fake one.
    from datum.intent.documents import _key_lines

    lines = _key_lines("apiVersion: datum.dev/v1\n? [a, b]\n: value\n")
    assert lines == {("apiVersion",): 1}


def test_key_lines_descends_into_sequences():
    # A sequence carries no key, so the path does not grow through one -- but a
    # mapping inside a sequence has keys, and stopping at the sequence left them
    # with no line at all. The failure was quiet: the error named the file and
    # no position, which reads as "there is nowhere to point" rather than as a
    # gap in the walk. The value walk descends into sequences, and two walks
    # over one graph disagreeing about where keys live is how the last two
    # defects in this module started.
    from datum.intent.documents import _key_lines

    # The position is part of the path. Without it two items reusing a key name
    # collide and the later one overwrites the earlier one's line -- see
    # `test_a_defect_in_one_sequence_item_does_not_report_a_later_item`, which
    # is the consequence this shape exists to prevent.
    lines = _key_lines("apiVersion: datum.dev/v1\nitems:\n  - name: a\n  - other: b\n")

    assert lines == {
        ("apiVersion",): 1,
        ("items",): 2,
        ("items", "[0]", "name"): 3,
        ("items", "[1]", "other"): 4,
    }


# --------------------------------------------------------------------------
# Nesting deep enough to exhaust the stack (issue #66)
# --------------------------------------------------------------------------


def nested(depth: int, body_key: str = "bomb") -> str:
    """A valid document carrying a `depth`-deep flow sequence."""
    return (
        "apiVersion: datum.dev/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: web\n  scope: default\n"
        f"{body_key}: " + "[" * depth + "]" * depth + "\n"
        "attributes:\n  replicas: 3\n"
    )


@pytest.mark.parametrize(
    ("text", "because"),
    [
        (nested(1000), "the reproducer filed with issue #66, ~2 KB of brackets"),
        (nested(5000), "far past the limit rather than just over it"),
        (
            "apiVersion: datum.dev/v1\nkind: Deployment\n"
            "metadata:\n  name: web\n  scope: default\n"
            "attributes:\n  replicas: " + "[" * 1000 + "]" * 1000 + "\n",
            "deep inside attributes rather than under an unread key",
        ),
        (
            "apiVersion: datum.dev/v1\nkind: Deployment\n"
            "metadata:\n  name: web\n  scope: default\n"
            "bomb: " + "{a: " * 800 + "1" + "}" * 800 + "\n"
            "attributes:\n  replicas: 3\n",
            "mappings rather than sequences, in case only one shape recurses",
        ),
    ],
)
def test_nesting_too_deep_to_parse_is_a_document_error_not_a_RecursionError(text, because):
    """The bug excluded is a bare `RecursionError` escaping the barricade.

    `parse_document_set` promises `InvalidDocument` / `InvalidRevision` or
    nothing. Under the bug these documents raised `RecursionError` instead, so
    `ingest_revision` never saw a domain error, the poll task logged "failed
    unexpectedly" with a traceback, and **every later poll failed identically
    and permanently** until the document changed.

    `pytest.raises(InvalidRevision)` is what discriminates: `RecursionError` is
    not a subclass of it, so under the bug these fail rather than pass with a
    different message. The message assertion alone would not be enough, because
    there would be no message at all.

    One guard, in `_parse_one`, is the whole fix. A second was written into
    `_key_lines` on the theory that locating the rejection re-parses the same
    text and would exhaust the stack again; reverting it changed no test, and
    the reason is that `_locate` returns before calling `_key_lines` when the
    error carries no path -- which this one does not.
    """
    (error,) = errors_from(text)

    assert "nesting exceeds" in error.message


def test_the_too_deep_message_names_no_maximum_depth():
    """A guard on the decision, not a demonstration of the bug.

    No depth can be honestly published: the limit is the interpreter's recursion
    budget minus whatever the caller already spent, so the same file is readable
    from a shallow stack and refused from a deep one. Measured against this
    tree, the deepest accepted document was 491 levels from a bare call and 341
    with 300 caller frames already on it.

    So this fails if someone later "improves" the message by putting a number in
    it, which would be a promise Datum cannot keep. It would pass before and
    after the #66 fix, and is here to constrain the fix rather than to prove it.
    """
    (error,) = errors_from(nested(1000))

    assert not any(character.isdigit() for character in error.message)


@pytest.mark.parametrize("depth", [1, 2, 40])
def test_ordinary_nesting_is_untouched_by_the_depth_guard(depth):
    """Without this, "reject everything nested" would pass every case above.

    40 levels is comfortably inside the limit. These documents are still
    rejected -- a sequence is not a valid `int` attribute -- but rejected *for
    that reason*, and located on the line the sequence is written on, which is
    what shows the guard did not swallow them.

    Depth 1 is the shallowest thing that is nested at all, and is here because a
    guard written as `>= 1` would pass a test that only tried 40.
    """
    text = (
        "apiVersion: datum.dev/v1\nkind: Deployment\n"  # 1, 2
        "metadata:\n  name: web\n  scope: default\n"  # 3, 4, 5
        "attributes:\n  replicas: " + "[" * depth + "]" * depth + "\n"  # 6, 7
    )

    (error,) = errors_from(text)

    assert "nesting exceeds" not in error.message
    assert "must be a scalar" in error.message
    assert error.line == 7


def test_a_deep_document_that_is_valid_apart_from_its_depth_still_parses():
    """The other side of the boundary: depth alone must not reject.

    A 40-deep sequence in a field declared as an attribute of a kind that has no
    such attribute would confuse the case above with a schema error. Here the
    deep structure sits under a key the envelope does not read, so the only
    thing that could reject this document is the depth guard -- and it must not.
    """
    (snapshot,) = parse(nested(40))

    assert snapshot.attributes == {"replicas": 3}


def test_a_too_deep_rejection_reports_no_line_rather_than_a_wrong_one():
    """Why `_key_lines` needs no guard of its own, pinned so it stays true.

    The too-deep rejection carries no path, so `_locate` returns before
    re-parsing the text -- which is the reason a second `RecursionError` guard
    in `_key_lines` was unreachable and was removed. If someone later gives this
    rejection a path, `_key_lines` starts being called on a document that
    exhausted the parser once already, and this test is what notices.

    `None` is also the honest answer on its own terms: the failure has no line,
    it has a shape.
    """
    (error,) = errors_from(nested(1000))

    assert error.line is None
    assert error.source == "web.yaml"


def test_a_defect_in_one_sequence_item_does_not_report_a_later_item():
    # The failure this excludes is a *confidently wrong* line, which is worse
    # than the "no line" it replaced. Both walks descended into sequences without
    # recording which item they were in, so two list items reusing a key name
    # collided on one path and `lines[path] = ...` kept whichever was visited
    # last. The duplicate below is on lines 4-5; the reported line was 6, which
    # is `- b: 9` and is perfectly well-formed.
    #
    # Asserting the exact line rather than "not 6": a fix that indexed only one
    # of the two walks would make them disagree and produce `None`, which would
    # pass a test that merely refused the wrong answer.
    text = (
        "apiVersion: datum.dev/v1\n"  # 1
        "kind: Deployment\n"  # 2
        "extra:\n"  # 3
        "  - b: 1\n"  # 4
        "    b: 2\n"  # 5
        "  - b: 9\n"  # 6
        "metadata:\n  name: web\n  scope: default\n"
        "attributes:\n  replicas: 3\n"
    )

    (error,) = errors_from(text)

    assert "more than once" in error.message
    assert error.line == 5


def test_a_defect_inside_an_anchored_mapping_reports_the_anchor_definition():
    # The public contract, not the mechanism. A mapping written once under an
    # anchor and used again through an alias exists at one editable place in the
    # file, and that is where the author has to fix it -- PyYAML keeps the
    # anchored node's own mark, so a use site is not available to report even if
    # it were wanted.
    #
    # Asserted as behaviour rather than as agreement between the two walks,
    # because that agreement does not always hold: an anchor defined inside
    # `attributes` and aliased into the envelope is reached by the two walks on
    # different paths, and the error then names the file with no line. That is a
    # documented degrade, so pinning it as an invariant would pin something
    # false.
    text = (
        "anchor: &a\n"  # 1
        "  dup: 1\n"  # 2
        "  dup: 3\n"  # 3
        "list:\n"  # 4
        "  - *a\n" + VALID  # 5
    )

    (error,) = errors_from(text)

    assert "more than once" in error.message
    assert error.line == 3


def test_rendered_message_locates_every_error():
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("deployments/web.yaml", "- nope\n")], TENANT, SCHEMAS)
    rendered = str(caught.value)
    assert "1 error(s)" in rendered
    assert "deployments/web.yaml:" in rendered


# --------------------------------------------------------------------------
# Storability: the right type is not the same as a storable value (issue #47)
# --------------------------------------------------------------------------


def bucket_with(name_prefix_literal: str) -> str:
    """A valid Bucket whose `name_prefix` is the given YAML scalar."""
    return (
        "apiVersion: datum.dev/v1\n"
        "kind: Bucket\n"
        "metadata:\n"
        "  name: assets\n"
        "  scope: default\n"
        "attributes:\n"
        f"  name_prefix: {name_prefix_literal}\n"
        "  is_public: false\n"
    )


@pytest.mark.parametrize(
    ("literal", "names"),
    [
        (r'"a\0b"', "NUL"),
        (r'"p\ud800q"', "unpaired surrogate"),
    ],
)
def test_a_string_of_the_right_type_that_cannot_be_stored_is_rejected(literal, names):
    """The declared plane checked what a value IS and never what it holds.

    The predicate table that preceded `ATTRIBUTE_TYPES` answered `type(value)
    is str` and stopped there, so a double-quoted YAML escape decoding to a real
    NUL or an unpaired surrogate passed validation intact. It then raised
    `DataError` out of projection --
    past `ingest_revision`, whose contract is two domain errors, and into the
    poll task's catch-all for the unanticipated. The author saw a dropped
    revision and an opaque log line rather than the file and field.

    Asserted through the real parser with a real YAML escape, not by calling
    the checker with a hand-built string, because the point is that YAML
    produces this from text a person can type.
    """
    (error,) = errors_from(bucket_with(literal))

    assert "name_prefix" in str(error)
    assert "cannot be stored" in str(error)
    assert names.split()[-1] in str(error)


def test_an_ordinary_string_is_still_accepted():
    """The nearby case: the guard reads contents, so it must not reject content.

    A quoted string full of escapes that are perfectly storable. Without this,
    a guard that rejected every double-quoted scalar would pass the two cases
    above and look correct.
    """
    (snapshot,) = parse(bucket_with(r'"tab\there \u00e9\u00e8 \u4e2d\u6587"'))

    assert snapshot.attributes["name_prefix"] == "tab\there éè 中文"
