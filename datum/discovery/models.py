from django.db import models

from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind


class CollectorRun(models.Model):
    tenant_id = models.UUIDField()
    collector_name = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=CollectorRunStatus.choices)
    resources_read = models.IntegerField(default=0)
    resources_written = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    # Some of the estate was never read -- a page that failed after other pages
    # had succeeded. Separate from `errors` because a rejection is a record that
    # was seen and refused, while a gap is an unknown number of resources never
    # seen at all: there is no honest number to add to a count. Kept distinct
    # from `status` because it answers a different question. PARTIAL says the
    # run is imperfect; this says which way, and 1.4.4's absence rules care.
    has_gap = models.BooleanField(default=False)
    # Ticks that found this run already in flight and stood down. Counted here
    # rather than recorded as runs of their own, because every run status means
    # a read was attempted and a skip attempted nothing (DESIGN open question 7).
    # A value of 11 says this read blocked eleven ticks, which is the
    # permanently-overlapping schedule section 11 wants visible.
    skipped_attempts = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class DiscoveredResource(models.Model):
    tenant_id = models.UUIDField()
    kind = models.ForeignKey(Kind, on_delete=models.PROTECT)
    name = models.CharField(max_length=253)
    scope = models.CharField(max_length=253)
    provider_id = models.CharField(max_length=253)
    attributes = models.JSONField(default=dict)
    # The run that last *observed* this resource. Renamed from `run`, which read
    # as "the run that created this row" and was never what the field meant.
    #
    # Nothing outside the collector's upsert may write this. Absence is stored
    # rather than derived from it, so a writer that moves this field without
    # re-running the absence rule leaves the two disagreeing -- the one way the
    # stored flag can go stale that a computed one could not.
    last_seen_run = models.ForeignKey(CollectorRun, on_delete=models.CASCADE)
    # Observed previously, not observed by a later complete read. Flagged rather
    # than deleted: the row is the evidence the review queue exists to show.
    is_absent = models.BooleanField(default=False)
    # When it went missing, not when it was last confirmed missing.
    absent_since = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "kind", "scope", "name"], name="uq_discovered_natural_key"
            ),
        ]
