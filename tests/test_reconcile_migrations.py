"""The CF-6 backfill, which is the only migration code with a decision in it.

Every other migration in `reconcile` is declarative, and runs to completion
building the test database. This one is a `RunPython` whose body never executes
there -- a fresh database has no prior matches to backfill -- so it sat at 45%
while the gate that covers it read green, because the suite failed before the
gate ever ran.

It is worth covering rather than exempting. The four anchor columns are what a
human's decision is about, and this function is the only thing that puts them
on rows written before the anchor existed. If it is wrong, an upgrade silently
unanchors every standing decision in a live deployment, which is CF-6 again
arriving by a different road.
"""

from importlib import import_module

import pytest
from django.apps import apps

from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus, Confidence, MatchState, MatchStrategy
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind
from datum.reconcile.models import Match

# A module whose name begins with a digit cannot be imported by statement.
backfill_anchor_from_fks = import_module(
    "datum.reconcile.migrations.0003_cf6_match_anchor"
).backfill_anchor_from_fks

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def _declared():
    kind = Kind.objects.get(name="Deployment")
    revision = IntentRevision.objects.create(tenant_id=TENANT, commit_sha="f" * 40)
    return DeclaredResource.objects.create(
        tenant_id=TENANT,
        kind=kind,
        name="web",
        scope="default",
        attributes={"replicas": 3},
        revision=revision,
    )


def _discovered():
    kind = Kind.objects.get(name="Deployment")
    run = CollectorRun.objects.create(
        tenant_id=TENANT, collector_name="kubernetes", status=CollectorRunStatus.SUCCESS
    )
    return DiscoveredResource.objects.create(
        tenant_id=TENANT,
        kind=kind,
        name="web",
        scope="default",
        provider_id="uid-web-1",
        attributes={"replicas": 5},
        last_seen_run=run,
    )


def _match(**overrides):
    """A pre-migration row: anchor columns empty, identity carried by the keys alone."""
    fields = {
        "tenant_id": TENANT,
        "strategy": MatchStrategy.NATURAL_KEY.value,
        "confidence": Confidence.HIGH.value,
        "state": MatchState.PROPOSED,
    }
    return Match.objects.create(**(fields | overrides))


def test_backfill_copies_both_sides_of_the_anchor_off_the_foreign_keys():
    match = _match(declared_resource=_declared(), discovered_resource=_discovered())

    backfill_anchor_from_fks(apps, None)

    match.refresh_from_db()
    assert (match.declared_kind, match.declared_scope, match.declared_name) == (
        "Deployment",
        "default",
        "web",
    )
    assert match.discovered_provider_id == "uid-web-1"


def test_backfill_leaves_a_row_with_no_foreign_keys_unanchored():
    """The defensive branch, and the one that must not invent an anchor.

    A row with nothing to copy from is left empty rather than given a plausible
    default. It is INVALIDATED here because the active-match index is unique
    over the empty anchor, so a terminal row is the only way two of these can
    coexist -- which is itself why an empty anchor is not a resting state.
    """
    match = _match(state=MatchState.INVALIDATED)

    backfill_anchor_from_fks(apps, None)

    match.refresh_from_db()
    assert (match.declared_kind, match.declared_scope, match.declared_name) == ("", "", "")
    assert match.discovered_provider_id is None


def test_backfill_anchors_the_discovered_side_when_the_declared_row_is_gone():
    """One side present and the other gone is the realistic upgrade case.

    `DeclaredResource` rows are rebuilt per revision, so by the time an upgrade
    runs, a match may well have lost one foreign key and kept the other. The
    two `if`s are separate for this reason, and this test and its mirror below
    hold them apart: each asserts that the surviving side is copied *and* that
    the missing side is left empty rather than filled from the other.
    """
    match = _match(state=MatchState.INVALIDATED, discovered_resource=_discovered())

    backfill_anchor_from_fks(apps, None)

    match.refresh_from_db()
    assert match.discovered_provider_id == "uid-web-1"
    assert (match.declared_kind, match.declared_scope, match.declared_name) == ("", "", "")


def test_backfill_anchors_the_declared_side_when_the_discovered_row_is_gone():
    """The mirror, which completes the four present/absent combinations.

    Branch coverage was already satisfied without this case -- each `if` had
    both outcomes exercised somewhere across the other three tests -- which is
    exactly why the gate could not have asked for it. The claim being tested is
    the one branch coverage cannot make: that the two `if`s are independent in
    both directions, not merely that both have been entered and skipped.
    """
    match = _match(state=MatchState.INVALIDATED, declared_resource=_declared())

    backfill_anchor_from_fks(apps, None)

    match.refresh_from_db()
    assert (match.declared_kind, match.declared_scope, match.declared_name) == (
        "Deployment",
        "default",
        "web",
    )
    assert match.discovered_provider_id is None
