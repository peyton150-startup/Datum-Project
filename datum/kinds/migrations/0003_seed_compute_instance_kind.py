"""The second kind, and the test of the bet the whole data model rests on.

ADR-001 says a kind is data, not code. DESIGN §24 names the early warning that
would falsify it: *"the second kind requires a migration."* This migration is
the check. It adds a `Kind` row and nothing else -- no column on
`declared_resource`, none on `discovered_resource`, no index, no constraint. If
a second kind had needed any of those, the bet was wrong and half the design
would need revisiting.

**Every attribute is required.** That is what keeps the optionality
simplification (§10) deferred rather than merely postponed: the trigger is a
second kind *exposing optional fields*, and this one does not need to. See
PROJECT_PLAN, WBS 1.5.0, for the condition attached to that choice.

Deliberately absent: `lifecycle_state`. `attribute_schema` is shared by both
planes, so every attribute must be something intent can declare, and no author
declares that an instance is currently RUNNING. Observed-only fields are a
precedence question, not a kind one.
"""

from django.db import migrations

KIND_NAME = "ComputeInstance"

# Provider-neutral names for provider-specific things. OCI calls the shape a
# shape and Datum agrees; OCI reports ocpus inside shape-config and Datum
# flattens it, because a nested attribute would be a second shape of value to
# compare and the diff engine has no reason to grow one yet.
ATTRIBUTE_SCHEMA = {"shape": "str", "ocpus": "int"}


def seed(apps, schema_editor):
    Kind = apps.get_model("kinds", "Kind")
    Kind.objects.get_or_create(name=KIND_NAME, defaults={"attribute_schema": ATTRIBUTE_SCHEMA})


def unseed(apps, schema_editor):
    apps.get_model("kinds", "Kind").objects.filter(name=KIND_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [("kinds", "0002_seed_deployment_kind")]
    operations = [migrations.RunPython(seed, unseed)]
