from collections.abc import Mapping, Sequence

from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import (
    MatchDecision,
    MatchedPair,
    MatchResult,
    NaturalKey,
    ResourceSnapshot,
)


def match_resources(
    declared: Sequence[ResourceSnapshot],
    discovered: Sequence[ResourceSnapshot],
    decisions: Sequence[MatchDecision] = (),
) -> MatchResult:
    """Pair the two planes, strongest evidence first (DESIGN 12).

    Two strategies ship here, applied in priority order, each claiming the
    resources it pairs so a later strategy cannot re-claim them:

    1. **Stored binding** from a prior confirmed match. Anchored on the declared
       natural key and the provider's own identifier, so it survives the rename
       that breaks strategy 3 -- one field-level discrepancy on `name` instead
       of two false orphans, which is the reason it outranks the natural key.
    2. **Natural key** `(kind, tenant_id, scope, name)`, high confidence until a
       rename, and suppressed for any pairing a human has rejected.

    A rejected pairing is not merely un-proposed, it is *refused*: the two
    resources become orphans on their own sides, because a human said they are
    not the same resource and re-proposing it every run makes the queue
    un-clearable.

    A confirmed decision whose declared key or provider id no longer resolves
    pairs nothing and is silently skipped here. Retiring the stored record is
    the service's job, not the matcher's -- this function reads decisions and
    never writes them.

    Deterministic: every pass iterates sorted keys, so the same inputs in any
    order produce an identical result.

    Two snapshots sharing one natural key, two discovered sharing one provider
    id, or two confirmed decisions claiming one side are all caller bugs rather
    than differences to report, and raise AssertionError.
    """
    declared_by_key = _by_natural_key(declared, "declared")
    discovered_by_key = _by_natural_key(discovered, "discovered")
    discovered_by_provider = _by_provider_id(discovered)

    confirmed = sorted(
        (d for d in decisions if d.is_confirmed), key=lambda d: (d.declared_key, d.provider_id)
    )
    rejected = frozenset(d.pairing for d in decisions if not d.is_confirmed)
    _refuse_ambiguous_decisions(confirmed)

    bound = _bound_pairs(confirmed, declared_by_key, discovered_by_provider)
    remaining = _natural_key_pairs(
        declared_by_key,
        discovered_by_key,
        claimed_declared=frozenset(p.declared.natural_key for p in bound),
        claimed_discovered=frozenset(p.discovered.natural_key for p in bound),
        rejected=rejected,
    )
    pairs = bound + remaining

    matched_declared = {p.declared.natural_key for p in pairs}
    matched_discovered = {p.discovered.natural_key for p in pairs}
    return MatchResult(
        pairs=pairs,
        declared_orphans=_orphans(declared_by_key, matched_declared),
        discovered_orphans=_orphans(discovered_by_key, matched_discovered),
    )


def _by_natural_key(
    snapshots: Sequence[ResourceSnapshot], plane: str
) -> dict[NaturalKey, ResourceSnapshot]:
    """Index one plane, refusing two snapshots that claim one identity.

    Keeping whichever arrived last would drop the other silently and make the
    result depend on input order, breaking determinism. DESIGN 12 rejects this
    at intent validation; the kernel refuses to paper over it.
    """
    by_key = {snap.natural_key: snap for snap in snapshots}
    assert len(by_key) == len(snapshots), f"duplicate natural key in {plane} input"
    return by_key


def _by_provider_id(snapshots: Sequence[ResourceSnapshot]) -> dict[str, ResourceSnapshot]:
    """Index the discovered plane by the provider's identifier.

    Resources without one are simply absent from the index: they can still be
    matched by natural key, but no durable binding can anchor to them, and the
    database refuses to confirm a match that has nothing to anchor to.
    """
    by_provider: dict[str, ResourceSnapshot] = {}
    identified = 0
    for snap in snapshots:
        provider_id = snap.provider_id
        if provider_id is None:
            continue
        identified += 1
        by_provider[provider_id] = snap
    assert len(by_provider) == identified, "duplicate provider id in discovered input"
    return by_provider


def _refuse_ambiguous_decisions(confirmed: Sequence[MatchDecision]) -> None:
    """Matching is one-to-one, so no confirmed decision may share either side.

    Postgres holds the same rule with partial unique indexes over the active
    states. This assertion is the interior restatement of an already impossible
    condition, not a validation of external data.
    """
    assert len({d.declared_key for d in confirmed}) == len(
        confirmed
    ), "two confirmed decisions claim one declared resource"
    assert len({d.provider_id for d in confirmed}) == len(
        confirmed
    ), "two confirmed decisions claim one discovered resource"


def _bound_pairs(
    confirmed: Sequence[MatchDecision],
    declared_by_key: Mapping[NaturalKey, ResourceSnapshot],
    discovered_by_provider: Mapping[str, ResourceSnapshot],
) -> tuple[MatchedPair, ...]:
    pairs = []
    for decision in confirmed:
        declared = declared_by_key.get(decision.declared_key)
        discovered = discovered_by_provider.get(decision.provider_id)
        if declared is None or discovered is None:
            continue
        pairs.append(
            MatchedPair(
                declared=declared,
                discovered=discovered,
                strategy=MatchStrategy.BINDING.value,
                confidence=Confidence.HIGH.value,
            )
        )
    return tuple(pairs)


def _natural_key_pairs(
    declared_by_key: Mapping[NaturalKey, ResourceSnapshot],
    discovered_by_key: Mapping[NaturalKey, ResourceSnapshot],
    *,
    claimed_declared: frozenset[NaturalKey],
    claimed_discovered: frozenset[NaturalKey],
    rejected: frozenset[tuple[NaturalKey, str]],
) -> tuple[MatchedPair, ...]:
    pairs = []
    for key in sorted(declared_by_key.keys() & discovered_by_key.keys()):
        if key in claimed_declared or key in claimed_discovered:
            continue
        discovered = discovered_by_key[key]
        if (key, discovered.provider_id) in rejected:
            continue
        pairs.append(
            MatchedPair(
                declared=declared_by_key[key],
                discovered=discovered,
                strategy=MatchStrategy.NATURAL_KEY.value,
                confidence=Confidence.HIGH.value,
            )
        )
    return tuple(pairs)


def _orphans(
    by_key: Mapping[NaturalKey, ResourceSnapshot], matched: set[NaturalKey]
) -> tuple[ResourceSnapshot, ...]:
    return tuple(by_key[key] for key in sorted(by_key.keys() - matched))
