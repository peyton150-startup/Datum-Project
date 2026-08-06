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

The rule now: **YAML parses an attribute scalar and the declared type validates
it.** Values are read from their nodes, and a parser receives the scalar text
alone rather than the node.

Stated that carefully because the first attempt at this claimed more. Reading
attributes from nodes is not enough on its own: the document was still *loaded*
to get its envelope, and loading is eager and schema-blind, so YAML's
conversions ran over the attributes too and a value they choked on took the
whole document down before any schema was consulted. The order is the mechanism
-- compose, select the type, then read the value -- and
`TestSelectionPrecedesConstruction` is the part of this file that pins it.

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
        """Two dates, because one of them was carrying the other's credit.

        `2026-08-03` excludes the coercion this issue was filed for: under
        `safe_load` it became a `date` and the document was rejected for not
        being a `str`.

        It excludes nothing else, and for a while that was not noticed. The
        first fix still ran YAML's constructor over the whole document before
        consulting any schema, and `2026-08-03` survived that only because it is
        a real calendar date. `2026-02-30` is the same ten characters and is
        not, so `datetime.date()` raised inside PyYAML and took the entire
        document down as "unparseable YAML" -- a value declared `str`, rejected
        for failing a conversion nobody asked for.

        Revert to constructing before selecting and the second assertion fails
        while the first still passes. That is the whole reason it is here.
        """
        assert declared("str", "2026-08-03") == "2026-08-03"
        assert declared("str", "2026-02-30") == "2026-02-30"


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
        """A bare `ValueError` escaped this barricade, and quoting decided how.

        CPython caps int-from-string at `sys.get_int_max_str_digits()`, 4300 by
        default since 3.11, and raises a `ValueError` -- not a `YAMLError` --
        past it.

        Traced rather than assumed: `ingest_revision` does not wrap the
        `parse_document_set` call, so the `ValueError` reaches the polling
        task's `except Exception` catch-all, whose own comment says it only
        catches what nobody anticipated. The revision is not ingested and every
        later poll fails the same way, permanently, until the document changes.
        The author sees "intent poll failed unexpectedly" with a PyYAML
        traceback instead of the `InvalidRevision` naming their file and field
        that the handler one clause above exists to produce.

        The two forms used to fail in two different places -- unquoted inside
        PyYAML's constructor before any Datum parser ran, quoted inside
        `_parse_integer` -- and only the quoted one could name the field. Now
        that no declared value is constructed before its type is known, both
        take the same route and give the same answer. **Asserted identically on
        purpose:** an author who quoted their integer and one who did not have
        the same mistake and get the same sentence.

        Boundaries either side: 4300 digits converts, 4301 does not. Both
        asserted, because a fix that rejected every long integer would pass a
        test that only checked the failing side.
        """
        assert declared("int", "1" * 4300) == int("1" * 4300)
        assert declared("int", '"' + "1" * 4300 + '"') == int("1" * 4300)

        for written in ("1" * 4301, '"' + "1" * 4301 + '"'):
            message = rejection("int", written)

            assert "4301 digits" in message
            assert "will not convert" in message
            assert "unparseable YAML" not in message

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


def envelope(text: str, schema: dict[str, str] | None = None) -> str:
    """Reject `text` as a whole document and return the message."""
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("d.yaml", text)], "t", {"K": schema or {"v": "str"}})
    return str(caught.value)


def document(body: str) -> str:
    return "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n" + body


class TestSelectionPrecedesConstruction:
    """The order the whole change is about: schema first, then value.

    The first attempt at this issue read attributes from their nodes but still
    ran YAML's constructor over the entire document first, to get the envelope.
    Construction is eager and schema-blind, so it reached inside `attributes`
    and converted values it had no authority over -- and where the conversion
    raised, the document died before any schema was consulted at all.

    So "the schema decides" was true of the values that got through and false
    of which documents got that far. These are the cases that tell the two
    apart: every one of them is a *legal* scalar that YAML's own conversion
    cannot complete, declared as a type that never needed the conversion.
    """

    @pytest.mark.parametrize(
        ("written", "why_yaml_chokes"),
        [
            ("2024-13-45", "month 13"),
            ("2026-02-30", "day 30 of February"),
            ("1" * 4301, "over the interpreter's digit cap"),
        ],
    )
    def test_a_scalar_yaml_cannot_convert_is_still_a_string(self, written, why_yaml_chokes):
        """Declared `str`, so no conversion was ever wanted. {why_yaml_chokes}.

        Under construction-first each of these was rejected as "unparseable
        YAML", naming the document rather than the field and blaming the syntax
        for text that is syntactically fine.
        """
        assert declared("str", written) == written

    def test_the_same_scalar_under_int_names_the_field_and_not_the_document(self):
        """The other half: rejected still, but by the schema and at the right place.

        Asserting only that these are now *accepted* as strings would pass
        against a barricade that had simply stopped checking. What makes the
        order visible is that the identical text, declared `int`, produces a
        field-scoped error from the integer grammar -- which can only happen if
        the type was consulted before the value was read.
        """
        message = rejection("int", "2024-13-45")

        assert "attribute 'v'" in message
        assert "expected a decimal integer" in message
        assert "unparseable YAML" not in message

    def test_the_envelope_still_lets_yaml_decide_and_that_is_deliberate(self):
        """Not collateral damage avoided -- a boundary held on purpose.

        `metadata.name: 007` is the integer 7 and is rejected for not being a
        string, exactly as before. These could all be reasonable names, and
        accepting them is a policy change about what an identifier may look
        like. It is not this fix, and it must not ride along inside it: the
        envelope is Datum's own structure and YAML has always decided it.
        """
        for written in ("007", "true", "NO", "2026-08-03", "1:30"):
            assert "must be a non-empty string" in envelope(
                document("").replace("name: n", f"name: {written}")
            )


class TestOneReadingOfTheDocument:
    """Every mapping obeys the same key rules, and the document is read once.

    This class began life asserting that two readings of the text could not
    disagree -- the envelope was loaded, the attributes composed -- with
    fixtures covering only repeated keys *inside* `attributes`. The name was
    broader than the fixtures and the gap was exactly where the disagreement
    lived: a repeated `attributes:` key one level up resolved to the last
    occurrence for the loader and the first for the node scan, silently.

    There is only one reading now, so that hazard is gone rather than guarded.
    The rules below survive it on their own footing: **a repeated key makes the
    document ambiguous, and silently keeping one of the two values hides input
    the author wrote.** That holds for a single reader, it holds everywhere in
    the document, and it needs no exception for `metadata` -- which the
    two-readings argument did need, because a mapping only one reader touched
    could not disagree with anyone.
    """

    def test_a_repeated_attribute_name_is_refused(self):
        """Which of two `v:` entries wins is not visible to someone reading the file."""
        assert "more than once" in rejection("int", "1\n  v: 2")

    def test_a_repeated_attributes_block_is_refused(self):
        """The historical defect: two `attributes:` blocks, read two ways.

        Verified before the first fix -- `yaml.safe_load` resolved this document
        to `{"v": "second"}` while `parse_document_set` returned `{"v":
        "first"}`, with no error raised either way. The declared plane held one
        block while the document showed the other.

        Break the refusal and this fails: the document parses and the assertion
        below never sees an exception at all.
        """
        message = envelope(document("attributes:\n  v: first\nattributes:\n  v: second\n"))

        assert "'attributes'" in message
        assert "more than once" in message

    def test_a_repeated_envelope_key_is_refused_too(self):
        """`kind:` twice resolved to the last silently, which is the same trap."""
        assert "'kind'" in envelope(
            "apiVersion: datum.dev/v1\nkind: K\nkind: Other\n"
            "metadata:\n  name: n\n  scope: s\nattributes:\n  v: x\n"
        )

    def test_a_repeated_key_is_reported_even_when_its_last_value_is_also_wrong(self):
        """The structural problem, not whatever the bad value happened to be.

        Verified against the previous implementation rather than assumed: this
        document reported "kind must be a non-empty string, got 7", because the
        envelope was checked before anything looked for repeated keys. The
        author is told to fix the `7` and would then be told about the duplicate
        -- two pushes for one document, and the second complaint is the one that
        explains the first.

        A mapping's keys are all checked before any of its values are read now,
        which is why this is the answer.
        """
        message = envelope(
            "apiVersion: datum.dev/v1\nkind: K\nkind: 7\n"
            "metadata:\n  name: n\n  scope: s\nattributes:\n  v: x\n"
        )

        assert "states 'kind' more than once" in message
        assert "must be a non-empty string" not in message

    def test_a_repeated_metadata_key_is_refused_now_too(self):
        """The exception the old rationale needed, and the new one does not.

        `metadata` was read only by the loader, so the two-readings argument had
        nothing to say about it and it was documented as a stated limit: a
        repeated `metadata.name` resolved silently to the last one. Ambiguity is
        the reason now, and ambiguity does not care how many readers there are.
        """
        message = envelope(
            "apiVersion: datum.dev/v1\nkind: K\n"
            "metadata:\n  name: a\n  name: b\n  scope: s\nattributes:\n  v: x\n"
        )

        assert "metadata states 'name' more than once" in message

    def test_the_key_rules_reach_inside_an_envelope_sequence(self):
        """A guard against a walk that stops too early, not a bug being fixed.

        Nothing the envelope reads is a sequence today -- `metadata` is a
        mapping and the identifiers are scalars -- so no document is currently
        rejected by this that would otherwise be accepted wrongly. It is here
        because "every mapping in the document obeys these rules" is the claim,
        and a walk that skipped sequence elements would quietly make it false in
        the one part of the document no reader happens to look at yet.
        """
        message = envelope(
            "apiVersion: datum.dev/v1\nkind: K\nextra:\n  - a: 1\n    a: 2\n"
            "metadata:\n  name: n\n  scope: s\nattributes:\n  v: x\n"
        )

        assert "states 'a' more than once" in message

    def test_a_quoted_merge_key_is_an_ordinary_attribute_name(self):
        """Quoting decides here as it decides for null, rather than being ignored.

        YAML treats `"<<"` as a literal key and not a merge, so refusing it
        would be the quoting rule applied in one place and dropped in the other
        -- inside the one file whose subject is a rule written twice.
        """
        [snapshot] = parse_document_set(
            [("d.yaml", document('attributes:\n  "<<": 1\n'))], "t", {"K": {"<<": "int"}}
        )

        assert snapshot.attributes == {"<<": 1}

    def test_a_merge_key_in_attributes_is_refused_by_name(self):
        """A composer does not expand merges the way a loader did.

        Left alone this surfaced as `unknown=['<<'] missing=['a']`, which names
        neither the feature nor the fix. Refused explicitly instead. A
        deliberate narrowing: a document using a merge key parsed before this
        and does not now.
        """
        message = envelope(document("defaults: &d\n  v: 1\nattributes:\n  <<: *d\n"), {"v": "int"})

        assert "merge key" in message

    def test_a_merge_key_in_the_envelope_is_refused_by_name_as_well(self):
        """Where the silence would have been worst, and it is the same rule.

        A merge under `metadata` used to expand and now would not -- so an
        author's `name` could quietly vanish and the document be rejected for a
        missing field it visibly declares. The one place a merge key is read as
        ordinary content is where quoting says so.
        """
        message = envelope(
            "d: &d\n  scope: s\napiVersion: datum.dev/v1\nkind: K\n"
            "metadata:\n  <<: *d\n  name: n\nattributes:\n  v: x\n"
        )

        assert "metadata uses the YAML merge key" in message


class TestWhatComposingLeavesToDatum:
    """The jobs the constructor was doing silently, now done out loud.

    Not constructing the document means not inheriting the constructor's
    opinions either. Each of these was previously answered by PyYAML as a side
    effect; each is now Datum's own rule, because the alternative is that it
    stops being answered at all and nobody notices which.
    """

    def test_a_self_referential_alias_is_a_document_error_not_a_crash(self):
        """This one crashed the barricade, and it crashed it on `main` too.

        `&a {k: *a}` composes to a node graph with a cycle in it. Walking that
        graph without a guard exhausts the stack, and a `RecursionError` came
        out of `parse_document_set` -- which promises `InvalidDocument` or
        nothing, and whose caller catches neither. It reached the failure path
        through `_key_lines`, so the document had already been rejected and the
        crash happened while working out which line to blame.

        Pre-existing rather than introduced here: the same three-line text takes
        `main` down today. Found while checking whether the new value walk had
        the same hole, which it did.
        """
        assert "contains itself through a YAML alias" in envelope("&a\nk: *a\n")

    def test_an_alias_to_a_finished_node_is_not_a_cycle(self):
        """The guard has to distinguish "still being built" from "seen before".

        A `seen` set instead of an in-progress one would reject this perfectly
        ordinary document, and reject it with a message about self-reference
        that would send its author looking for something that is not there.
        """
        [snapshot] = parse_document_set(
            [
                (
                    "d.yaml",
                    "shared: &s reused\napiVersion: datum.dev/v1\nkind: K\n"
                    "metadata:\n  name: n\n  scope: s\nalso: *s\nattributes:\n  v: x\n",
                )
            ],
            "t",
            {"K": {"v": "str"}},
        )

        assert snapshot.name == "n"

    def test_a_shared_node_is_built_once_and_handed_out_twice(self):
        """The mechanism, asserted exactly rather than by stopwatch.

        Two aliases to one anchor are one node in the composed graph. Building
        it once and returning the same object is what PyYAML's constructor did
        via its own node cache, and is what the envelope walk has to keep doing
        now that it has replaced the constructor. Equal-but-distinct objects
        here means the walk rebuilt it, which is correct in value and is the
        defect below in miniature.
        """
        from datum.intent.documents import _document_view, _single_mapping_node

        env, _ = _document_view(
            _single_mapping_node(
                "shared: &s {k: v}\napiVersion: datum.dev/v1\nkind: K\n"
                "metadata:\n  name: n\n  scope: s\nalso: *s\nattributes:\n  v: x\n"
            )
        )

        assert env["also"] == {"k": "v"}
        assert env["also"] is env["shared"]

    def test_an_alias_fan_out_does_not_take_exponential_time(self):
        """A 400-byte document that used to take a minute, and why.

        The walk answered "am I inside myself" and not "have I done this", so a
        node reached through `K` aliases at each of `N` levels was rebuilt
        `K**N` times. `K=6, N=9` below is 10 million rebuilds of one scalar;
        measured at 1.8s for `N=7` and growing sixfold per level, against 3ms
        for `yaml.safe_load` on the same text. This is the "billion laughs"
        shape, and it was reachable from any file in a polled repository.

        The bound is deliberately loose. Under the fix this is single-digit
        milliseconds, so a second is a thousandfold margin and cannot fail from
        a slow machine; under the bug it is tens of seconds, so the test fails
        rather than hanging a CI run forever.
        """
        import time

        levels = ["L0: &L0 [x, x, x, x, x, x]"]
        levels += [f"L{i}: &L{i} [" + ", ".join([f"*L{i - 1}"] * 6) + "]" for i in range(1, 9)]
        text = "\n".join(
            [
                *levels,
                "apiVersion: datum.dev/v1",
                "kind: K",
                "metadata:",
                "  name: n",
                "  scope: s",
                "  hidden: *L8",
                "attributes:",
                "  v: x",
            ]
        )

        started = time.perf_counter()
        [snapshot] = parse_document_set([("d.yaml", text)], "t", {"K": {"v": "str"}})
        elapsed = time.perf_counter() - started

        assert snapshot.name == "n"
        assert elapsed < 1.0

    @pytest.mark.parametrize("written", ["!!set {a, b}", "!!omap [{a: 1}]"])
    def test_a_nested_envelope_collection_may_not_be_retagged(self, written):
        """A walk reads entries and not tags, so an ignored tag is a silent change.

        The constructor built a `set` out of `!!set` and a list of pairs out of
        `!!omap`. Walking the node instead would hand back a plain mapping and a
        plain sequence and say nothing, which is a value nobody wrote -- the
        exact shape of the defect this whole change is about, reappearing one
        layer up from where it was fixed.
        """
        message = envelope(
            f"apiVersion: datum.dev/v1\nkind: K\nmetadata: {written}\nattributes:\n  v: x\n"
        )

        assert "carries the explicit tag" in message

    @pytest.mark.parametrize(
        ("where", "text"),
        [
            (
                "document root",
                "--- !!python/object/apply:os.system\napiVersion: datum.dev/v1\nkind: K\n"
                "metadata:\n  name: n\n  scope: s\nattributes:\n  v: x\n",
            ),
            (
                "attributes, misspelt tag",
                "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
                "attributes: !something-typoed\n  v: x\n",
            ),
            (
                "attributes, unsafe tag",
                "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
                "attributes: !!python/object/apply:os.system\n  v: x\n",
            ),
        ],
    )
    def test_the_two_structural_mappings_may_not_be_retagged_either(self, where, text):
        """The regression: the rule reached nested mappings and neither structural one.

        The document root and `attributes` are not values, so the value walk
        never sees them -- and the tag check was written into the value walk.
        Both were accepted with their tag ignored: `{where}` parsed silently
        here while the previous revision refused it, because that revision ran
        the whole document through the constructor and caught these as a side
        effect of the eager construction this change exists to remove.

        Verified against `86a32b0` rather than assumed -- all three of these
        were rejected there and accepted on the branch before this fix. No code
        execution was ever possible, since nothing constructs the tagged node;
        what was lost was the refusal.
        """
        assert "carries the explicit tag" in envelope(text)

    def test_a_tagged_envelope_scalar_is_still_yamls_business(self):
        """Scalars keep tag handling, and the asymmetry is the point.

        `!!str 7` genuinely is the string `7`, and the envelope has always let
        YAML say so. Only collections lose the privilege, and only because a
        walk cannot honour a collection tag without reimplementing the
        constructor.
        """
        [snapshot] = parse_document_set(
            [("d.yaml", document("attributes:\n  v: x\n").replace("name: n", "name: !!str 7"))],
            "t",
            {"K": {"v": "str"}},
        )

        assert snapshot.name == "7"

    def test_an_unsafe_tag_on_an_envelope_scalar_is_still_refused(self):
        """`SafeConstructor` still refuses what it always refused.

        Constructing per scalar rather than per document does not widen what a
        scalar may be tagged as; asserted so that "we construct less now" cannot
        be read as "we check less now".
        """
        message = envelope(
            document("attributes:\n  v: x\n").replace("name: n", "name: !!python/name:os.system ''")
        )

        assert "unparseable YAML" in message

    @pytest.mark.parametrize(
        ("written", "why"),
        [("1" * 4301, "over the digit cap"), ("2024-13-45", "not a real date")],
    )
    def test_an_envelope_scalar_yaml_cannot_convert_is_a_document_error(self, written, why):
        """The `ValueError` route out of PyYAML, which is narrower now but not gone.

        No declared attribute reaches this any more -- that is the fix. The
        envelope still does, because the envelope is still constructed, and a
        bare `ValueError` escaping here would reach the polling task's catch-all
        exactly as it used to. It now carries a line number, which it did not
        before.
        """
        (error,) = _errors(document("attributes:\n  v: x\n").replace("name: n", f"name: {written}"))

        assert "unparseable YAML" in error.message
        assert error.line == 4


def _errors(text: str) -> tuple[object, ...]:
    with pytest.raises(InvalidRevision) as caught:
        parse_document_set([("d.yaml", text)], "t", {"K": {"v": "str"}})
    return caught.value.errors


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

    def test_a_complex_attribute_name_is_refused_as_a_shape_not_as_bad_syntax(self):
        """Datum's own rule now, and it used to borrow PyYAML's by accident.

        `? [a, b]` is perfectly good YAML. It was rejected only because the
        constructor needs a hashable mapping key, so the document came back as
        "unparseable YAML: found unhashable key" -- blaming the syntax for
        something syntactically fine, and blaming it in a routine that had no
        opinion on complex keys at all. Nothing constructs the mapping now, so
        the accident is gone and the rule has to be stated.

        This asserts the *reclassification*, not merely the rejection: the old
        wording must not come back, because it names the wrong layer and would
        send an author looking for a typo they did not make.
        """
        document = (
            "d.yaml",
            "apiVersion: datum.dev/v1\nkind: K\nmetadata:\n  name: n\n  scope: s\n"
            "attributes:\n  ? [a, b]\n  : 1\n",
        )

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        message = str(caught.value)

        assert "attributes uses a sequence as a key" in message
        assert "unparseable YAML" not in message

    def test_a_complex_key_is_refused_in_the_envelope_too(self):
        """One rule for every mapping, so no corner keeps the old accident.

        The envelope is not constructed wholesale either, so the constructor is
        no longer answering this question anywhere in the document.
        """
        document = ("d.yaml", "? [a, b]\n: v\n")

        with pytest.raises(InvalidRevision) as caught:
            parse_document_set([document], "t", {"K": {"v": "str"}})

        assert "document uses a sequence as a key" in str(caught.value)


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
