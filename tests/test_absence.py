"""SPECIFICATION for WBS 1.4.4: absence semantics.

Written before the implementation and reviewed as a description of intended
behaviour, not as a check that code works. Nothing behind these passes yet.

The rule these encode, from DESIGN section 11: a resource missing from a run
means either that it was deleted or that this run failed to read it, and the
collector cannot tell which. **Only a SUCCESS run may be used to infer
absence.** Getting this wrong means Datum reporting that production resources
were deleted when they were merely unread, which is the failure section 11
mostly exists to prevent.

## The API these tests assume

- `DiscoveredResource.last_seen_run` -- the run that last *observed* this row.
  Renames the existing `run` field, which already means exactly this; DESIGN
  section 11 names it `last_seen_run` and the code should agree with the
  document.
- `DiscoveredResource.is_absent` (bool, default False) and `absent_since`
  (nullable datetime).
- `datum.discovery.absence.mark_absent_after(run)` -- called at the end of a
  run. Marks rows the run should have seen but did not.

**Do not write `last_seen_run` outside `_upsert`.** A stored flag can fall out
of step with the field it was derived from in a way a computed one cannot, and
the only way that happens here is something else -- a data migration, an admin
fix, a future bulk reassignment -- moving `last_seen_run` without re-running the
absence rule. Raised in review as the real cost of the decision below.

**Why `is_absent` is stored rather than derived.** Absence could be computed as
`last_seen_run != <latest successful run>`, saving a column. That is rejected
for the same reason a fourth run status was rejected: it makes every reader
responsible for restating the rule correctly, and a reader who gets it wrong
reports resources as deleted. Storing it means the rule is applied in exactly
one place. This is the decision most worth arguing with in review.
"""

import pytest

from datum.discovery.absence import mark_absent_after
from datum.discovery.collector import run_collector
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.django_db


def a_run(status, collector_name="kubernetes", tenant_id=TENANT) -> CollectorRun:
    return CollectorRun.objects.create(
        tenant_id=tenant_id, collector_name=collector_name, status=status
    )


def a_resource(name, run, tenant_id=TENANT, scope="default") -> DiscoveredResource:
    return DiscoveredResource.objects.create(
        tenant_id=tenant_id,
        kind=Kind.objects.get(name="Deployment"),
        name=name,
        scope=scope,
        provider_id=f"uid-{name}",
        attributes={"replicas": 1},
        last_seen_run=run,
    )


# ---------------------------------------------------------------------------
# The rule: only SUCCESS infers absence
# ---------------------------------------------------------------------------


def test_a_successful_run_marks_what_it_did_not_see_as_absent():
    """The ordinary case: the resource really was deleted from the estate."""
    old = a_run(CollectorRunStatus.SUCCESS)
    gone = a_resource("gone", old)

    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))

    gone.refresh_from_db()
    assert gone.is_absent is True
    assert gone.absent_since is not None


@pytest.mark.parametrize("status", [CollectorRunStatus.PARTIAL, CollectorRunStatus.FAILED])
def test_a_run_that_is_not_successful_marks_nothing_absent(status):
    """The rule that prevents mass deletion.

    A run with a gap cannot distinguish a gap from a deletion, so it is not
    permitted to guess. This is the single most important test in the file:
    if it fails, one bad afternoon at a cloud provider reads as the deletion of
    an entire estate.
    """
    old = a_run(CollectorRunStatus.SUCCESS)
    unread = a_resource("unread", old)

    mark_absent_after(a_run(status))

    unread.refresh_from_db()
    assert unread.is_absent is False


def test_a_successful_run_carrying_a_gap_marks_nothing_absent():
    """SUCCESS is necessary but a gap still disqualifies.

    A run cannot currently be SUCCESS with a gap -- `_status_for` forces PARTIAL
    -- so this asserts the belt as well as the braces. If a later change ever
    lets the two combine, absence inference must refuse rather than silently
    become wrong, because `has_gap` is the more specific signal.
    """
    old = a_run(CollectorRunStatus.SUCCESS)
    unread = a_resource("unread", old)
    gapped = a_run(CollectorRunStatus.SUCCESS)
    gapped.has_gap = True
    gapped.save(update_fields=["has_gap"])

    mark_absent_after(gapped)

    unread.refresh_from_db()
    assert unread.is_absent is False


def test_a_resource_the_run_did_see_is_not_marked_absent():
    """The other branch, which a rule that marked everything would still pass."""
    current = a_run(CollectorRunStatus.SUCCESS)
    seen = a_resource("seen", current)

    mark_absent_after(current)

    seen.refresh_from_db()
    assert seen.is_absent is False


# ---------------------------------------------------------------------------
# Scope: whose absence is a run entitled to infer?
# ---------------------------------------------------------------------------


def test_a_collector_may_not_mark_another_collectors_resources_absent():
    """The case that would be catastrophic and is easy to write by accident.

    A successful Kubernetes run says nothing whatsoever about Oracle Cloud. A
    naive "mark everything this run did not see" would delete the entire
    estate of every other provider on the first successful run of any one of
    them.
    """
    oci_run = a_run(CollectorRunStatus.SUCCESS, collector_name="oci")
    oci_resource = a_resource("oci-instance", oci_run)

    mark_absent_after(a_run(CollectorRunStatus.SUCCESS, collector_name="kubernetes"))

    oci_resource.refresh_from_db()
    assert oci_resource.is_absent is False


def test_a_collector_may_only_produce_the_one_kind_it_declares():
    """The invariant that makes collector-scoped absence safe.

    Absence is scoped by `(tenant, collector_name)`, which is correct only while
    a collector owns exactly one kind. If a collector owned two and its `fetch`
    silently returned records for only one of them -- a bug in the adapter's own
    dispatch, invisible to the framework -- the run would still report
    `errors=0`, `has_gap=False`, `SUCCESS`, and absence would then be entitled
    to mark every resource of the other kind absent. That is the section 11
    mass-deletion failure relocated one level down, from provider to kind.

    Rather than build multi-kind ownership that nothing yet needs, the
    single-kind assumption is made explicit and loud: a collector declares its
    kind, and producing any other is a bug in the adapter, not bad provider
    data. Whoever builds the first two-kind collector hits this assertion
    instead of the silent hole. The wider question is DESIGN open question 8,
    to be answered when a second kind actually arrives.
    """
    from datum.reconcile.domain import ResourceSnapshot

    class WrongKind:
        name = "kubernetes"
        kind = "Deployment"

        def fetch(self):
            return [{"name": "impostor"}]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind="Service",  # not the kind this collector declares
                tenant_id=tenant_id,
                scope="default",
                name=record["name"],
                provider_id="uid-impostor",
                attributes={"replicas": 1},
            )

    with pytest.raises(AssertionError):
        run_collector(WrongKind(), TENANT)


def test_a_run_may_not_mark_another_tenants_resources_absent():
    """Tenant isolation is not enforced until phase 5, but every query is
    written tenant-scoped from day one, and this is a query."""
    other_run = a_run(CollectorRunStatus.SUCCESS, tenant_id=OTHER_TENANT)
    other_resource = a_resource("theirs", other_run, tenant_id=OTHER_TENANT)

    mark_absent_after(a_run(CollectorRunStatus.SUCCESS, tenant_id=TENANT))

    other_resource.refresh_from_db()
    assert other_resource.is_absent is False


# ---------------------------------------------------------------------------
# Absence is recorded, never destructive
# ---------------------------------------------------------------------------


def test_an_absent_resource_is_marked_not_deleted():
    """Deleting the row would destroy the evidence the review queue exists to
    show. The operator needs to see what went missing, not find nothing."""
    old = a_run(CollectorRunStatus.SUCCESS)
    a_resource("gone", old)

    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))

    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="gone").exists()


def test_a_resource_that_reappears_stops_being_absent():
    """Resources come back: a node drains and returns, a namespace is
    recreated. An absence flag that only ever latches on would report a healthy
    resource as missing forever."""
    old = a_run(CollectorRunStatus.SUCCESS)
    resource = a_resource("flapping", old)
    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))

    run_collector(_collector_returning(["flapping"]), TENANT)

    resource.refresh_from_db()
    assert resource.is_absent is False
    assert resource.absent_since is None


def test_direct_observation_clears_absence_even_in_a_partial_run():
    """Inferring absence needs SUCCESS. *Refuting* it does not.

    The asymmetry is the point, and it is a decision rather than an oversight.
    Absence is inferred from silence, and only a complete read makes silence
    mean anything. But a resource that was actually read is direct evidence
    that it exists, and evidence does not become less true because a different
    record in the same batch was malformed.

    Without this, a resource would stay marked absent while being successfully
    read on every single run, for as long as any one record in the payload kept
    failing -- reported missing by a collector that can see it.
    """
    old = a_run(CollectorRunStatus.SUCCESS)
    resource = a_resource("flapping", old)
    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))
    resource.refresh_from_db()
    assert resource.is_absent is True, "precondition: it starts out absent"

    run = run_collector(_collector_returning(["flapping"], with_bad_record=True), TENANT)

    assert run.status == CollectorRunStatus.PARTIAL
    resource.refresh_from_db()
    assert resource.is_absent is False
    assert resource.absent_since is None


def test_absent_since_records_when_it_went_missing_not_when_it_was_last_checked():
    """A resource absent for three weeks must not look like it vanished this
    morning, or the timestamp is worthless for judging how stale intent is."""
    old = a_run(CollectorRunStatus.SUCCESS)
    resource = a_resource("gone", old)
    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))
    resource.refresh_from_db()
    first_seen_absent = resource.absent_since

    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))

    resource.refresh_from_db()
    assert resource.absent_since == first_seen_absent


# ---------------------------------------------------------------------------
# What absence means downstream
# ---------------------------------------------------------------------------


def test_an_absent_resource_produces_a_declared_missing_discrepancy(intent_repo):
    """The point of recording absence rather than deleting.

    A resource declared in intent and no longer in the estate is exactly the
    "declared, missing" case, and the reviewer must see it. If absent rows
    still fed the matcher, the pair would match and report no difference at all.
    """
    from datum.enums import DiscrepancyType
    from datum.intent.ingest import ingest_revision
    from datum.reconcile.models import Discrepancy
    from datum.reconcile.service import run_reconciliation

    ingest_revision(TENANT, intent_repo())
    old = a_run(CollectorRunStatus.SUCCESS)
    a_resource("web", old)
    mark_absent_after(a_run(CollectorRunStatus.SUCCESS))

    run_reconciliation(TENANT)

    assert Discrepancy.objects.filter(
        tenant_id=TENANT, discrepancy_type=DiscrepancyType.DECLARED_MISSING, name="web"
    ).exists()


# ---------------------------------------------------------------------------
# The collector run wires it together
# ---------------------------------------------------------------------------


def test_a_successful_collector_run_applies_absence_itself():
    """Absence must not be a second thing an operator has to remember to run."""
    old = a_run(CollectorRunStatus.SUCCESS)
    gone = a_resource("gone", old)

    run_collector(_collector_returning(["still-here"]), TENANT)

    gone.refresh_from_db()
    assert gone.is_absent is True


def test_a_partial_collector_run_applies_no_absence():
    """End to end, through the real framework rather than the helper: one bad
    record in a payload must not license any absence inference at all."""
    old = a_run(CollectorRunStatus.SUCCESS)
    unread = a_resource("unread", old)

    run_collector(_collector_returning(["good"], with_bad_record=True), TENANT)

    unread.refresh_from_db()
    assert unread.is_absent is False


def _collector_returning(names, with_bad_record=False):
    """A collector that reports exactly `names`, optionally with one junk record."""
    from datum.discovery.errors import MalformedProviderData
    from datum.reconcile.domain import ResourceSnapshot

    records = [{"name": n} for n in names]
    if with_bad_record:
        records.append({"name": None})

    class Fake:
        name = "kubernetes"

        def fetch(self):
            return records

        def normalize(self, record, tenant_id):
            if record["name"] is None:
                raise MalformedProviderData("no name")
            return ResourceSnapshot(
                kind="Deployment",
                tenant_id=tenant_id,
                scope="default",
                name=record["name"],
                provider_id=f"uid-{record['name']}",
                attributes={"replicas": 1},
            )

    return Fake()
