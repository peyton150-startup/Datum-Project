"""Explicit policy for the pre-integration diff and presence regression corpus."""

from datum.reconcile.diff import reconcile as configured_reconcile
from datum.reconcile.schema import ComparisonSchema


def reconcile(matches):
    fields = {
        name: {"type": "object", "comparison": {"mode": "opaque"}}
        for name in ("replicas", "paused", "image", "a", "b", "c", "f")
    }
    fields.update(
        {
            "replicas": {"type": "numeric", "comparison": {"mode": "exact_value"}},
            "paused": {"type": "boolean", "comparison": {"mode": "exact"}},
            "image": {"type": "string", "comparison": {"mode": "exact"}},
        }
    )
    return configured_reconcile(matches, {"Deployment": ComparisonSchema("Deployment", fields)})
