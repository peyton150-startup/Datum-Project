"""WBS 1.5.0 specification, written before the implementation.

Spec-first for this package by the decision recorded at the opening of Phase 4:
1.5.0 settles a representation that crosses Python, JSONB, and TypeScript, and
it gates 1.5.3 and 1.5.4. Nothing behind these tests exists yet -- `PlaneValue`
is not defined and `FieldDiscrepancy` still carries bare values -- so every test
here fails at import until the implementation lands. That is the point: the
claims are reviewed with no code making them look correct.

The rule these encode is DESIGN section 13, "Null versus absent".

What this package is NOT: it does not decide what absence *means* for
authority. Declared-absent as "intent has no opinion" versus declared-null as
"intent requires this empty" is precedence, and belongs to 1.5.3. Here the
distinction is only made visible.
"""

from datum.reconcile.diff import reconcile
from datum.reconcile.domain import PlaneValue, ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key

T = "t1"

ABSENT = PlaneValue(present=False, value=None)
NULL = PlaneValue(present=True, value=None)


def declared(**attributes):
    return ResourceSnapshot("Deployment", T, "default", "web", None, attributes)


def discovered(**attributes):
    return ResourceSnapshot("Deployment", T, "default", "web", "u1", attributes)


def only_discrepancy(declared_snapshot, discovered_snapshot):
    diff = reconcile(match_by_natural_key([declared_snapshot], [discovered_snapshot]))
    assert len(diff.field_discrepancies) == 1, "expected exactly one field discrepancy"
    return diff.field_discrepancies[0]


def no_discrepancy(declared_snapshot, discovered_snapshot):
    diff = reconcile(match_by_natural_key([declared_snapshot], [discovered_snapshot]))
    return not diff.field_discrepancies


# --- The truth table in DESIGN section 13, one test per row -------------------


def test_absent_on_both_planes_is_not_a_discrepancy():
    assert no_discrepancy(declared(replicas=3), discovered(replicas=3))


def test_null_on_both_planes_is_not_a_discrepancy():
    """Both planes state the same thing. Agreement on emptiness is agreement."""
    assert no_discrepancy(declared(image=None), discovered(image=None))


def test_declared_absent_against_discovered_null_is_a_discrepancy():
    fd = only_discrepancy(declared(replicas=3), discovered(replicas=3, image=None))
    assert fd.field_name == "image"
    assert fd.declared == ABSENT
    assert fd.discovered == NULL


def test_declared_null_against_discovered_absent_is_a_discrepancy():
    fd = only_discrepancy(declared(replicas=3, image=None), discovered(replicas=3))
    assert fd.field_name == "image"
    assert fd.declared == NULL
    assert fd.discovered == ABSENT


def test_the_two_directions_are_not_the_same_discrepancy():
    """The asymmetry is the whole deliverable.

    Before 1.5.0 these two produced byte-identical reports: both sides None,
    both directions indistinguishable. A reader could not tell which plane had
    said nothing. If this test passes while the two above pass, the distinction
    is real rather than cosmetic.
    """
    forwards = only_discrepancy(declared(replicas=3), discovered(replicas=3, image=None))
    backwards = only_discrepancy(declared(replicas=3, image=None), discovered(replicas=3))
    assert forwards.declared != backwards.declared
    assert forwards.discovered != backwards.discovered
    assert (forwards.declared, forwards.discovered) != (backwards.declared, backwards.discovered)


def test_declared_absent_against_a_real_value_reports_absent_not_null():
    fd = only_discrepancy(declared(replicas=3), discovered(replicas=3, image="nginx"))
    assert fd.declared == ABSENT
    assert fd.discovered == PlaneValue(present=True, value="nginx")


def test_declared_null_against_a_real_value_reports_null_not_absent():
    fd = only_discrepancy(declared(replicas=3, image=None), discovered(replicas=3, image="nginx"))
    assert fd.declared == NULL
    assert fd.discovered == PlaneValue(present=True, value="nginx")


# --- The distinction the old representation collapsed ------------------------


def test_absent_and_null_are_different_values_of_the_same_type():
    """Just below, at, and just above the boundary between the two facts.

    `PlaneValue(False, None)` and `PlaneValue(True, None)` carry the same value
    and differ only in presence. If these ever compare equal, every test above
    passes vacuously.
    """
    assert ABSENT != NULL
    assert ABSENT.value == NULL.value
    assert ABSENT.present != NULL.present


def test_a_present_falsy_value_is_not_absence():
    """Presence is not truthiness. Zero, empty string, and empty list are values."""
    for falsy in (0, "", [], {}, False):
        fd = only_discrepancy(declared(f=falsy), discovered(f="something"))
        assert fd.declared == PlaneValue(present=True, value=falsy)
        assert fd.declared != ABSENT


# --- Hostile input: the reason absence is not encoded inside the value -------


def test_a_payload_shaped_like_a_sentinel_is_a_value_not_absence():
    """The argument against encoding absence inside the JSON, made executable.

    A provider is untrusted data and may return anything, including whatever
    object a sentinel scheme would have reserved. Under presence flags this is
    unremarkable -- it is a value, it is present, and it differs from the
    declared side. Under a reserved-object scheme it would be indistinguishable
    from absence, and no test could tell the difference either.
    """
    trap = {"__absent__": True}
    fd = only_discrepancy(declared(f="real"), discovered(f=trap))
    assert fd.discovered == PlaneValue(present=True, value=trap)
    assert fd.discovered != ABSENT


def test_a_declared_payload_shaped_like_a_sentinel_is_also_just_a_value():
    trap = {"__absent__": True}
    assert no_discrepancy(declared(f=trap), discovered(f=trap))


# --- Invariants that must survive the change ---------------------------------


def test_determinism_holds_across_the_new_representation():
    """Identical inputs, identical discrepancy set. D5, restated over presence."""
    d = declared(replicas=3, image=None, paused=False)
    x = discovered(replicas=5, paused=False, region="uk-london-1")
    first = reconcile(match_by_natural_key([d], [x]))
    second = reconcile(match_by_natural_key([d], [x]))
    assert first == second


def test_every_differing_field_is_still_reported_not_just_the_first():
    d = declared(replicas=3, image=None)
    x = discovered(replicas=5, region="uk-london-1")
    diff = reconcile(match_by_natural_key([d], [x]))
    assert {fd.field_name for fd in diff.field_discrepancies} == {"replicas", "image", "region"}


def test_presence_is_reported_for_orphans_by_not_being_reported_at_all():
    """An orphan has no counterpart plane, so it carries no per-field presence.

    Guards against the implementation growing a third state -- an orphan whose
    fields are all 'absent on the other side' -- which would turn one resource
    orphan into one discrepancy per field and flood the queue.
    """
    diff = reconcile(match_by_natural_key([declared(replicas=3)], []))
    assert len(diff.orphans) == 1
    assert not diff.field_discrepancies
