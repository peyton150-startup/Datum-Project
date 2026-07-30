from django.db import models

from datum.discovery.models import DiscoveredResource
from datum.enums import (
    ACTIVE_MATCH_STATES,
    HUMAN_MATCH_STATES,
    Confidence,
    DiscrepancyState,
    DiscrepancyType,
    MatchState,
    MatchStrategy,
    Plane,
)
from datum.graph.models import DeclaredResource


class Match(models.Model):
    """One pairing of a declared resource with a discovered one (DESIGN 12).

    Anchored on domain identity rather than row identity. Neither plane's rows
    are durable -- `DeclaredResource` is rebuilt per intent revision under
    full-rebuild projection -- so a foreign key would bind a human's decision to
    a commit rather than to a resource, which is the second half of CF-6. The
    foreign keys below are convenience lookups into the current run and carry
    no meaning; the four anchor columns are what a decision is about.
    """

    tenant_id = models.UUIDField()
    # The anchor: durable on both sides. Declared by natural key as of the
    # decision, discovered by the provider's own identifier, which survives the
    # rename that breaks the natural key.
    declared_kind = models.CharField(max_length=128)
    declared_scope = models.CharField(max_length=253)
    declared_name = models.CharField(max_length=253)
    discovered_provider_id = models.CharField(max_length=253, null=True, blank=True)
    # Non-authoritative, and null once the row they point at is gone.
    declared_resource = models.ForeignKey(
        DeclaredResource, on_delete=models.SET_NULL, null=True, blank=True
    )
    discovered_resource = models.ForeignKey(
        DiscoveredResource, on_delete=models.SET_NULL, null=True, blank=True
    )
    strategy = models.CharField(max_length=16, choices=MatchStrategy.choices)
    confidence = models.CharField(max_length=8, choices=Confidence.choices)
    state = models.CharField(max_length=12, choices=MatchState.choices, default=MatchState.PROPOSED)
    confirmed_by = models.CharField(max_length=128, null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Matching is one-to-one over the *active* states only. Terminal
            # rows are history: a resource that is confirmed, invalidated, and
            # confirmed again legitimately has several, and a unique index over
            # every state would refuse the second confirmation.
            models.UniqueConstraint(
                fields=["tenant_id", "declared_kind", "declared_scope", "declared_name"],
                condition=models.Q(state__in=ACTIVE_MATCH_STATES),
                name="uq_active_match_per_declared",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "discovered_provider_id"],
                condition=models.Q(state__in=ACTIVE_MATCH_STATES),
                name="uq_active_match_per_discovered",
            ),
            # A rejection is a standing decision about one pairing, so recording
            # it twice would be a duplicate rather than a second fact. Several
            # distinct rejections for one declared resource remain legal.
            models.UniqueConstraint(
                fields=[
                    "tenant_id",
                    "declared_kind",
                    "declared_scope",
                    "declared_name",
                    "discovered_provider_id",
                ],
                condition=models.Q(state=MatchState.REJECTED),
                name="uq_one_rejection_per_pairing",
            ),
            # A decision with nothing to anchor to could never be found again,
            # so it cannot be made. This is what makes "confirm a resource the
            # provider does not identify" unavailable rather than merely
            # discouraged -- the row is refused, not silently orphaned.
            models.CheckConstraint(
                check=~models.Q(state__in=HUMAN_MATCH_STATES)
                | models.Q(discovered_provider_id__isnull=False),
                name="ck_human_decision_requires_provider_id",
            ),
        ]


class Discrepancy(models.Model):
    tenant_id = models.UUIDField()
    discrepancy_type = models.CharField(max_length=24, choices=DiscrepancyType.choices)
    kind_name = models.CharField(max_length=128)
    scope = models.CharField(max_length=253)
    name = models.CharField(max_length=253)
    field_name = models.CharField(max_length=128, null=True, blank=True)
    # Presence is a separate fact from value, because `null` is already taken
    # by the JSON value. NULL here means "recorded before WBS 1.5.0, never
    # determined" -- a closed set of legacy rows, not a state new writes use.
    declared_present = models.BooleanField(null=True)
    declared_value = models.JSONField(null=True, blank=True)
    discovered_present = models.BooleanField(null=True)
    discovered_value = models.JSONField(null=True, blank=True)
    authoritative_plane = models.CharField(
        max_length=12, choices=Plane.choices, default=Plane.DECLARED
    )
    state = models.CharField(
        max_length=12, choices=DiscrepancyState.choices, default=DiscrepancyState.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # An absent plane states nothing, so it cannot carry a value. The
            # domain enforces the same rule at construction (PlaneValue), which
            # is what keeps this from surfacing as an IntegrityError escaping
            # run_reconciliation's transaction.
            models.CheckConstraint(
                check=models.Q(declared_present=True)
                | models.Q(declared_present__isnull=True)
                | models.Q(declared_value__isnull=True),
                name="ck_declared_absent_implies_null_value",
            ),
            models.CheckConstraint(
                check=models.Q(discovered_present=True)
                | models.Q(discovered_present__isnull=True)
                | models.Q(discovered_value__isnull=True),
                name="ck_discovered_absent_implies_null_value",
            ),
        ]
