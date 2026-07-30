from django.db.models import TextChoices


class MatchStrategy(TextChoices):
    NATURAL_KEY = "natural_key"
    BINDING = "binding"
    PROVIDER_TAG = "provider_tag"


class Confidence(TextChoices):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MatchState(TextChoices):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    # System-closed, and deliberately distinct from REJECTED: a human said
    # "these are not the same resource", the system says "the resource this
    # decision was about is gone". Terminal, and never revived -- a re-created
    # resource is re-proposed and re-decided (DESIGN 12, cross-run semantics).
    INVALIDATED = "invalidated"


# A match that still binds two live resources. Terminal states are excluded:
# they are history, and several may exist for one resource over time.
ACTIVE_MATCH_STATES = (MatchState.PROPOSED, MatchState.CONFIRMED)
# States a human authored, which a run must never delete or rebuild.
HUMAN_MATCH_STATES = (MatchState.CONFIRMED, MatchState.REJECTED)


class DiscrepancyType(TextChoices):
    FIELD = "field"
    DECLARED_MISSING = "declared_missing"
    DISCOVERED_UNDECLARED = "discovered_undeclared"


class DiscrepancyState(TextChoices):
    OPEN = "open"
    RESOLVED = "resolved"


class Plane(TextChoices):
    DECLARED = "declared"
    DISCOVERED = "discovered"


class CollectorRunStatus(TextChoices):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
