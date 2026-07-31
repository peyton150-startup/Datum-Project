"""Run exclusion, shared by the operations that own an estate-wide pass.

Extracted from `discovery/lock.py` when reconciliation needed the same
exclusion. The mechanism was always general: a tenant, a name for the thing
being excluded, and one holder at a time.

**Why a Postgres advisory lock.** There is no natural row to lock: the thing
being excluded is a run that does not exist yet. A broker-level lock would work
but adds a second authority that can disagree with the first about who holds
it, and the database is already the thing both workers agree on. Advisory locks
are also held per *session*, so a worker that dies takes its lock with it --
there is no stale lock row for someone to clean up at three in the morning.

**The lock must be taken outside the caller's transaction.** Advisory locks are
session-scoped and `run_lock` releases on exit, so a lock taken inside an atomic
block is released before the commit it was meant to protect. A second worker can
then acquire the lock, begin its own pass before the first transaction commits,
and compute from the previously committed state -- the lock still looks present
and excludes nothing. `locked_run` exists so that the correct composition is the
convenient one; prefer it wherever the body wants a transaction.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import blake2b

from django.db import connection, transaction

logger = logging.getLogger(__name__)

# Postgres advisory locks are keyed on a signed 64-bit integer, and the key here
# is a pair of strings, so it is hashed down. blake2b rather than Python's hash()
# because hash() is salted per process: two workers would derive different keys
# for the same operation and neither would ever block the other.
_KEY_BYTES = 8
_SIGNED_64_BIT_MAX = 2**63

# TODO: the key space is flat -- an operation name is hashed with the tenant and
# nothing else, so collector names and operation names share one namespace. When
# a third kind of operation arrives, namespacing the key (("collector", name) vs
# ("reconcile", tenant)) will be worth doing, and it needs a *version* in the
# derivation plus a documented deploy procedure: changing how the key is derived
# means old and new workers compute different keys and stop excluding each other
# for the length of a rolling deploy.


@contextmanager
def run_lock(tenant_id: str, operation: str) -> Iterator[bool]:
    """Hold the run lock for one operation and tenant, or report it taken.

    Yields True when the lock was acquired and False when another session holds
    it. Never blocks: a caller that has to wait for the lock would be waiting to
    do work the holder is already doing.

    Releases on exit, including when the body raises -- a run that crashes must
    not wedge the operation until someone restarts the worker.

    Callers whose body opens a transaction want `locked_run` instead. See this
    module's docstring for why the ordering is not a matter of taste.
    """
    key = _advisory_key(tenant_id, operation)
    acquired = _try_acquire(key)
    try:
        yield acquired
    finally:
        # Only the holder releases. A caller that was denied the lock must not
        # unlock on its way out: Postgres would warn about releasing a lock it
        # does not hold, and on a connection shared between logical callers it
        # would hand away a lock still in use.
        if acquired:
            _release(key)


@contextmanager
def locked_run(tenant_id: str, operation: str) -> Iterator[bool]:
    """Hold the run lock and, if granted, a transaction inside it.

    The lock is acquired before the transaction opens and released after it
    commits. That order is the reason this helper exists: a caller nesting the
    two blocks itself can invert them, and an advisory lock released before its
    commit excludes nothing while still looking present.

    Yields True inside an open transaction when the run may proceed, and False
    -- with no transaction -- when another session already holds the lock.
    """
    with run_lock(tenant_id, operation) as acquired:
        if not acquired:
            yield False
            return
        with transaction.atomic():
            yield True


def _advisory_key(tenant_id: str, operation: str) -> int:
    """A stable 64-bit key for this (tenant, operation) pair.

    Stability across processes is the whole requirement: two workers must derive
    the same key or the lock excludes nothing. The separator keeps
    ("ab", "c") and ("a", "bc") from colliding.
    """
    digest = blake2b(f"{tenant_id}\x00{operation}".encode(), digest_size=_KEY_BYTES)
    return int.from_bytes(digest.digest(), "big", signed=False) - _SIGNED_64_BIT_MAX


def _try_acquire(key: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        row = cursor.fetchone()
    return bool(row[0])


def _release(key: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


__all__ = ["locked_run", "run_lock"]
