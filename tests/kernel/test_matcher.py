from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key

T = "t1"


def snap(name, scope="default", replicas=1, provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_first_sighting_matches_by_natural_key():
    result = match_by_natural_key(
        [snap("web", replicas=3)], [snap("web", replicas=5, provider_id="uid1")]
    )
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.strategy == MatchStrategy.NATURAL_KEY.value
    assert pair.confidence == Confidence.HIGH.value
    assert not result.declared_orphans and not result.discovered_orphans


def test_declared_never_provisioned_is_declared_orphan():
    result = match_by_natural_key([snap("web")], [])
    assert not result.pairs
    assert [o.name for o in result.declared_orphans] == ["web"]
    assert not result.discovered_orphans


def test_discovered_undeclared_is_discovered_orphan():
    result = match_by_natural_key([], [snap("ghost", provider_id="uid9")])
    assert not result.pairs
    assert [o.name for o in result.discovered_orphans] == ["ghost"]


def test_move_of_scope_breaks_natural_key_into_two_orphans():
    # same name, different scope -> not a match (documented limitation, correct behavior)
    result = match_by_natural_key(
        [snap("web", scope="default")], [snap("web", scope="prod", provider_id="uid1")]
    )
    assert not result.pairs
    assert len(result.declared_orphans) == 1
    assert len(result.discovered_orphans) == 1


def test_same_name_two_scopes_yields_two_distinct_matches():
    result = match_by_natural_key(
        [snap("web", scope="default"), snap("web", scope="prod")],
        [
            snap("web", scope="default", provider_id="u1"),
            snap("web", scope="prod", provider_id="u2"),
        ],
    )
    assert len(result.pairs) == 2
    scopes = sorted(p.declared.scope for p in result.pairs)
    assert scopes == ["default", "prod"]


def test_output_is_deterministic_regardless_of_input_order():
    a = match_by_natural_key(
        [snap("a"), snap("b"), snap("c")],
        [snap("c", provider_id="uc"), snap("a", provider_id="ua")],
    )
    b = match_by_natural_key(
        [snap("c"), snap("b"), snap("a")],
        [snap("a", provider_id="ua"), snap("c", provider_id="uc")],
    )
    assert a == b
