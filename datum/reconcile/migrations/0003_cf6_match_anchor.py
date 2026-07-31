"""CF-6: Match anchored on domain identity, not row identity (DESIGN 12).

A match that survives a run and an intent commit needs to anchor on durable
facts, not on row IDs. DeclaredResource rows are rebuilt per revision under
full-rebuild projection, so a foreign key binds a decision to a commit rather
than to a resource. The four anchor columns hold the decided-upon identity:
(kind, scope, name) from the declared side as of the decision, and the
provider's own identifier from the discovered side.

The foreign keys become convenience lookups, nulled when their target row is
gone. They carry no meaning: neither plane's rows are durable, and a foreign
key to a DeclaredResource in one revision does not find the same resource in
another, silently breaking stored bindings (second half of CF-6).

Also adds INVALIDATED state for cross-run semantics: when either side of a
stored decision vanishes, the record moves to INVALIDATED rather than being
deleted. Terminal and never revived -- a re-created resource is re-proposed
and re-decided (DESIGN 12, cross-run corpus).
"""

from typing import Any

import django.db.models.deletion
from django.db import migrations, models


def backfill_anchor_from_fks(apps: Any, schema_editor: Any) -> None:
    """Populate the four anchor columns from the foreign key rows.

    This runs after the anchor fields are added but before they become the
    source of truth. Matches without a foreign key row (impossible in current
    code but defensive) are left NULL, and the constraint will fail on save.
    That is correct: a match that has no resource to anchor to should not exist.
    """
    Match = apps.get_model("reconcile", "Match")
    for match in Match.objects.select_related("declared_resource", "discovered_resource"):
        if match.declared_resource:
            match.declared_kind = match.declared_resource.kind.name
            match.declared_scope = match.declared_resource.scope
            match.declared_name = match.declared_resource.name
        if match.discovered_resource:
            match.discovered_provider_id = match.discovered_resource.provider_id
        match.save(
            update_fields=[
                "declared_kind",
                "declared_scope",
                "declared_name",
                "discovered_provider_id",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("reconcile", "0002_plane_values")]

    operations = [
        # Add the anchor columns: these hold what a decision is actually about.
        # They must come before the foreign key changes so existing rows can be
        # backfilled (via data migration), avoiding NULL values in a NOT NULL field.
        migrations.AddField(
            model_name="match",
            name="declared_kind",
            field=models.CharField(default="", max_length=128),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="match",
            name="declared_scope",
            field=models.CharField(default="", max_length=253),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="match",
            name="declared_name",
            field=models.CharField(default="", max_length=253),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="match",
            name="discovered_provider_id",
            field=models.CharField(blank=True, max_length=253, null=True),
        ),
        # Backfill the anchor columns from the foreign key rows before the
        # foreign keys become optional. This must run before the ALTER operations.
        migrations.RunPython(backfill_anchor_from_fks, migrations.RunPython.noop),
        # Make the foreign keys optional, since they are convenience lookups only.
        # Once a row they point to is gone, the field becomes NULL but the decision
        # stays anchored on the four columns above.
        migrations.AlterField(
            model_name="match",
            name="declared_resource",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="graph.declaredresource",
            ),
        ),
        migrations.AlterField(
            model_name="match",
            name="discovered_resource",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="discovery.discoveredresource",
            ),
        ),
        # Add INVALIDATED to the state choices. One-to-OneField becomes ForeignKey
        # so a discovered resource can be matched by multiple proposals (before
        # confirmation), and confirmed matches can coexist with invalidated history.
        migrations.AlterField(
            model_name="match",
            name="discovered_resource",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="discovery.discoveredresource",
            ),
        ),
        migrations.AlterField(
            model_name="match",
            name="state",
            field=models.CharField(
                choices=[
                    ("proposed", "Proposed"),
                    ("confirmed", "Confirmed"),
                    ("rejected", "Rejected"),
                    ("invalidated", "Invalidated"),
                ],
                default="proposed",
                max_length=12,
            ),
        ),
        # Add invalidated_at to record when a decision became terminal.
        migrations.AddField(
            model_name="match",
            name="invalidated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Add the one-to-one constraints on active states only. Terminal states
        # are history and several may exist for one resource over time.
        migrations.AddConstraint(
            model_name="match",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "declared_kind", "declared_scope", "declared_name"],
                condition=models.Q(state__in=["proposed", "confirmed"]),
                name="uq_active_match_per_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="match",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "discovered_provider_id"],
                condition=models.Q(state__in=["proposed", "confirmed"]),
                name="uq_active_match_per_discovered",
            ),
        ),
        # A rejection is a standing decision about one pairing: one per pairing.
        migrations.AddConstraint(
            model_name="match",
            constraint=models.UniqueConstraint(
                fields=[
                    "tenant_id",
                    "declared_kind",
                    "declared_scope",
                    "declared_name",
                    "discovered_provider_id",
                ],
                condition=models.Q(state="rejected"),
                name="uq_one_rejection_per_pairing",
            ),
        ),
        # A human decision must have a provider_id to anchor to. Without one,
        # the decision cannot be found again in a future run (DESIGN 12).
        migrations.AddConstraint(
            model_name="match",
            constraint=models.CheckConstraint(
                check=~models.Q(state__in=["confirmed", "rejected"])
                | models.Q(discovered_provider_id__isnull=False),
                name="ck_human_decision_requires_provider_id",
            ),
        ),
    ]
