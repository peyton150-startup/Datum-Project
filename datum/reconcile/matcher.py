from collections.abc import Sequence

from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import MatchedPair, MatchResult, ResourceSnapshot


def match_by_natural_key(
    declared: Sequence[ResourceSnapshot],
    discovered: Sequence[ResourceSnapshot],
) -> MatchResult:
    declared_by_key = {snap.natural_key: snap for snap in declared}
    discovered_by_key = {snap.natural_key: snap for snap in discovered}

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
