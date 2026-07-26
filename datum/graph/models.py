from django.db import models

from datum.intent.models import IntentRevision
from datum.kinds.models import Kind


class DeclaredResource(models.Model):
    tenant_id = models.UUIDField()
    kind = models.ForeignKey(Kind, on_delete=models.PROTECT)
    name = models.CharField(max_length=253)
    scope = models.CharField(max_length=253)
    provider_id = models.CharField(max_length=253, null=True, blank=True)
    attributes = models.JSONField(default=dict)
    revision = models.ForeignKey(IntentRevision, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "kind", "scope", "name", "revision"],
                name="uq_declared_natural_key_per_revision",
            ),
        ]
