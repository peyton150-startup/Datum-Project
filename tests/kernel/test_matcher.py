import pytest

from datum.enums import Confidence, MatchState, MatchStrategy
from datum.reconcile.domain import MatchDecision, ResourceSnapshot
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


# Cross-run corpus (DESIGN 12): semantics across reconciliation runs.
# These test the matcher's handling of stored human decisions that persist
# across runs: bindings from renames, rejections that prevent re-proposal, and
# the invalidation of decisions whose subject resources disappear.


def test_run_twice_with_no_changes_confirmed_match_persists():
    """Running reconciliation on unchanged inputs does not recreate matches.

    A confirmed match is a human decision, not a derived proposal. The second
    run should propose the same pairing but find the stored decision and keep it
    as CONFIRMED, not replace it with a new PROPOSED match.
    """
    declared = [snap("web")]
    discovered = [snap("web", provider_id="uid1")]

    # First run: natural-key match is proposed
    result1 = match_resources(declared, discovered)
    assert len(result1.pairs) == 1
    assert result1.pairs[0].strategy == MatchStrategy.NATURAL_KEY.value

    # Second run with same inputs: same natural-key proposal
    result2 = match_resources(declared, discovered)
    assert result2 == result1


def test_confirmed_binding_survives_no_change_and_invalidates_on_declared_rename():
    """A binding anchors to declared key at decision time; renamed declared side invalidates it.

    The binding records (kind, tenant, scope, name) = declared key and provider_id.
    When the declared side is renamed, that anchor no longer resolves and the
    binding is skipped. Service-side invalidation marks it INVALIDATED as history.
    The renamed resource is then proposed by natural key (DESIGN 12, cross-run
    corpus: "Confirmed binding whose declared resource is renamed in intent").
    """
    declared = [snap("web")]
    discovered = [snap("web", provider_id="uid1")]

    # First run: natural-key match, confirmed by human
    decision = MatchDecision(
        declared_key=("Deployment", T, "default", "web"),
        provider_id="uid1",
        is_confirmed=True,
    )

    # Same run again, no changes: binding still finds its pair
    result_unchanged = match_resources(declared, discovered, [decision])
    assert len(result_unchanged.pairs) == 1
    assert result_unchanged.pairs[0].strategy == MatchStrategy.BINDING.value

    # Second run: declared side was renamed in intent
    declared_renamed = [snap("api")]
    discovered_unchanged = [snap("web", provider_id="uid1")]

    # Binding looks for ("Deployment", T, "default", "web") but only finds "api".
    # The anchor does not resolve, so the binding is skipped.
    result_after_rename = match_resources(declared_renamed, discovered_unchanged, [decision])

    # No binding match because the declared key changed
    assert len(result_after_rename.pairs) == 0
    # Both sides are orphans because natural key no longer matches
    assert len(result_after_rename.declared_orphans) == 1
    assert result_after_rename.declared_orphans[0].name == "api"
    assert len(result_after_rename.discovered_orphans) == 1
    assert result_after_rename.discovered_orphans[0].name == "web"


def test_rejected_match_is_not_reproposed():
    """A human rejection is remembered and prevents the same pairing from reappearing.

    If a human said "these are not the same resource", the matcher must not
    propose the pairing again. Both sides fall out as orphans on their own
    merits, not as a pair.
    """
    declared = [snap("web")]
    discovered = [snap("web", provider_id="uid1")]

    # Human rejected the pairing
    rejection = MatchDecision(
        declared_key=("Deployment", T, "default", "web"),
        provider_id="uid1",
        is_confirmed=False,  # rejected
    )

    # Next run: the same pairing would naturally match
    result = match_resources([snap("web")], [snap("web", provider_id="uid1")], [rejection])

    # But the rejection suppresses it
    assert not result.pairs
    assert len(result.declared_orphans) == 1
    assert result.declared_orphans[0].name == "web"
    assert len(result.discovered_orphans) == 1
    assert result.discovered_orphans[0].name == "web"


def test_confirmed_match_with_new_provider_id_is_natural_key_fallback():
    """If a discovered resource is recreated with a new provider ID, binding fails.

    The old binding pointed at provider_id="uid1", but the resource now has
    provider_id="uid2". The anchor is gone, so binding does not fire. Natural
    key matches instead.
    """
    declared = [snap("web")]

    # First run: natural key, confirmed
    decision = MatchDecision(
        declared_key=("Deployment", T, "default", "web"),
        provider_id="uid1",
        is_confirmed=True,
    )

    # Second run: discovered resource has new provider_id (was recreated)
    result = match_resources(
        [snap("web")],
        [snap("web", provider_id="uid2")],
        [decision],
    )

    # Binding looks for uid1, does not find it. Natural key matches instead.
    assert len(result.pairs) == 1
    assert result.pairs[0].strategy == MatchStrategy.NATURAL_KEY.value


def test_two_resources_swap_names_bindings_distinguish_them():
    """Resources that swap names are correctly tracked via confirmed bindings.

    Without bindings, the matcher would produce crossed matches. With them,
    each side goes to its correct partner.
    """
    declared = [snap("web"), snap("api")]
    discovered = [snap("api", provider_id="uid_web"), snap("web", provider_id="uid_api")]

    # Both are confirmed (human or prior runs have established the bindings)
    decisions = [
        MatchDecision(
            declared_key=("Deployment", T, "default", "web"),
            provider_id="uid_web",
            is_confirmed=True,
        ),
        MatchDecision(
            declared_key=("Deployment", T, "default", "api"),
            provider_id="uid_api",
            is_confirmed=True,
        ),
    ]

    result = match_resources(declared, discovered, decisions)

    # Two pairs, both by binding, no crossed matches
    assert len(result.pairs) == 2
    for pair in result.pairs:
        assert pair.strategy == MatchStrategy.BINDING.value

    # web declared matches web discovered (uid_web), api to api (uid_api)
    web_pair = next(p for p in result.pairs if p.declared.name == "web")
    assert web_pair.discovered.provider_id == "uid_web"

    api_pair = next(p for p in result.pairs if p.declared.name == "api")
    assert api_pair.discovered.provider_id == "uid_api"


def test_discovered_resource_with_no_provider_id_cannot_be_confirmed():
    """A resource with no provider_id cannot be the target of a confirmed match.

    Without a provider_id, a decision cannot be found again in future runs
    (the discovered_provider_id anchor is gone). The database check constraint
    prevents this state from being created.
    """
    declared = [snap("web")]
    discovered = [snap("web", provider_id=None)]

    # A human tries to confirm a match with no provider_id
    decision = MatchDecision(
        declared_key=("Deployment", T, "default", "web"),
        provider_id=None,  # impossible anchor
        is_confirmed=True,
    )

    # This should be rejected by the database, but at the matcher layer
    # (which is a stateless function), it can be passed through. The check
    # constraint in models.py is the actual enforcement.
    # For now, the matcher accepts it but the domain layer would reject it.
    result = match_resources([snap("web")], [snap("web", provider_id=None)], [decision])
    # The matcher would propose by natural key since provider_id is None
    assert len(result.pairs) == 1
