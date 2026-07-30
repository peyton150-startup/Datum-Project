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

The spec anticipated extending the fixtures so that ingestion could produce a
declared-absent field, and implementation showed that cannot be done here.
Section 10's all-attributes-required limit means a declared document cannot
omit an attribute in its kind's schema, so **declared-absent is unreachable
through intent ingestion today** -- which section 13's "which layer the
guarantee is made at" paragraph already says in as many words. Extending the
fixtures would have meant relaxing that limit, which is a different package's
decision and explicitly deferred.

So the wire-contract test builds its rows directly. That is the honest level
for it: this is an API serialization contract, not an ingestion test. When
section 10's limit lifts, the end-to-end version becomes writable and belongs
with that work.
"""

import pytest
from django.db.utils import IntegrityError
from django.test import Client

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import from_recording
from datum.enums import DiscrepancyType, Plane
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


def a_discrepancy(field_name, declared_present, declared_value):
    return Discrepancy.objects.create(
        tenant_id=TENANT,
        discrepancy_type=DiscrepancyType.FIELD,
        kind_name="Deployment",
        scope="default",
        name="web",
        field_name=field_name,
        declared_present=declared_present,
        declared_value=declared_value,
        discovered_present=True,
        discovered_value="nginx",
        authoritative_plane=Plane.DECLARED,
    )


def test_declared_absent_and_declared_null_are_distinguishable_over_the_wire():
    """The one test that catches the defect where a reader sees it.

    Two discrepancies: a field the declared plane never states, and a field it
    states as null. Before 1.5.0 both serialized as `"declared_value": null`
    and the review queue rendered both as the string "null" -- the exact
    confusion DESIGN section 13 opens by naming.

    Rows are built directly rather than driven through intent ingestion, and
    that is not a shortcut. Section 10's all-attributes-required limit means a
    declared document CANNOT currently omit an attribute in its kind's schema,
    so declared-absent is unreachable through ingestion today -- which section
    13's "which layer the guarantee is made at" paragraph states outright.
    This is an API serialization contract, so it is asserted at the layer it
    describes. When section 10's limit lifts, the end-to-end version becomes
    writable and belongs with it.
    """
    absent = a_discrepancy("image", declared_present=False, declared_value=None)
    null = a_discrepancy("strategy", declared_present=True, declared_value=None)

    items = {
        i["field_name"]: i for i in Client().get("/api/discrepancies?state=open").json()["items"]
    }

    assert items[absent.field_name]["declared"] == {"present": False, "value": None}
    assert items[null.field_name]["declared"] == {"present": True, "value": None}
    assert items[absent.field_name]["declared"] != items[null.field_name]["declared"]


def test_the_database_refuses_an_absent_plane_that_carries_a_value():
    """The check constraint, asserted rather than assumed.

    The domain makes this state unconstructible, so this can only be reached
    by writing the ORM directly -- which is exactly what a future migration or
    a bulk fix-up script does.
    """
    with pytest.raises(IntegrityError):
        a_discrepancy("image", declared_present=False, declared_value="nginx")


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
