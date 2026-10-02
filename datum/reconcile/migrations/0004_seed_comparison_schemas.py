"""Separate operator comparison policy from declared attribute types."""

import django.db.models.deletion
from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    Kind = apps.get_model("kinds", "Kind")
    Policy = apps.get_model("reconcile", "ComparisonPolicy")
    policies = {
        "Deployment": {
            "replicas": {"comparison": {"mode": "exact_value"}, "logging": "discrepancy"}
        },
        "ComputeInstance": {
            "shape": {"comparison": {"mode": "exact"}, "logging": "discrepancy"},
            "ocpus": {"comparison": {"mode": "exact_value"}, "logging": "discrepancy"},
        },
    }
    for name, fields in policies.items():
        Policy.objects.get_or_create(kind=Kind.objects.get(name=name), defaults={"fields": fields})


class Migration(migrations.Migration):
    dependencies = [
        ("reconcile", "0003_cf6_match_anchor"),
        ("kinds", "0003_seed_compute_instance_kind"),
    ]
    operations = [
        migrations.CreateModel(
            name="ComparisonPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("fields", models.JSONField(default=dict)),
                (
                    "kind",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to="kinds.kind"
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="discrepancy",
            name="discrepancy_type",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("field", "Field"),
                    ("missing_comparison_policy", "Missing Comparison Policy"),
                    ("declared_missing", "Declared Missing"),
                    ("discovered_undeclared", "Discovered Undeclared"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="discrepancy",
            name="authoritative_plane",
            field=models.CharField(
                max_length=12,
                choices=[("declared", "Declared"), ("discovered", "Discovered")],
                default="declared",
                null=True,
            ),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
