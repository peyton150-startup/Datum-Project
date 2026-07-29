"""SPECIFICATION for WBS 1.4.4: one run per collector per tenant.

Written before the implementation, reviewed as a description of intended
behaviour. Nothing behind these passes yet.

DESIGN section 11: a second run starting while one is in flight is **skipped,
not queued** -- the later run would read the same estate and the two would race
on the same rows to no benefit. The skip is counted against the run that caused
it (`skipped_attempts`, resolving open question 7) rather than recorded as a
run of its own, because every existing run status means *a read was attempted*.

## The API these tests assume

- `datum.discovery.lock.collector_lock(tenant_id, collector_name)` -- a context
  manager yielding True when the lock was taken and False when another holder
  has it. Backed by a Postgres advisory lock, released by the connection, so a
  worker that dies cannot leave the collector wedged forever.
- `CollectorRun.skipped_attempts` (int, default 0).
- `run_collector` returns `None` when it was skipped. No run row is created,
  because no read was attempted.
"""

import threading

import pytest
from django.db import connection

from datum.discovery.collector import run_collector
from datum.discovery.lock import collector_lock
from datum.discovery.models import CollectorRun
from datum.enums import CollectorRunStatus

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The lock itself
# ---------------------------------------------------------------------------


def test_the_lock_is_granted_when_free():
    with collector_lock(TENANT, "kubernetes") as acquired:
        assert acquired is True


def test_the_same_collector_and_tenant_cannot_hold_it_twice(second_connection):
    with collector_lock(TENANT, "kubernetes") as first:
        assert first is True
        assert second_connection(collector_lock, TENANT, "kubernetes") is False


def test_a_different_collector_is_not_blocked(second_connection):
    """Kubernetes and OCI read different estates and share no rows. Blocking
    one on the other would halve throughput for no safety."""
    with collector_lock(TENANT, "kubernetes"):
        assert second_connection(collector_lock, TENANT, "oci") is True


def test_a_different_tenant_is_not_blocked(second_connection):
    """The lock exists to stop two runs racing on the same rows, and two
    tenants share none."""
    with collector_lock(TENANT, "kubernetes"):
        assert second_connection(collector_lock, OTHER_TENANT, "kubernetes") is True


def test_the_lock_is_released_when_the_block_exits():
    with collector_lock(TENANT, "kubernetes"):
        pass

    with collector_lock(TENANT, "kubernetes") as reacquired:
        assert reacquired is True


def test_the_lock_is_released_even_when_the_body_raises():
    """A run that crashes must not wedge the collector until someone restarts
    the worker. This is the failure mode that makes people distrust locks."""
    with pytest.raises(RuntimeError):
        with collector_lock(TENANT, "kubernetes"):
            raise RuntimeError("the run exploded")

    with collector_lock(TENANT, "kubernetes") as reacquired:
        assert reacquired is True


def test_a_holder_that_never_took_the_lock_does_not_release_it(second_connection):
    """A denied caller exiting its `with` must not release anything.

    Kept deliberately modest about what it proves. Postgres advisory locks are
    session-scoped, so one session *cannot* release another's through
    `pg_advisory_unlock` however carelessly the caller is written -- the
    catastrophic version of this bug is structurally impossible as long as each
    logical caller has its own connection. What this still catches is the
    mundane version: unlocking with the wrong key, or a connection reused
    across callers so that two logical holders are one session.
    """
    with collector_lock(TENANT, "kubernetes"):
        second_connection(collector_lock, TENANT, "kubernetes")  # denied, then exits
        assert second_connection(collector_lock, TENANT, "kubernetes") is False


# ---------------------------------------------------------------------------
# What a skipped run does
# ---------------------------------------------------------------------------


def test_an_overlapping_run_is_skipped_rather_than_run(collector_holding_the_lock):
    assert run_collector(_a_collector(), TENANT) is None


def test_a_skipped_run_creates_no_run_row(collector_holding_the_lock):
    """No read was attempted, so there is nothing for a run row to describe.
    A row with zero counts would be indistinguishable from a genuinely empty
    successful read by anything that did not check the status carefully."""
    before = CollectorRun.objects.count()

    run_collector(_a_collector(), TENANT)

    assert CollectorRun.objects.count() == before


def test_a_skip_is_counted_against_the_run_that_caused_it(collector_holding_the_lock):
    """Resolving open question 7: the skip is attributed to the run responsible,
    which is more informative than a standalone row saying only that something
    was blocked."""
    in_flight = collector_holding_the_lock

    run_collector(_a_collector(), TENANT)

    in_flight.refresh_from_db()
    assert in_flight.skipped_attempts == 1


def test_repeated_skips_accumulate(collector_holding_the_lock):
    """A permanently-overlapping schedule must be visible rather than silent:
    `skipped_attempts` of 11 says the read blocked eleven ticks."""
    in_flight = collector_holding_the_lock

    for _ in range(3):
        run_collector(_a_collector(), TENANT)

    in_flight.refresh_from_db()
    assert in_flight.skipped_attempts == 3


def test_the_skip_counter_is_incremented_atomically(collector_holding_the_lock):
    """Written by a process other than the one owning the run, so it must be a
    database-side increment rather than read-modify-write. Two concurrent skips
    that both read 0 and both write 1 would lose one, which is the concurrency
    rule in DESIGN section 11 applied to its first new case.
    """
    in_flight = collector_holding_the_lock
    barrier = threading.Barrier(2)

    def skip():
        barrier.wait()
        run_collector(_a_collector(), TENANT)
        connection.close()

    threads = [threading.Thread(target=skip) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    in_flight.refresh_from_db()
    assert in_flight.skipped_attempts == 2


def test_a_skipped_run_writes_no_resources(collector_holding_the_lock):
    from datum.discovery.models import DiscoveredResource

    run_collector(_a_collector(), TENANT)

    assert not DiscoveredResource.objects.filter(tenant_id=TENANT).exists()


def test_the_lock_is_free_again_after_a_run_finishes():
    """The ordinary path must not leak the lock, or the second tick of every
    schedule is skipped forever."""
    run_collector(_a_collector(), TENANT)

    run = run_collector(_a_collector(), TENANT)

    assert run is not None
    assert run.status == CollectorRunStatus.SUCCESS


def test_a_run_that_raises_still_frees_the_lock():
    class Exploding:
        name = "kubernetes"

        def fetch(self):
            raise RuntimeError("something nobody anticipated")

        def normalize(self, record, tenant_id):  # pragma: no cover
            raise AssertionError("unreachable")

    with pytest.raises(RuntimeError):
        run_collector(Exploding(), TENANT)

    assert run_collector(_a_collector(), TENANT) is not None


def _a_collector():
    from datum.reconcile.domain import ResourceSnapshot

    class Fake:
        name = "kubernetes"

        def fetch(self):
            return [{"name": "web"}]

        def normalize(self, record, tenant_id):
            return ResourceSnapshot(
                kind="Deployment",
                tenant_id=tenant_id,
                scope="default",
                name=record["name"],
                provider_id="uid-web",
                attributes={"replicas": 1},
            )

    return Fake()
