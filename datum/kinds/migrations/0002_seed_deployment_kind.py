from django.db import migrations


def seed(apps, schema_editor):
    Kind = apps.get_model("kinds", "Kind")
    Kind.objects.get_or_create(
        name="Deployment", defaults={"attribute_schema": {"replicas": "int"}}
    )


def unseed(apps, schema_editor):
    apps.get_model("kinds", "Kind").objects.filter(name="Deployment").delete()


class Migration(migrations.Migration):
    dependencies = [("kinds", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
