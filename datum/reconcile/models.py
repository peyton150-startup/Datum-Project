from django.db import models

from datum.discovery.models import DiscoveredResource
from datum.enums import (
    Confidence,
    DiscrepancyState,
    DiscrepancyType,
    MatchState,
    MatchStrategy,
    Plane,
)
from datum.graph.models import DeclaredResource


class Match(models.Model):
    tenant_id = models.UUIDField()
    declared_resource = models.OneToOneField(DeclaredResource, on_delete=models.CASCADE)
    discovered_resource = models.OneToOneField(DiscoveredResource, on_delete=models.CASCADE)
    strategy = models.CharField(max_length=16, choices=MatchStrategy.choices)
    confidence = models.CharField(max_length=8, choices=Confidence.choices)
    state = models.CharField(max_length=12, choices=MatchState.choices, default=MatchState.PROPOSED)
    confirmed_by = models.CharField(max_length=128, null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


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
