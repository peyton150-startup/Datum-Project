import pytest

from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_resources

T = "t1"


def snap(name, scope="default", replicas=1, provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_first_sighting_matches_by_natural_key():
    result = match_resources(
        [snap("web", replicas=3)], [snap("web", replicas=5, provider_id="uid1")]
    )
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.strategy == MatchStrategy.NATURAL_KEY.value
    assert pair.confidence == Confidence.HIGH.value
    assert not result.declared_orphans and not result.discovered_orphans


def test_declared_never_provisioned_is_declared_orphan():
    result = match_resources([snap("web")], [])
    assert not result.pairs
    assert [o.name for o in result.declared_orphans] == ["web"]
    assert not result.discovered_orphans


def test_discovered_undeclared_is_discovered_orphan():
    result = match_resources([], [snap("ghost", provider_id="uid9")])
    assert not result.pairs
    assert [o.name for o in result.discovered_orphans] == ["ghost"]


def test_move_of_scope_breaks_natural_key_into_two_orphans():
    # same name, different scope -> not a match (documented limitation, correct behavior)
    result = match_resources(
        [snap("web", scope="default")], [snap("web", scope="prod", provider_id="uid1")]
    )
    assert not result.pairs
    assert len(result.declared_orphans) == 1
    assert len(result.discovered_orphans) == 1


def test_same_name_two_scopes_yields_two_distinct_matches():
    result = match_resources(
        [snap("web", scope="default"), snap("web", scope="prod")],
        [
            snap("web", scope="default", provider_id="u1"),
            snap("web", scope="prod", provider_id="u2"),
        ],
    )
    assert len(result.pairs) == 2
    scopes = sorted(p.declared.scope for p in result.pairs)
    assert scopes == ["default", "prod"]


def test_duplicate_declared_natural_key_is_refused_not_silently_collapsed():
    """Two declared snapshots claiming one identity is a loader bug.

    Silently keeping the last one would drop data and make the output depend on
    input order: [d(3), d(99)] and [d(99), d(3)] would disagree.
    """
    with pytest.raises(AssertionError):
        match_resources([snap("web", replicas=3), snap("web", replicas=99)], [])


def test_duplicate_discovered_natural_key_is_refused_not_silently_collapsed():
    with pytest.raises(AssertionError):
        match_resources([], [snap("web", provider_id="u1"), snap("web", provider_id="u2")])


def test_distinct_keys_that_merely_share_a_name_are_not_duplicates():
    """The guard keys on the whole natural key, not the name: scope disambiguates."""
    result = match_resources([snap("web", scope="default"), snap("web", scope="prod")], [])
    assert len(result.declared_orphans) == 2


def test_output_is_deterministic_regardless_of_input_order():
    a = match_resources(
        [snap("a"), snap("b"), snap("c")],
        [snap("c", provider_id="uc"), snap("a", provider_id="ua")],
    )
    b = match_resources(
        [snap("c"), snap("b"), snap("a")],
        [snap("a", provider_id="ua"), snap("c", provider_id="uc")],
    )
    assert a == b
