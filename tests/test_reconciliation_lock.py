"""Exclusion between reconciliation runs: DESIGN section 11, fourth instance.

The race these close was reproduced before being fixed: two threads held inside
`run_reconciliation` past `_reset` and before the writes, one committing and the
other taking

  IntegrityError: duplicate key value violates unique constraint
  "uq_active_match_per_declared"

`_reset` deletes PROPOSED rows, which takes row locks -- but an *empty* DELETE
takes none, so two runs over an estate with no standing proposals both compute
the same pairings and both insert.

`transaction=True` is load-bearing wherever it appears below. The default
django_db wraps a test in a transaction that never commits, so rows written by
the main connection are invisible to the other connections these tests open.
"""

import threading

import pytest
from django.db import connection, connections, transaction

from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind
from datum.locks import run_lock
from datum.reconcile import service
from datum.reconcile.models import Match
from datum.reconcile.service import RECONCILE_OPERATION, run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


def _seed(tenant_id: str = TENANT) -> None:
    """One declared and one discovered resource that match, for one tenant.

    `get_or_create` on the kind because a `transaction=True` test truncates
    tables without restoring what the migrations seeded.
    """
    kind, _ = Kind.objects.get_or_create(
        name="Deployment", defaults={"attribute_schema": {"replicas": "int"}}
    )
    revision = IntentRevision.objects.create(
        tenant_id=tenant_id, commit_sha=tenant_id.replace("-", "").ljust(40, "0")[:40]
    )
    IntentRevision.objects.filter(pk=revision.pk).update(is_active=True)
    DeclaredResource.objects.create(
        tenant_id=tenant_id,
        kind=kind,
        name="web",
        scope="default",
        attributes={"replicas": 3},
        revision=revision,
    )
    run = CollectorRun.objects.create(
        tenant_id=tenant_id, collector_name="kubernetes", status=CollectorRunStatus.SUCCESS
    )
    DiscoveredResource.objects.create(
        tenant_id=tenant_id,
        kind=kind,
        name="web",
        scope="default",
        provider_id=f"uid-{tenant_id[-4:]}",
        attributes={"replicas": 5},
        last_seen_run=run,
    )


def _advisory_locks_on_this_session() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        )
        return int(cursor.fetchone()[0])


@pytest.fixture
def a_run_holding_the_lock():
    """A reconciliation lock held on another connection for the test's duration.

    Another connection rather than this one: advisory locks are held per session,
    so a same-session check would re-enter a lock it already owns and prove
    nothing.
    """
    released = threading.Event()
    holding = threading.Event()

    def hold():
        try:
            with run_lock(TENANT, RECONCILE_OPERATION) as acquired:
                assert acquired, "the fixture must actually hold the lock"
                holding.set()
                released.wait(timeout=30)
        finally:
            connections.close_all()

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait(timeout=10)

    yield

    released.set()
    holder.join(timeout=10)


# ---------------------------------------------------------------------------
# Exclusion
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_second_run_is_skipped_while_one_holds_the_lock(a_run_holding_the_lock):
    _seed()

    assert run_reconciliation(TENANT) is False


@pytest.mark.django_db(transaction=True)
def test_a_skipped_run_writes_nothing(a_run_holding_the_lock):
    """Not "writes no matches": a skip must leave no partial state anywhere."""
    _seed()

    run_reconciliation(TENANT)

    assert not Match.objects.filter(tenant_id=TENANT).exists()
    from datum.reconcile.models import Discrepancy

    assert not Discrepancy.objects.filter(tenant_id=TENANT).exists()


@pytest.mark.django_db(transaction=True)
def test_a_completed_run_reports_that_it_ran(a_run_holding_the_lock):
    """The other side of the boolean, so False is a signal rather than the only
    value ever returned. A caller must be able to tell a skip from a run."""
    _seed(OTHER_TENANT)

    assert run_reconciliation(OTHER_TENANT) is True


@pytest.mark.django_db(transaction=True)
def test_a_different_tenant_is_not_blocked(a_run_holding_the_lock):
    """The lock is per tenant. One tenant's long run must not stop every other
    tenant's reconciliation, which a global lock would."""
    _seed(OTHER_TENANT)

    assert run_reconciliation(OTHER_TENANT) is True
    assert Match.objects.filter(tenant_id=OTHER_TENANT).count() == 1


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_free_after_a_run_finishes(second_connection):
    _seed()

    run_reconciliation(TENANT)

    assert second_connection(run_lock, TENANT, RECONCILE_OPERATION) is True


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_free_after_a_run_raises(second_connection):
    """A crashed run must not wedge the tenant until someone restarts a worker."""
    _seed()

    def explode(tenant_id: str) -> None:
        raise RuntimeError("reconciliation failed")

    original = service._reconcile_once
    service._reconcile_once = explode
    try:
        with pytest.raises(RuntimeError):
            run_reconciliation(TENANT)
    finally:
        service._reconcile_once = original

    assert second_connection(run_lock, TENANT, RECONCILE_OPERATION) is True


@pytest.mark.django_db(transaction=True)
def test_the_lock_is_still_held_when_the_transaction_commits():
    """The test this file exists for.

    Every other test here passes against the broken ordering, because a lock
    nested *inside* the transaction is still held for the whole body. What
    distinguishes the two is the moment of commit:

        correct:  acquire -> BEGIN -> work -> COMMIT -> release
        broken:   BEGIN -> acquire -> work -> release -> COMMIT

    In the broken form the lock is gone before the commit lands, so a second
    worker can acquire it and compute from the previously committed state while
    the first transaction is still open. No dirty read is involved -- the second
    run simply works from a snapshot the first is about to invalidate.

    `on_commit` fires after the commit and before `locked_run` releases, so it
    observes exactly the window that tells the two apart.
    """
    _seed()
    observed: dict[str, int] = {}
    original = service._reconcile_once

    def record_lock_state_at_commit(tenant_id: str) -> None:
        original(tenant_id)
        transaction.on_commit(
            lambda: observed.__setitem__("locks", _advisory_locks_on_this_session())
        )

    service._reconcile_once = record_lock_state_at_commit
    try:
        assert run_reconciliation(TENANT) is True
    finally:
        service._reconcile_once = original

    assert observed["locks"] == 1, (
        "the advisory lock was already released when the transaction committed, "
        "which means it is nested inside the transaction instead of around it"
    )


# ---------------------------------------------------------------------------
# The race itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_runs_no_longer_collide():
    """The reproduction that demonstrated the defect, kept as a regression test.

    Before the lock this produced one commit and one IntegrityError on
    uq_active_match_per_declared. The defect is only closed if the thing that
    showed it now passes.
    """
    _seed()
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            ran = run_reconciliation(TENANT)
            with lock:
                outcomes.append("ran" if ran else "skipped")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcomes.append(f"{type(exc).__name__}")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["ran", "skipped"]
    assert Match.objects.filter(tenant_id=TENANT).count() == 1


@pytest.mark.django_db(transaction=True)
def test_ten_concurrent_attempts_admit_exactly_one():
    """Scale the two-thread case up, to catch an exclusion that holds for one
    contender and leaks under real contention."""
    _seed()
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(10, timeout=30)

    def attempt() -> None:
        try:
            start.wait()
            ran = run_reconciliation(TENANT)
            with lock:
                outcomes.append("ran" if ran else "skipped")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcomes.append(f"{type(exc).__name__}")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("ran") == 1, outcomes
    assert outcomes.count("skipped") == 9, outcomes
    assert Match.objects.filter(tenant_id=TENANT).count() == 1
