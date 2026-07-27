from collections.abc import Sequence

from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import MatchedPair, MatchResult, ResourceSnapshot


def match_by_natural_key(
    declared: Sequence[ResourceSnapshot],
    discovered: Sequence[ResourceSnapshot],
) -> MatchResult:
    """Pair declared against discovered resources on identical natural keys.

    The natural key is `(kind, tenant_id, scope, name)`. A key present on both
    planes yields exactly one pair; anything unmatched becomes an orphan on its
    own side. Every pair is `NATURAL_KEY` strategy at `HIGH` confidence, because
    an exact key match admits no doubt — other strategies arrive in Phase 4.

    Deterministic: pairs and both orphan lists are ordered by natural key, so
    the same inputs in any order produce an identical result.

    Two snapshots sharing one natural key is a caller bug, not a difference to
    report, and raises AssertionError rather than silently keeping one of them.
    """
    declared_by_key = {snap.natural_key: snap for snap in declared}
    discovered_by_key = {snap.natural_key: snap for snap in discovered}

    # Two snapshots claiming one identity is a loader bug, not a difference to
    # report. Keeping whichever arrived last would drop the other silently and
    # make the result depend on input order, breaking determinism. DESIGN 12
    # rejects this at intent validation; the kernel refuses to paper over it.
    assert len(declared_by_key) == len(declared), "duplicate natural key in declared input"
    assert len(discovered_by_key) == len(discovered), "duplicate natural key in discovered input"

    shared = sorted(declared_by_key.keys() & discovered_by_key.keys())
    declared_only = sorted(declared_by_key.keys() - discovered_by_key.keys())
    discovered_only = sorted(discovered_by_key.keys() - declared_by_key.keys())

    pairs = tuple(
        MatchedPair(
            declared=declared_by_key[key],
            discovered=discovered_by_key[key],
            strategy=MatchStrategy.NATURAL_KEY.value,
            confidence=Confidence.HIGH.value,
        )
        for key in shared
    )
    declared_orphans = tuple(declared_by_key[key] for key in declared_only)
    discovered_orphans = tuple(discovered_by_key[key] for key in discovered_only)
    return MatchResult(pairs, declared_orphans, discovered_orphans)
