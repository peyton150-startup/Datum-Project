"""WBS 1.5.0: presence beside value, and the honest handling of rows that predate it.

Hand-written rather than generated, because the data step is the part that
needed a decision and `makemigrations` does not have one. (It was in fact
generated once, mid-implementation, and silently overwrote this file with a
version containing only the schema operations -- the same failure class this
project recorded at Phase 3 close: a file that looks right and does not do
what it claims. Restored by hand, and worth the note.)

**Open rows are deleted. Terminal rows are kept.** An earlier draft of this
package said "delete existing rows, nothing durable depends on them", which was
false: `_reset` deletes only `state=OPEN`, and `resolve_discrepancy` writes
`RESOLVED` when a human clears an item in the review queue. Resolved rows are
the entire durable human-authored state in the system, so a blanket delete
would have erased every record of who reviewed what, silently, with the drift
reappearing as fresh open rows on the next beat.

Open rows are safe to delete for the reason that argument was reaching for:
`run_reconciliation` rebuilds them from both planes on the next run, so
deleting them invents nothing and backfills nothing.

Terminal rows keep their values and get NULL presence, meaning "recorded before
1.5.0, never determined". Backfilling `present=True` was refused because it
asserts "intent stated null" about records where nothing determined that --
and doing it only to resolved rows means doing it where nobody will look.
"""

from typing import Any

from django.db import migrations, models


def delete_open_discrepancies(apps: Any, schema_editor: Any) -> None:
    """Open rows only. They are rebuilt by the next reconciliation run.

    Deliberately not a delete over everything: the WHERE clause is the entire
    safety property of this migration.
    """
    Discrepancy = apps.get_model("reconcile", "Discrepancy")
    Discrepancy.objects.filter(state="open").delete()


def restore_open_discrepancies(apps: Any, schema_editor: Any) -> None:
    """Nothing to restore, and saying so is the honest reverse.

    The deleted rows were derived state; reversing this migration leaves the
    next reconciliation run to rebuild them. No data is recoverable here
    because none was retained, which is why the forward step is limited to
    rows that can be regenerated.
    """


class Migration(migrations.Migration):
    dependencies = [("reconcile", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="discrepancy",
            name="declared_present",
            field=models.BooleanField(null=True),
        ),
        migrations.AddField(
            model_name="discrepancy",
            name="discovered_present",
            field=models.BooleanField(null=True),
        ),
        migrations.RunPython(delete_open_discrepancies, restore_open_discrepancies),
        migrations.AddConstraint(
            model_name="discrepancy",
            constraint=models.CheckConstraint(
                check=models.Q(("declared_present", True))
                | models.Q(("declared_present__isnull", True))
                | models.Q(("declared_value__isnull", True)),
                name="ck_declared_absent_implies_null_value",
            ),
        ),
        migrations.AddConstraint(
            model_name="discrepancy",
            constraint=models.CheckConstraint(
                check=models.Q(("discovered_present", True))
                | models.Q(("discovered_present__isnull", True))
                | models.Q(("discovered_value__isnull", True)),
                name="ck_discovered_absent_implies_null_value",
            ),
        ),
    ]
