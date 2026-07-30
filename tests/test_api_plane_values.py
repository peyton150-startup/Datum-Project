"""WBS 1.5.0 specification, the API contract half. Written before implementation.

Separate from `tests/kernel/test_null_versus_absent.py` because these need the
database and that file is pure domain, but the two are one specification and
are reviewed together.

Why this file exists at all is the sharpest thing the review of 1.5.0 found.
The domain type `PlaneValue` protects exactly one call site -- `service.py`,
via `mypy --strict` on `datum.reconcile.*`. It does not reach the API:
`api/router.py::_serialize` reads `declared_value` off the Django *model*, so
adding presence columns changes nothing there and the endpoint keeps emitting
one collapsed `null` for both states with CI green. It does not reach the
frontend either: `web/src/api.ts` is hand-written and casts with
`body.items as Discrepancy[]`, an unchecked assertion.

So the boundary interface guards domain-to-database, and everything downstream
of the database -- which is where a reader actually is, and where the original
defect is *felt* -- is guarded by these tests and nothing else.

Every test here fails until the API carries the new shape.

One of them needs data that does not exist yet, and that is a deliverable
rather than an oversight: `test_declared_absent_and_declared_null_are_
distinguishable_over_the_wire` requires the intent fixture and the recorded
Kubernetes payload to disagree about a field intent omits AND a field intent
sets to null. Today's fixtures produce exactly one discrepancy, on `replicas`,
with both sides present -- so the fixtures cannot currently express the
distinction this package exists to make. Extending them is part of 1.5.0.

Stated here because a test whose fixture cannot produce its precondition fails
for a reason that looks like the feature is missing, and an implementer who
"fixes" it by weakening the assertion has removed the only check on the thing
the package is for.
"""

import pytest
from django.test import Client

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.intent.ingest import ingest_revision
from datum.reconcile.models import Discrepancy
from datum.reconcile.service import run_reconciliation

FIXTURE = "fixtures/k8s/deployments.json"
TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(from_recording(FIXTURE), TENANT)
    run_reconciliation(TENANT)


def declared_side(item):
    return item["declared"]


def test_a_present_value_serializes_with_presence_true(seeded):
    """The ordinary case, pinned so the shape change is total rather than partial."""
    item = Client().get("/api/discrepancies?state=open").json()["items"][0]
    assert item["field_name"] == "replicas"
    assert declared_side(item) == {"present": True, "value": 3}
    assert item["discovered"] == {"present": True, "value": 5}


def test_the_collapsed_keys_are_gone_from_the_payload(seeded):
    """`declared_value` and `discovered_value` are removed, not kept alongside.

    The cheap way through a red suite is to add the nested shape and leave the
    flat keys in place, which satisfies every other test in this file and
    leaves `ReviewQueue.tsx` rendering the collapsed value forever.
    """
    item = Client().get("/api/discrepancies?state=open").json()["items"][0]
    assert "declared_value" not in item
    assert "discovered_value" not in item


def test_declared_absent_and_declared_null_are_distinguishable_over_the_wire(seeded):
    """The one test that catches the defect where a reader sees it.

    Two discrepancies on one resource: a field intent never mentions, and a
    field intent explicitly sets to null. Before 1.5.0 both serialize as
    `"declared_value": null` and the review queue renders both as the string
    "null" -- the exact confusion DESIGN section 13 opens by naming.
    """
    absent_field = Discrepancy.objects.get(tenant_id=TENANT, field_name="image")
    null_field = Discrepancy.objects.get(tenant_id=TENANT, field_name="strategy")

    items = {
        i["field_name"]: i for i in Client().get("/api/discrepancies?state=open").json()["items"]
    }

    assert items[absent_field.field_name]["declared"] == {"present": False, "value": None}
    assert items[null_field.field_name]["declared"] == {"present": True, "value": None}
    assert items[absent_field.field_name]["declared"] != items[null_field.field_name]["declared"]


def test_a_pre_migration_row_reports_undetermined_presence(seeded):
    """Legacy rows keep their values and admit they do not know.

    The migration deletes open rows only -- they are rebuilt next run -- and
    leaves terminal rows with NULL presence, meaning "recorded before 1.5.0,
    never determined". Backfilling `present: true` was refused because it
    asserts "intent stated null" about records where nothing determined that.

    This is the third state reaching TypeScript as `present: boolean | null`.
    It is temporary in fact, since legacy rows age out under the section 23.4
    retention policy, and permanent in the type.
    """
    legacy = Discrepancy.objects.filter(tenant_id=TENANT).first()
    legacy.state = "resolved"
    legacy.declared_present = None
    legacy.discovered_present = None
    legacy.save()

    item = Client().get("/api/discrepancies?state=resolved").json()["items"][0]
    assert item["declared"]["present"] is None
    assert item["discovered"]["present"] is None
