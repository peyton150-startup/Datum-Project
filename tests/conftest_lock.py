"""Fixtures for the WBS 1.4.4 lock specification.

An advisory lock is only meaningful across connections, so testing it needs a
second one. These fixtures exist so the lock tests read as statements about
behaviour rather than as connection plumbing.
"""

import threading

import pytest
from django.db import connections


@pytest.fixture
def second_connection(django_db_setup, django_db_blocker):
    """Run a context manager on a *different* database connection.

    Returns a callable taking the context manager and its arguments, and
    yielding whether it was granted. A separate thread is used because Django
    binds one connection per thread, which is the cheapest honest way to be a
    second session.

    Postgres advisory locks are held per session, so a same-connection check
    would always succeed and the test would prove nothing.
    """

    def run(context_manager, *args):
        result = {}

        def attempt():
            with django_db_blocker.unblock():
                try:
                    with context_manager(*args) as acquired:
                        result["acquired"] = acquired
                finally:
                    connections.close_all()

        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join(timeout=10)

        # Bounded on purpose. An implementation reaching for the blocking
        # pg_advisory_lock() instead of pg_try_advisory_lock() would leave this
        # thread waiting on a lock the main thread cannot release until this
        # join returns -- a deadlock between the test and its own fixture. With
        # no timeout that hangs the run instead of failing it, which for a spec
        # whose principle is "a flaky test is worse than none" is worse than
        # either: no signal locally, and an unattributed timeout in CI.
        assert not thread.is_alive(), (
            "the second connection never returned; collector_lock is probably "
            "blocking rather than failing fast when the lock is held"
        )
        return result["acquired"]

    return run


@pytest.fixture
def collector_holding_the_lock(second_connection):
    """A run that is in flight and holding the collector lock.

    Yields the `CollectorRun` row so a test can assert against the counter the
    skips land on. The lock is held on another connection for the duration, so
    anything the test does in the main connection is genuinely blocked rather
    than re-entering a lock it already owns.
    """
    from datum.discovery.models import CollectorRun
    from datum.enums import CollectorRunStatus

    tenant_id = "00000000-0000-0000-0000-000000000001"
    run = CollectorRun.objects.create(
        tenant_id=tenant_id,
        collector_name="kubernetes",
        status=CollectorRunStatus.FAILED,
    )

    released = threading.Event()
    holding = threading.Event()

    def hold():
        from django.db import connections

        from datum.discovery.lock import collector_lock

        try:
            with collector_lock(tenant_id, "kubernetes") as acquired:
                assert acquired, "the fixture must actually hold the lock"
                holding.set()
                released.wait(timeout=30)
        finally:
            connections.close_all()

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait(timeout=10)

    yield run

    released.set()
    holder.join(timeout=10)
