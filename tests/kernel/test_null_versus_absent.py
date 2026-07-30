"""WBS 1.5.0 specification, written before the implementation.

Spec-first for this package by the decision recorded at the opening of Phase 4:
1.5.0 settles a representation that crosses Python, JSONB, and TypeScript, and
it gates 1.5.3 and 1.5.4. Nothing behind these tests exists yet -- `PlaneValue`
is not defined and `FieldDiscrepancy` still carries bare values -- so every test
here fails at import until the implementation lands. That is the point: the
claims are reviewed with no code making them look correct.

The rule these encode is DESIGN section 13, "Null versus absent".

Revised after non-authoring review, which found five blocking defects in the
first draft. The largest: `PlaneValue` was specified with a public `.value`,
which makes the collapse recoverable rather than unavailable, and the minimal
implementation of `service.py` against it reintroduces the original defect at
the database layer with every test here still green. There is now no public
value accessor, and `test_absence_cannot_be_read_without_being_handled` is the
test that holds it.

What this package is NOT: it does not decide what absence *means* for
authority. Declared-absent as "intent has no opinion" versus declared-null as
"intent requires this empty" is precedence, and belongs to 1.5.3.
"""

from dataclasses import FrozenInstanceError

import pytest

from datum.reconcile.diff import reconcile
from datum.reconcile.domain import PlaneValue, ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key

T = "t1"

ABSENT = PlaneValue.absent()


def value_of(plane_value):
    """Read a PlaneValue's value in a test, stating what absence means here.

    There is deliberately no shorter way. `resolve` is the only accessor, and
    it cannot be called without an on_absent branch -- that is the guarantee
    under test, so the tests live under it rather than around it.
    """
    return plane_value.resolve(on_absent=lambda: ABSENT, on_present=lambda v: v)


def declared(**attributes):
    return ResourceSnapshot("Deployment", T, "default", "web", None, attributes)


def discovered(**attributes):
    return ResourceSnapshot("Deployment", T, "default", "web", "u1", attributes)


def only_discrepancy(declared_snapshot, discovered_snapshot):
    match_result = match_by_natural_key([declared_snapshot], [discovered_snapshot])
    assert len(match_result.pairs) == 1, "snapshots did not pair; the comparison never ran"
    diff = reconcile(match_result)
    assert not diff.orphans
    assert len(diff.field_discrepancies) == 1, "expected exactly one field discrepancy"
    return diff.field_discrepancies[0]


def no_discrepancy(declared_snapshot, discovered_snapshot):
    """Assert agreement, and assert the two snapshots actually met.

    The pairing assertion is not defensive noise. Without it, a typo in scope
    or name -- or a change to natural-key matching in 1.5.1, the very next
    package -- turns every negative test in this file into two orphans, zero
    field discrepancies, and a green pass asserting nothing about comparison.
    """
    match_result = match_by_natural_key([declared_snapshot], [discovered_snapshot])
    assert len(match_result.pairs) == 1, "snapshots did not pair; the comparison never ran"
    diff = reconcile(match_result)
    assert not diff.orphans
    return not diff.field_discrepancies


# --- The guarantee the interface exists to provide ---------------------------


def test_absence_cannot_be_read_without_being_handled():
    """The boundary claim in DESIGN section 13, made executable.

    A hostile-implementer test in the sense named at Phase 3 close: it is the
    review act of "try to reintroduce the fixed defect" turned into something
    that runs on every push instead of once.

    The defect being guarded is `declared_value=fd.declared.value` in
    service.py -- the minimal edit that writes JSON null for both states and
    puts the collapse back at the database layer. If a public value accessor
    ever appears, this test fails and the boundary claim in section 13 becomes
    false and must be rewritten rather than quietly relied upon.
    """
    absent = PlaneValue.absent()
    present = PlaneValue.of("nginx")

    for name in ("value", "val", "unwrap", "get"):
        assert not hasattr(absent, name), f"public accessor {name!r} reintroduces the collapse"
        assert not hasattr(present, name), f"public accessor {name!r} reintroduces the collapse"

    assert present.resolve(on_absent=lambda: "ABSENT", on_present=lambda v: v) == "nginx"
    assert absent.resolve(on_absent=lambda: "ABSENT", on_present=lambda v: v) == "ABSENT"


def test_an_absent_value_carrying_a_value_is_unconstructible():
    """The domain refuses the state the check constraint rejects.

    Without this, the domain can hold a row Postgres will not accept, and the
    conflict surfaces as IntegrityError from inside run_reconciliation's
    transaction -- wearing the database's abstraction rather than this
    module's, which section 6 forbids, and taking down reconciliation for the
    whole tenant rather than one field.

    Asserted against the raw constructor rather than the factories, because
    `of` and `absent` cannot produce it by construction. The validation is
    what stops a later refactor from reaching the state around them.
    """
    with pytest.raises(ValueError):
        PlaneValue(_present=False, _value="x")


def test_plane_values_are_immutable():
    with pytest.raises(FrozenInstanceError):
        PlaneValue.of(3)._present = False


# --- The truth table in DESIGN section 13, one test per reachable row --------
#
# The absent/absent row is vacuous by construction: _field_discrepancies walks
# the union of both sides' keys, so a key in neither is never visited. It has
# no test because no input produces it. See section 13.


def test_null_on_both_planes_is_not_a_discrepancy():
    """Both planes state the same thing. Agreement on emptiness is agreement."""
    assert no_discrepancy(declared(image=None), discovered(image=None))


def test_equal_values_on_both_planes_are_not_a_discrepancy():
    assert no_discrepancy(declared(replicas=3), discovered(replicas=3))


def test_differing_values_on_both_planes_are_a_discrepancy():
    fd = only_discrepancy(declared(replicas=3), discovered(replicas=5))
    assert fd.declared == PlaneValue.of(3)
    assert fd.discovered == PlaneValue.of(5)


def test_declared_absent_against_discovered_null():
    fd = only_discrepancy(declared(replicas=3), discovered(replicas=3, image=None))
    assert fd.field_name == "image"
    assert fd.declared == ABSENT
    assert fd.discovered == PlaneValue.of(None)


def test_declared_null_against_discovered_absent():
    fd = only_discrepancy(declared(replicas=3, image=None), discovered(replicas=3))
    assert fd.field_name == "image"
    assert fd.declared == PlaneValue.of(None)
    assert fd.discovered == ABSENT


def test_declared_absent_against_a_real_value():
    fd = only_discrepancy(declared(replicas=3), discovered(replicas=3, image="nginx"))
    assert fd.declared == ABSENT
    assert fd.discovered == PlaneValue.of("nginx")


def test_declared_null_against_a_real_value():
    fd = only_discrepancy(declared(replicas=3, image=None), discovered(replicas=3, image="nginx"))
    assert fd.declared == PlaneValue.of(None)
    assert fd.discovered == PlaneValue.of("nginx")


def test_a_real_value_against_discovered_absent():
    """The mirrored direction, enumerated rather than inferred.

    The rule's own claim is that direction matters, so the discovered-side
    cases are written out. The first draft of this spec tested only the
    declared-empty direction and would have passed against an implementation
    that handled the declared side properly and reused a bare-None helper for
    the discovered side.
    """
    fd = only_discrepancy(declared(replicas=3, image="nginx"), discovered(replicas=3))
    assert fd.declared == PlaneValue.of("nginx")
    assert fd.discovered == ABSENT


def test_a_real_value_against_discovered_null():
    fd = only_discrepancy(declared(replicas=3, image="nginx"), discovered(replicas=3, image=None))
    assert fd.declared == PlaneValue.of("nginx")
    assert fd.discovered == PlaneValue.of(None)


# --- The distinction the old representation collapsed ------------------------


def test_absent_and_null_are_different_values_of_the_same_type():
    assert ABSENT != PlaneValue.of(None)
    assert value_of(PlaneValue.of(None)) is None


def test_flipping_absent_to_null_updates_a_record_rather_than_creating_one():
    """The property that matters for the section 23.2 identity rule.

    A discrepancy's durable identity is (tenant, kind, scope, name, type,
    field_name). Presence is content, not identity -- so a field flipping from
    absent to null must produce a *different* discrepancy at the *same*
    identity. If presence ever leaks into identity, the flip creates a second
    record and silently drops any suppression made against the first, which is
    the failure the identity rule exists to prevent.
    """
    absent_side = only_discrepancy(declared(replicas=3), discovered(replicas=3, image="nginx"))
    null_side = only_discrepancy(
        declared(replicas=3, image=None), discovered(replicas=3, image="nginx")
    )
    assert absent_side.natural_key == null_side.natural_key
    assert absent_side.field_name == null_side.field_name
    assert absent_side.declared != null_side.declared
    assert absent_side != null_side


def test_a_present_falsy_value_is_not_absence():
    """Presence is not truthiness. Zero, empty string, and empty list are values."""
    for falsy in (0, "", [], {}, False):
        fd = only_discrepancy(declared(f=falsy), discovered(f="something"))
        assert fd.declared == PlaneValue.of(falsy)
        assert fd.declared != ABSENT


def test_zero_and_false_are_not_the_same_plane_value():
    """Equality is type-strict, because the comparison that produced it is.

    Structural dataclass equality would pass this vacuously: in Python
    0 == False, and 0 == 0.0. Meanwhile _canonical distinguishes "0" from
    "false", so plain equality here would let a report contradict the
    comparison that generated it -- a live risk, because these values round
    trip through JSONB.
    """
    assert PlaneValue.of(0) != PlaneValue.of(False)
    assert PlaneValue.of(0) != PlaneValue.of(0.0)
    assert PlaneValue.of("") != PlaneValue.of(0)
    assert PlaneValue.of(1) != PlaneValue.of(True)


def test_plane_values_are_hashable_including_around_a_dict():
    """Hashable so discrepancy sets can be deduplicated by content.

    A PlaneValue may wrap a dict, which is unhashable, so hashing has to go
    through the canonical form rather than the raw value.
    """
    assert len({PlaneValue.of(0), PlaneValue.of(False), ABSENT, PlaneValue.of(None)}) == 4
    assert len({PlaneValue.of({"a": 1}), PlaneValue.of({"a": 2})}) == 2


# --- Hostile input: the reason absence is not encoded inside the value -------


def test_a_payload_shaped_like_a_sentinel_is_a_value_not_absence():
    """The argument against encoding absence inside the JSON, made executable.

    A provider is untrusted data and may return anything, including whatever
    object a sentinel scheme would have reserved. Under presence flags this is
    unremarkable. Under a reserved-object scheme it would be indistinguishable
    from absence, and no test could tell the difference either.
    """
    trap = {"__absent__": True}
    fd = only_discrepancy(declared(f="real"), discovered(f=trap))
    assert fd.discovered == PlaneValue.of(trap)
    assert fd.discovered != ABSENT


def test_a_declared_payload_shaped_like_a_sentinel_is_also_just_a_value():
    trap = {"__absent__": True}
    assert no_discrepancy(declared(f=trap), discovered(f=trap))


# --- Invariants that must survive the change ---------------------------------
#
# Determinism (D5) is NOT re-tested here with a same-process double call. That
# proves nothing beyond "reconcile does not mutate its input or read a clock",
# and tests/kernel/test_diff.py already holds the real invariant under
# Hypothesis by varying input order. The implementation extends that generator
# to produce absent and null attributes rather than adding a weaker test here.


def test_every_differing_field_is_reported_in_canonical_order():
    """Ordering is a stated determinism requirement, so assert the list."""
    d = declared(replicas=3, image=None)
    x = discovered(replicas=5, region="uk-london-1")
    diff = reconcile(match_by_natural_key([d], [x]))
    assert [fd.field_name for fd in diff.field_discrepancies] == ["image", "region", "replicas"]


def test_presence_is_reported_for_orphans_by_not_being_reported_at_all():
    """An orphan has no counterpart plane, so it carries no per-field presence.

    Guards against the implementation growing a third state -- an orphan whose
    fields are all 'absent on the other side' -- which would turn one resource
    orphan into one discrepancy per field and flood the queue.
    """
    diff = reconcile(match_by_natural_key([declared(replicas=3)], []))
    assert len(diff.orphans) == 1
    assert not diff.field_discrepancies
