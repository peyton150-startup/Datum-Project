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
