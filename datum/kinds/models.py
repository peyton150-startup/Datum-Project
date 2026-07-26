from django.db import models


class Kind(models.Model):
    name = models.CharField(max_length=128, unique=True)
    attribute_schema = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
