"""What a declared scalar may say, and who decides it (issue #55).

`safe_load` ran YAML 1.1's implicit resolver over every declared value, so the
document's syntax decided the type and the schema only checked the result. Two
coercions were live: a `bool` field accepted the country code `NO` as `False`,
and an `int` field accepted `1:30` as `90`. Neither errored. The declared plane
simply held a value nobody wrote.

Two more were caught, but by accident -- `1.10 -> 1.1` and `2026-08-03 -> date`
failed only because the vocabulary had no `float` and no `date` to accept them
into. That protection nobody designed disappears the moment the vocabulary
widens (issue #53), which is why this had to be settled first.

The rule now: **the schema decides the type and YAML's resolver does not.**
Values are read from their nodes, and a parser receives the scalar text alone --
never the node -- so it cannot consult the resolver's tag even by accident.

Three layers, answered in order, the first two identically for every type:
node shape, then value state, then declared type.
"""

import pytest

from datum.intent.documents import parse_document_set
from datum.intent.errors import InvalidRevision

KIND_SCHEMAS = {"K": {"v": "str"}}


def _document(written: str) -> tuple[str, str]:
    return (
        "d.yaml",
        "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
        f"attributes:\n  v: {written}\n",
    )


def declared(type_name: str, written: str) -> object:
    """The value the declared plane ends up holding, or raise InvalidRevision."""
    [snapshot] = parse_document_set([_document(written)], "t", {"K": {"v": type_name}})
    return snapshot.attributes["v"]


def rejection(type_name: str, written: str) -> str:
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([_document(written)], "t", {"K": {"v": type_name}})
    return str(caught.value)


class TestTheTwoLiveCoercions:
    """The defects issue #55 was filed for. Both silently produced a wrong value."""

    def test_a_country_code_is_not_a_boolean(self):
        """`NO` resolved to False under YAML 1.1, so Norway was a lie.

        Under the bug this excludes the document was *accepted* and the declared
        plane held `False`. A test that only asserted "rejected" would pass
        against a parser that rejected everything, so the accepted cases below
        are the other half of this claim.
        """
        assert "expected true or false" in rejection("bool", "NO")

    def test_a_time_of_day_is_not_an_integer(self):
        """`1:30` is YAML 1.1 sexagesimal and resolved to 90.

        Ninety of anything, silently, where the author wrote a duration.
        """
        assert "expected a decimal integer" in rejection("int", "1:30")


class TestTheTwoAccidentallyCaughtCoercions:
    """Caught before by the vocabulary's narrowness, now caught on purpose.

    These are the cases that would have turned live the moment #53 widened the
    declared vocabulary to reach `float`. They are accepted as strings here
    rather than merely rejected, which is the part the old accident could not do.
    """

    def test_a_version_keeps_its_trailing_zero(self):
        assert declared("str", "1.10") == "1.10"

    def test_a_date_stays_the_text_that_was_written(self):
        assert declared("str", "2026-08-03") == "2026-08-03"


class TestBooleanLiterals:
    """Lowercase and exact. Quoting is irrelevant: the schema already said bool."""

    @pytest.mark.parametrize(
        ("written", "expected"),
        [("true", True), ("false", False), ('"true"', True), ("'false'", False)],
    )
    def test_accepted(self, written, expected):
        assert declared("bool", written) is expected

    @pytest.mark.parametrize(
        "written", ["TRUE", "True", "FALSE", "False", "yes", "no", "on", "off", "y", "n", "1", "0"]
    )
    def test_refused(self, written):
        """Every spelling YAML 1.1 would have taken, and the two YAML 1.2 would.

        `True` and `FALSE` are the interesting ones: YAML 1.2's core schema
        accepts them, so this is where Datum's rule is deliberately narrower
        than the standard it resembles. Accepting `TRUE` while rejecting `NULL`
        would be two answers to one question inside one ruleset.
        """
        assert "expected true or false" in rejection("bool", written)


class TestIntegerLiterals:
    """Canonical signed decimal, no leading zeros."""

    @pytest.mark.parametrize(
        ("written", "expected"), [("3", 3), ("0", 0), ("+7", 7), ("-7", -7), ("10", 10)]
    )
    def test_accepted(self, written, expected):
        assert declared("int", written) == expected

    @pytest.mark.parametrize(
        "written", ["007", "00", "-01", "+0007", "1_000", "0x1f", "0o17", "1e3", "1:30", "three"]
    )
    def test_refused(self, written):
        """`007` is refused rather than read as 7, and that is the deliberate part.

        If the padding carries meaning the value is an identifier and belongs in
        a `str` field; truncating it silently is the same defect as reading
        `1:30` as 90. YAML 1.2's core schema would accept `007` -- another place
        this vocabulary is narrower than the standard on purpose.
        """
        assert "expected a decimal integer" in rejection("int", written)

    def test_an_integer_too_long_for_the_interpreter_is_a_document_error(self):
        """A bare `ValueError` escaped this barricade, by two separate routes.

        CPython caps int-from-string at `sys.get_int_max_str_digits()`, 4300 by
        default since 3.11, and raises a `ValueError` -- not a `YAMLError` --
        past it. Escaping `parse_document_set` means passing `ingest_revision`,
        which does not catch it, and reaching the polling task's catch-all for
        the unanticipated: a silently dropped revision rather than an error
        naming the file.

        **Unquoted** dies inside PyYAML's own constructor, which resolves the
        scalar as an integer and calls `int()` on it -- before any Datum parser
        runs. That route predates the node-level reader and escapes on `main`
        today, for a long integer anywhere in a document.

        **Quoted** is never converted by PyYAML, so it reaches `_parse_integer`,
        matches the grammar, and dies on `int(text)` there. Two throw sites, one
        interpreter limit, and fixing either alone leaves the other open.

        Boundaries either side: 4300 digits converts, 4301 does not. Both
        asserted, because a fix that rejected every long integer would pass a
        test that only checked the failing side.
        """
        assert declared("int", "1" * 4300) == int("1" * 4300)
        assert declared("int", '"' + "1" * 4300 + '"') == int("1" * 4300)

        assert "unparseable YAML" in rejection("int", "1" * 4301)

        quoted = rejection("int", '"' + "1" * 4301 + '"')

        assert "4301 digits" in quoted
        assert "will not convert" in quoted

    def test_minus_zero_is_accepted_and_is_zero(self):
        """The one place the grammar is not a bijection, pinned rather than found.

        `-0` and `0` are different texts and one value. Harmless for an integer,
        recorded so nobody later reads it as a defect.
        """
        assert declared("int", "-0") == 0


class TestStringsAreLossless:
    """Whatever YAML parsed, unchanged. No trimming, coercion or normalisation."""

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("NO", "NO"),
            ("1.10", "1.10"),
            ("~", "~"),
            ("NULL", "NULL"),
            ("Null", "Null"),
            ("yes", "yes"),
            ('""', ""),
            ("''", ""),
            ('"  x  "', "  x  "),
            ('"null"', "null"),
            ("'null'", "null"),
        ],
    )
    def test_scalar_content_is_preserved(self, written, expected):
        assert declared("str", written) == expected

    def test_an_escape_sequence_is_already_decoded(self):
        """`"tru\\u0065"` arrives as `true`, and that is correct rather than a hole.

        Escapes are decoded by the YAML parser before Datum sees the scalar, so
        the content genuinely *is* `true`. Written down because it is the case a
        reader trips over -- and note the consequence for the type next door:
        the same text against a `bool` field is a valid boolean, since quoting
        is irrelevant there.
        """
        assert declared("str", '"tru\\u0065"') == "true"
        assert declared("bool", '"tru\\u0065"') is True


class TestTheNullState:
    """One spelling, unquoted, and it is a state rather than a fourth type."""

    @pytest.mark.parametrize("type_name", ["str", "int", "bool"])
    def test_unquoted_null_is_a_declared_null_for_every_type(self, type_name):
        """The state layer runs before the type layer, so all types answer alike.

        Parametrized rather than written once: a null handled inside each
        parser would be three encodings of one rule, which is the family of bug
        this module's own subject was filed under.
        """
        assert declared(type_name, "null") is None

    @pytest.mark.parametrize("written", ['"null"', "'null'"])
    def test_quoting_null_states_the_string_instead(self, written):
        """Quoting is load-bearing here, and only here.

        `node.value` is `'null'` for the quoted and unquoted forms alike -- only
        the style separates them. This is a real cost: the barricade exists
        partly because `NO` and `"NO"` look identical in review, and it creates
        one case where they must differ. Unavoidable while both a declared null
        and the string `"null"` are expressible at all.
        """
        assert declared("str", written) == "null"

    @pytest.mark.parametrize("written", ["~", "NULL", "Null"])
    def test_yamls_other_null_spellings_are_not_nulls(self, written):
        """`~` resolves to a null tag, and is read here as the string `~`.

        Honouring the resolver's tag for these would hand it authority back
        through the one door this change closes, and would make a one-character
        string unwritable unquoted. They are ordinary content read by the
        declared type -- so against a `bool` field they are simply invalid.
        """
        assert declared("str", written) == written
        assert "expected true or false" in rejection("bool", written)

    def test_an_implicit_empty_value_is_refused(self):
        """`v:` with nothing after it is legal YAML and rejected here on purpose.

        Not a mistake being made unavailable -- `key:` is an idiom every YAML
        tool accepts, and a document passing `yamllint` fails here. The
        justification is that a declared null must be conspicuous, because
        null-versus-absent is load-bearing (WBS 1.5.0) and an invisible null is
        worse than a rejected document. The error names all three states.
        """
        message = rejection("str", "")

        assert "implicit empty value" in message
        assert "null" in message
        assert '""' in message

    def test_an_empty_quoted_string_is_a_string_not_a_null(self):
        """The distinction `safe_load` could not carry: both arrive as `''`."""
        assert declared("str", '""') == ""


class TestTheTwoParsesCannotDisagree:
    """The envelope is loaded and the attributes are composed, from one text.

    Two readings of one document is a drift hazard by construction. This class
    was written asserting that they could not disagree, and covering only
    repeated keys *inside* `attributes` and the merge key -- a name broader than
    its fixtures, and the gap was exactly where the disagreement lived. A
    repeated `attributes:` key one level up resolved to the last occurrence for
    the loader and the first for the node scan, silently.

    The fix is that a repeated key is now refused at both levels rather than
    resolved, so there is no occurrence to choose between and the two readings
    have nothing left to disagree about. These tests pin that.
    """

    def test_a_repeated_attribute_name_is_refused(self):
        """Which of two `v:` entries wins is not visible to someone reading the file.

        The loader and the node reader both happen to keep the last one, so
        this is not a disagreement -- it is the same rule as the case below,
        applied where it would otherwise have had an exception.
        """
        assert "more than once" in rejection("int", "1\n  v: 2")

    def test_a_repeated_attributes_block_is_refused(self):
        """The defect: two `attributes:` blocks, and the two readings differed.

        Verified before the fix -- `yaml.safe_load` resolved this document to
        `{"v": "second"}` while `parse_document_set` returned `{"v": "first"}`,
        with no error raised either way. The declared plane held one block while
        the document showed the other.

        Break the fix and this fails: without the refusal, the document parses
        and the assertion below never sees an exception at all.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            "attributes:\n  v: first\n"
            "attributes:\n  v: second\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "'attributes'" in str(caught.value)
        assert "more than once" in str(caught.value)

    def test_a_repeated_envelope_key_is_refused_too(self):
        """`kind:` twice resolved to the last silently, which is the same trap.

        Refused for every key rather than for `attributes` alone: a rule with an
        exception in it is what was already going wrong here, and the next
        reader added to this file would inherit the exception.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nkind: Other\n"
            "metadata:\n  name: n\n  scope: s\nattributes:\n  v: x\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "'kind'" in str(caught.value)

    def test_a_quoted_merge_key_is_an_ordinary_attribute_name(self):
        """Quoting decides here as it decides for null, rather than being ignored.

        `safe_load` treats `"<<"` as a literal key and not a merge, so refusing
        it would be the quoting rule applied in one place and dropped in the
        other -- inside the one file whose subject is a rule written twice.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            'attributes:\n  "<<": 1\n',
        )

        [snapshot] = parse_document_set([document], "t", {"K": {"<<": "int"}})

        assert snapshot.attributes == {"<<": 1}

    def test_a_merge_key_in_attributes_is_refused_by_name(self):
        """`<<` expands under `safe_load` and does not under `compose`.

        Left alone this surfaced as `unknown=['<<'] missing=['a']`, which names
        neither the feature nor the fix. Refused explicitly instead. This is a
        deliberate narrowing: a document using a merge key parsed before this
        change and does not now.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            "defaults: &d\n  v: 1\n"
            "attributes:\n  <<: *d\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "int"}})

        assert "merge key" in str(caught.value)


class TestTheAttributesMappingItself:
    """The node reader is the only reader of it, so it owns these errors now.

    `_mapping_field` used to answer them for the loaded dict. Asking both would
    be a key set derived twice, so these moved rather than being duplicated --
    which means the moved-to versions need their own cases.
    """

    def test_a_document_with_no_attributes_key_is_refused(self):
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "attributes must be a mapping" in str(caught.value)

    def test_attributes_that_are_not_a_mapping_are_refused(self):
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            "attributes: [1, 2]\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "attributes must be a mapping, got a sequence" in str(caught.value)

    def test_a_complex_attribute_name_is_refused_by_the_syntax_layer(self):
        """Pins *where* this is caught, which is why the node reader may assume it.

        YAML permits a sequence as a mapping key, but its constructor needs a
        hashable one, so `safe_load` rejects `? [a, b]` before the node reader
        runs. `_named_nodes` therefore asserts rather than handles it: a
        rejection written there would be a branch no document can reach, and an
        unreachable handler reads as a guarantee that something checks this.

        This test is what makes that assumption a checked claim rather than a
        belief -- if a future PyYAML accepted complex keys, this fails and the
        assertion becomes the thing to revisit.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            "attributes:\n  ? [a, b]\n  : 1\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "unparseable YAML" in str(caught.value)
        assert "unhashable key" in str(caught.value)


class TestNodeShape:
    """A collection where a value belongs is refused before any type is consulted."""

    @pytest.mark.parametrize(("written", "shape"), [("[1, 2]", "sequence"), ("{x: 1}", "mapping")])
    @pytest.mark.parametrize("type_name", ["str", "int", "bool"])
    def test_a_collection_is_refused_identically_for_every_type(self, type_name, written, shape):
        """No `str(node)`, no serialisation, no recursive conversion.

        `str` is the type worth checking: a lossless string parser that received
        the node instead of its text would happily stringify a whole sequence,
        and the declared plane would hold `"[1, 2]"` -- a value nobody wrote,
        which is the entire subject of this issue.
        """
        message = rejection(type_name, written)

        assert "must be a scalar" in message
        assert shape in message
