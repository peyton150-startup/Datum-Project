"""Tests for object field comparison (WBS 1.5.2 Phase 2F).

Covers all object modes: opaque, version, identity, ignore, recurse(N).

The claims under test come from DIFF_SEMANTICS.md section 5 and from the
Null / Missing / Empty table in its Core Principles. Three of them are the ones
worth trying to break, and each has tests written to do so rather than to
confirm:

1. Key order never changes a result, at any depth, in any mode.
2. Absence and null are two statements, not one. `missing` against `null` is a
   discrepancy, and no mode except `ignore` may collapse them.
3. `recurse(N)` changes what the audit trail can say, not what counts as equal.
   Every depth must agree with `opaque` on the verdict.
"""

from datum.reconcile.comparison import compare_object
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import FieldConfig


def config(mode: str, field_name: str = "metadata") -> FieldConfig:
    return FieldConfig(
        field_name=field_name,
        field_type="object",
        comparison={"mode": mode},
        logging="discrepancy",
    )


def compare(declared, discovered, mode: str):
    """Run one comparison, returning the verdict only."""
    is_equal, _ = compare_object(declared, discovered, config(mode))
    return is_equal


def compare_values(declared_value, discovered_value, mode: str):
    """Run one comparison of two present values."""
    return compare(PlaneValue.of(declared_value), PlaneValue.of(discovered_value), mode)


ALL_MODES = ["opaque", "version", "identity", "recurse(0)", "recurse(1)", "recurse(-1)"]
RECURSION_MODES = ["recurse(0)", "recurse(1)", "recurse(2)", "recurse(-1)"]


class TestObjectOpaque:
    """opaque: structure-preserving hash of the whole object, no drilling."""

    def test_identical_objects_match(self):
        assert compare_values({"a": 1, "b": 2}, {"a": 1, "b": 2}, "opaque") is True

    def test_key_order_does_not_matter(self):
        """DIFF_SEMANTICS section 5 corpus, case 1."""
        assert compare_values({"a": 1, "b": 2}, {"b": 2, "a": 1}, "opaque") is True

    def test_differing_value_is_a_discrepancy(self):
        assert compare_values({"a": 1}, {"a": 2}, "opaque") is False

    def test_extra_key_is_a_discrepancy(self):
        assert compare_values({"a": 1}, {"a": 1, "b": 2}, "opaque") is False

    def test_empty_objects_match(self):
        """Null / Missing / Empty table: {} against {} is agreement."""
        assert compare_values({}, {}, "opaque") is True

    def test_nested_difference_is_seen_despite_no_drilling(self):
        """Opaque does not drill, but the hash still covers nested structure."""
        assert compare_values({"u": {"n": "a"}}, {"u": {"n": "b"}}, "opaque") is False

    def test_nested_key_order_does_not_matter(self):
        """Sorting must reach every level, not only the top one."""
        declared = {"outer": {"a": 1, "b": {"x": 1, "y": 2}}}
        discovered = {"outer": {"b": {"y": 2, "x": 1}, "a": 1}}
        assert compare_values(declared, discovered, "opaque") is True

    def test_a_moved_value_is_not_the_same_structure(self):
        """Same leaves, different shape. A hash over content alone would pass."""
        assert compare_values({"a": {"b": 1}}, {"a": {}, "b": 1}, "opaque") is False

    def test_the_audit_log_records_both_hashes(self):
        _, log = compare_object(PlaneValue.of({"a": 1}), PlaneValue.of({"a": 2}), config("opaque"))
        assert log.field_type == "object"
        assert log.comparison_mode == "opaque"
        assert log.declared_transformed != log.discovered_transformed
        assert len(log.declared_transformed) == 64  # sha256 hex


class TestObjectVersion:
    """version: compare the `version` key only, ignoring everything else."""

    def test_same_version_matches(self):
        """DIFF_SEMANTICS section 5 corpus, case 5."""
        assert compare_values({"version": "v1"}, {"version": "v1"}, "version") is True

    def test_different_version_is_a_discrepancy(self):
        """DIFF_SEMANTICS section 5 corpus, case 6."""
        assert compare_values({"version": "v1"}, {"version": "v2"}, "version") is False

    def test_contents_are_ignored_when_versions_agree(self):
        declared = {"version": "v1", "note": "left"}
        discovered = {"version": "v1", "note": "right", "extra": [1, 2, 3]}
        assert compare_values(declared, discovered, "version") is True

    def test_a_missing_version_on_one_side_is_a_discrepancy(self):
        assert compare_values({"version": "v1"}, {"other": "v1"}, "version") is False

    def test_a_missing_version_on_both_sides_matches(self):
        """Issue #35. Both sides said the same thing about the version: nothing.

        The tempting wrong answer is False, on the grounds that the configured
        comparison could not run. But that reasoning applies just as well to
        null on both sides, which has always matched -- see the test below.
        Absence is a statement, and two identical statements agree.
        """
        assert compare_values({"a": 1}, {"a": 1}, "version") is True

    def test_a_null_version_on_both_sides_matches(self):
        """The case the both-absent reading had to stay consistent with."""
        assert compare_values({"version": None}, {"version": None}, "version") is True

    def test_an_absent_version_against_a_null_one_is_a_discrepancy(self):
        """Absence and null are two statements, so they do not agree.

        This is the boundary between the two tests above: neither side stating
        a version matches, but one side stating null while the other stays
        silent is a real difference.
        """
        assert compare_values({}, {"version": None}, "version") is False
        assert compare_values({"version": None}, {}, "version") is False

    def test_versions_are_compared_as_strings(self):
        """1 and "1" are the same version; the spec says compare as strings."""
        assert compare_values({"version": 1}, {"version": "1"}, "version") is True

    def test_a_null_version_is_not_the_string_none(self):
        """Issue #34. `str(None)` is `"None"`, which a provider may emit.

        The mirror of the marker test below: stringifying before comparing
        made a declared null and a discovered `"None"` indistinguishable, so
        a real discrepancy went unreported.
        """
        assert compare_values({"version": None}, {"version": "None"}, "version") is False
        assert compare_values({"version": "None"}, {"version": None}, "version") is False

    def test_two_string_none_versions_still_match(self):
        """The nearby case that must not be broken by the fix for #34.

        `"None"` is an ordinary string. Two of them agree; it is only the
        collision with real null that was wrong.
        """
        assert compare_values({"version": "None"}, {"version": "None"}, "version") is True

    def test_a_version_valued_like_the_missing_marker_is_still_a_value(self):
        """Presence is read off the key, not off the extracted string.

        A marker-based presence check reports False here, because both sides
        stringify to the marker it uses for absence.
        """
        assert compare_values({"version": "<missing>"}, {"version": "<missing>"}, "version") is True

    def test_a_version_valued_like_the_null_marker_is_still_a_value(self):
        """The same trap as the marker test above, for the null marker.

        A value of `"<null>"` renders like null in the audit log, and must
        still not be treated as null when deciding.
        """
        assert compare_values({"version": "<null>"}, {"version": "<null>"}, "version") is True
        assert compare_values({"version": "<null>"}, {"version": None}, "version") is False


class TestObjectIdentity:
    """identity: compare the `id` key only, ignoring everything else."""

    def test_same_id_matches_despite_differing_contents(self):
        """DIFF_SEMANTICS section 5 corpus, case 7."""
        declared = {"id": "123"}
        discovered = {"id": "123", "timestamp": "2026-07-30"}
        assert compare_values(declared, discovered, "identity") is True

    def test_different_id_is_a_discrepancy(self):
        assert compare_values({"id": "123"}, {"id": "456"}, "identity") is False

    def test_a_missing_id_on_one_side_is_a_discrepancy(self):
        assert compare_values({"id": "123"}, {"name": "123"}, "identity") is False

    def test_a_missing_id_on_both_sides_matches(self):
        """Issue #35, through the other keyed mode.

        `identity` and `version` share one helper, so the rule must hold for
        both -- and a helper that read the wrong key would pass the version
        tests alone.
        """
        assert compare_values({"a": 1}, {"a": 1}, "identity") is True

    def test_a_null_id_is_not_the_string_none(self):
        """Issue #34, through the other keyed mode."""
        assert compare_values({"id": None}, {"id": "None"}, "identity") is False

    def test_ids_are_compared_as_strings(self):
        assert compare_values({"id": 123}, {"id": "123"}, "identity") is True

    def test_the_audit_log_distinguishes_an_absent_key_from_a_null_one(self):
        """A log that spelled both the same could not explain the verdict.

        The two statements decide differently, so a reader has to be able to
        tell which one each side made.
        """
        _, absent_log = compare_object(
            PlaneValue.of({}), PlaneValue.of({"id": "x"}), config("identity")
        )
        _, null_log = compare_object(
            PlaneValue.of({"id": None}), PlaneValue.of({"id": "x"}), config("identity")
        )
        assert absent_log.declared_transformed != null_log.declared_transformed

    def test_the_version_key_is_not_consulted(self):
        """Each keyed mode reads its own key. A shared helper could mix them up."""
        declared = {"id": "same", "version": "v1"}
        discovered = {"id": "same", "version": "v2"}
        assert compare_values(declared, discovered, "identity") is True
        assert compare_values(declared, discovered, "version") is False


class TestObjectIgnore:
    """ignore: always matches, never produces a discrepancy."""

    def test_differing_objects_match(self):
        """DIFF_SEMANTICS section 5 corpus, case 8."""
        assert compare_values({"internal": "state"}, {"internal": "DIFFERENT"}, "ignore") is True

    def test_absence_on_one_side_still_matches(self):
        """The short-circuit must sit ahead of presence handling, not behind it.

        An implementation that runs the null/absent gate first reports a
        discrepancy here, which is exactly what `ignore` promises not to do.
        """
        assert compare(PlaneValue.absent(), PlaneValue.of({"a": 1}), "ignore") is True

    def test_absent_against_null_still_matches(self):
        assert compare(PlaneValue.absent(), PlaneValue.of(None), "ignore") is True

    def test_a_non_object_value_still_matches(self):
        assert compare_values("not an object", {"a": 1}, "ignore") is True

    def test_the_audit_log_says_nothing_was_read(self):
        _, log = compare_object(PlaneValue.of({"secret": 1}), PlaneValue.absent(), config("ignore"))
        assert log.result is True
        assert log.declared_transformed == "<ignored>"
        assert log.discovered_transformed == "<ignored>"


class TestObjectRecurse:
    """recurse(N): drill N levels, then compare what is left opaquely."""

    def test_full_recursion_finds_a_nested_difference(self):
        """DIFF_SEMANTICS section 5 corpus, case 2."""
        declared = {"user": {"name": "alice"}}
        discovered = {"user": {"name": "Alice"}}
        assert compare_values(declared, discovered, "recurse(-1)") is False

    def test_depth_zero_is_opaque_and_still_finds_it(self):
        """DIFF_SEMANTICS section 5 corpus, case 3."""
        declared = {"user": {"name": "alice"}}
        discovered = {"user": {"name": "Alice"}}
        assert compare_values(declared, discovered, "recurse(0)") is False

    def test_depth_zero_matches_when_the_objects_agree(self):
        """DIFF_SEMANTICS section 5 corpus, case 4."""
        declared = {"user": {"name": "alice"}}
        discovered = {"user": {"name": "alice"}}
        assert compare_values(declared, discovered, "recurse(0)") is True

    def test_a_difference_below_the_depth_limit_is_still_found(self):
        """The limit bounds drilling, not detection.

        Below the limit the remaining subtree is compared as one value, so a
        depth-3 difference under recurse(1) is still a discrepancy. An
        implementation that stops *looking* at the limit reports a match.
        """
        declared = {"a": {"b": {"c": {"d": 1}}}}
        discovered = {"a": {"b": {"c": {"d": 2}}}}
        assert compare_values(declared, discovered, "recurse(1)") is False

    def test_every_depth_agrees_with_opaque_on_the_verdict(self):
        """Recursion changes the reporting, never the answer."""
        pairs = [
            ({"a": 1}, {"a": 1}),
            ({"a": 1}, {"a": 2}),
            ({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 1}}}),
            ({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 2}}}),
            ({"a": {"b": 1}}, {"a": {"b": 1, "extra": 2}}),
            ({}, {}),
            ({"a": {}}, {"a": {}}),
        ]
        for declared, discovered in pairs:
            expected = compare_values(declared, discovered, "opaque")
            for mode in RECURSION_MODES:
                assert (
                    compare_values(declared, discovered, mode) is expected
                ), f"{mode} disagreed with opaque on {declared} vs {discovered}"

    def test_key_order_does_not_matter_at_any_depth(self):
        declared = {"a": {"x": 1, "y": {"p": 1, "q": 2}}, "b": 2}
        discovered = {"b": 2, "a": {"y": {"q": 2, "p": 1}, "x": 1}}
        for mode in RECURSION_MODES:
            assert compare_values(declared, discovered, mode) is True

    def test_a_key_present_on_one_side_only_is_a_discrepancy(self):
        assert compare_values({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}}, "recurse(-1)") is False

    def test_the_audit_log_names_the_diverging_path(self):
        declared = {"spec": {"image": "nginx", "replicas": 3}}
        discovered = {"spec": {"image": "nginx", "replicas": 5}}
        _, log = compare_object(
            PlaneValue.of(declared), PlaneValue.of(discovered), config("recurse(-1)")
        )
        assert log.result is False
        assert any("spec.replicas" in step for step in log.steps)

    def test_the_diverging_path_is_the_first_in_sorted_order(self):
        """Determinism: the reported path cannot depend on insertion order."""
        declared = {"zebra": 1, "alpha": 1}
        discovered = {"zebra": 2, "alpha": 2}
        _, forward = compare_object(
            PlaneValue.of(declared), PlaneValue.of(discovered), config("recurse(-1)")
        )
        reversed_declared = {"alpha": 1, "zebra": 1}
        _, backward = compare_object(
            PlaneValue.of(reversed_declared), PlaneValue.of(discovered), config("recurse(-1)")
        )
        assert any("Diverged at: alpha" in step for step in forward.steps)
        assert forward.steps == backward.steps

    def test_a_divergence_at_the_root_is_named_root(self):
        """recurse(0) never enters a key, so there is no path to report."""
        _, log = compare_object(
            PlaneValue.of({"a": 1}), PlaneValue.of({"a": 2}), config("recurse(0)")
        )
        assert any("Diverged at: <root>" in step for step in log.steps)

    def test_a_nested_non_object_stops_the_drilling_without_error(self):
        """Recursion must survive a subtree that is a list, not an object."""
        assert compare_values({"a": [1, 2]}, {"a": [1, 2]}, "recurse(-1)") is True
        assert compare_values({"a": [1, 2]}, {"a": [2, 1]}, "recurse(-1)") is False

    def test_a_subtree_that_is_an_object_on_one_side_only(self):
        assert compare_values({"a": {"b": 1}}, {"a": "b"}, "recurse(-1)") is False


class TestObjectNullAbsentAndEmpty:
    """The Null / Missing / Empty table, per mode.

    This is where the four earlier comparison phases share a gap: they fold
    absence into None before comparing, which makes `missing` against `null`
    read as a match. These tests hold the object path to the table instead.
    """

    def test_absent_on_both_sides_matches(self):
        for mode in ALL_MODES:
            assert compare(PlaneValue.absent(), PlaneValue.absent(), mode) is True

    def test_null_on_both_sides_matches(self):
        for mode in ALL_MODES:
            assert compare(PlaneValue.of(None), PlaneValue.of(None), mode) is True

    def test_absent_against_null_is_a_discrepancy(self):
        """Field absence is not an explicit null, in either direction."""
        for mode in ALL_MODES:
            assert compare(PlaneValue.absent(), PlaneValue.of(None), mode) is False
            assert compare(PlaneValue.of(None), PlaneValue.absent(), mode) is False

    def test_empty_object_against_null_is_a_discrepancy(self):
        """Null / Missing / Empty table, row 10."""
        for mode in ["opaque", "recurse(-1)"]:
            assert compare(PlaneValue.of({}), PlaneValue.of(None), mode) is False
            assert compare(PlaneValue.of(None), PlaneValue.of({}), mode) is False

    def test_empty_object_against_absent_is_a_discrepancy(self):
        """Null / Missing / Empty table, row 12."""
        for mode in ["opaque", "recurse(-1)"]:
            assert compare(PlaneValue.of({}), PlaneValue.absent(), mode) is False
            assert compare(PlaneValue.absent(), PlaneValue.of({}), mode) is False

    def test_empty_object_against_empty_object_matches(self):
        """Null / Missing / Empty table, row 11."""
        for mode in ["opaque", "recurse(0)", "recurse(1)", "recurse(-1)"]:
            assert compare(PlaneValue.of({}), PlaneValue.of({}), mode) is True

    def test_absent_against_a_real_object_is_a_discrepancy(self):
        for mode in ALL_MODES:
            assert compare(PlaneValue.absent(), PlaneValue.of({"a": 1}), mode) is False

    def test_the_audit_log_distinguishes_absent_from_null(self):
        """A report that prints None for both reproduces the collapsed defect."""
        _, absent_side = compare_object(
            PlaneValue.absent(), PlaneValue.of({"a": 1}), config("opaque")
        )
        _, null_side = compare_object(
            PlaneValue.of(None), PlaneValue.of({"a": 1}), config("opaque")
        )
        assert absent_side.declared_transformed == "absent"
        assert null_side.declared_transformed == "null"
        assert absent_side.declared_transformed != null_side.declared_transformed


class TestObjectNonObjectValues:
    """A field declared as an object that a plane did not supply as one."""

    def test_a_non_object_on_one_side_is_a_discrepancy(self):
        assert compare_values("not an object", {"a": 1}, "opaque") is False
        assert compare_values({"a": 1}, ["a", 1], "opaque") is False

    def test_two_identical_non_objects_are_still_a_discrepancy(self):
        """Deliberate, and the same rule compare_list applies to a non-list.

        The two planes agree, but they agree on data the configured comparison
        never ran against. A kernel that could not perform the comparison it
        was asked for must not report a match.
        """
        assert compare_values("same", "same", "opaque") is False
        assert compare_values(7, 7, "recurse(-1)") is False

    def test_the_audit_log_names_the_type_that_was_supplied(self):
        _, log = compare_object(PlaneValue.of("oops"), PlaneValue.of({"a": 1}), config("opaque"))
        assert log.declared_transformed == "value:str"
        assert log.discovered_transformed == "value:dict"


class TestObjectCanonicalValueIdentity:
    """Object comparison inherits `canonical`, so it inherits its strictness."""

    def test_an_integer_and_its_float_are_different_values(self):
        assert compare_values({"a": 1}, {"a": 1.0}, "opaque") is False
        assert compare_values({"a": 1}, {"a": 1.0}, "recurse(-1)") is False

    def test_zero_and_false_are_different_values(self):
        assert compare_values({"a": 0}, {"a": False}, "opaque") is False
        assert compare_values({"a": 0}, {"a": False}, "recurse(-1)") is False

    def test_an_empty_string_is_not_null(self):
        assert compare_values({"a": ""}, {"a": None}, "recurse(-1)") is False

    def test_an_empty_object_is_not_an_empty_list(self):
        assert compare_values({"a": {}}, {"a": []}, "recurse(-1)") is False


class TestObjectDeterminism:
    """Identical input, identical output -- including the audit trail."""

    def test_repeated_comparison_returns_an_identical_log(self):
        declared = {"b": {"z": 1, "a": 2}, "a": [3, 4]}
        discovered = {"a": [3, 4], "b": {"a": 2, "z": 9}}
        first = compare_object(PlaneValue.of(declared), PlaneValue.of(discovered), config("opaque"))
        second = compare_object(
            PlaneValue.of(declared), PlaneValue.of(discovered), config("opaque")
        )
        assert first == second

    def test_key_insertion_order_does_not_change_the_hash(self):
        _, forward = compare_object(
            PlaneValue.of({"a": 1, "b": 2}), PlaneValue.of({"a": 1, "b": 2}), config("opaque")
        )
        _, backward = compare_object(
            PlaneValue.of({"b": 2, "a": 1}), PlaneValue.of({"a": 1, "b": 2}), config("opaque")
        )
        assert forward.declared_transformed == backward.declared_transformed


class TestObjectUnknownMode:
    """A mode the schema layer would have rejected, reaching the kernel anyway."""

    def test_an_unknown_mode_is_a_discrepancy_not_a_match(self):
        """The hostile-implementer case: the barricade removed.

        `FieldConfig` rejects an unknown object mode at construction, so this
        state is unreachable through the boundary. It is asserted anyway,
        because the failure mode if it ever becomes reachable is silent
        agreement across every object field -- the worst direction for a kernel
        to fail in.
        """
        broken = config("opaque")
        broken.comparison["mode"] = "sortof"  # past the constructor's validation

        is_equal, log = compare_object(PlaneValue.of({"a": 1}), PlaneValue.of({"a": 1}), broken)
        assert is_equal is False
        assert any("Unknown mode: sortof" in step for step in log.steps)
