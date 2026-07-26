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
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class DiscoveredResource(models.Model):
    tenant_id = models.UUIDField()
    kind = models.ForeignKey(Kind, on_delete=models.PROTECT)
    name = models.CharField(max_length=253)
    scope = models.CharField(max_length=253)
    provider_id = models.CharField(max_length=253)
    attributes = models.JSONField(default=dict)
    run = models.ForeignKey(CollectorRun, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "kind", "scope", "name"], name="uq_discovered_natural_key"
            ),
        ]
