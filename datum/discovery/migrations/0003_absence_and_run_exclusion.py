"""WBS 1.4.4: absence semantics and run exclusion.

Written by hand rather than generated, for one reason: `run` becoming
`last_seen_run` is a **rename**, and autodetection cannot know that. Answering
its prompt wrongly -- or running it non-interactively, where it does not ask --
produces a drop and an add, which silently discards every discovered resource's
link to the run that observed it. That is exactly the history absence semantics
exists to preserve.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("discovery", "0002_collectorrun_has_gap")]

    operations = [
        migrations.RenameField(
            model_name="discoveredresource",
            old_name="run",
            new_name="last_seen_run",
        ),
        migrations.AddField(
            model_name="discoveredresource",
            name="is_absent",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="discoveredresource",
            name="absent_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="collectorrun",
            name="skipped_attempts",
            field=models.IntegerField(default=0),
        ),
    ]
