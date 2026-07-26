from django.db import models


class IntentRevision(models.Model):
    tenant_id = models.UUIDField()
    commit_sha = models.CharField(max_length=64)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "commit_sha"], name="uq_revision_tenant_commit"
            ),
            models.UniqueConstraint(
                fields=["tenant_id"],
                condition=models.Q(is_active=True),
                name="uq_one_active_revision_per_tenant",
            ),
        ]
