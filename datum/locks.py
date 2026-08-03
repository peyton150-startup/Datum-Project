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

**The rule: a run lock must outlive the commit that publishes the work it
protects.** Release it earlier and a second worker acquires it, begins its own
pass before the first transaction commits, and computes from the previously
committed state -- the lock still looks present and excludes nothing.

That one rule has two lifetimes, because the two callers publish differently,
and the two are not interchangeable:

- **`run_lock` is session-scoped.** A collector holds its lock across a provider
  read and manages the atomicity of its writes itself, so there is no single
  transaction to tie the lock to. Session scope is the only scope that spans it.
- **`locked_run` is transaction-scoped.** Its whole body is one transaction, so
  `pg_try_advisory_xact_lock` ties the lock to exactly the commit it guards and
  Postgres releases the two together. There is no window between them to get
  wrong, and no unlock call to place correctly.

The second exists because ordering the lock and the transaction by hand is a
mistake available from both sides. `locked_run` used to compose `run_lock`
around `transaction.atomic()`, which stopped a *callee* from inverting them but
not a *caller*: Django turns a nested `atomic()` into a savepoint, so the block
exits by releasing a savepoint rather than committing, and a session-scoped
unlock then fires while the real commit is still pending (#36). A
transaction-scoped lock has no such failure mode -- nested or not, it is held
until the transaction that will actually commit ends, so the correct ordering is
the only ordering expressible.

**Do not swap `run_lock` to the transaction-scoped call.** Acquired outside a
transaction it is taken and released inside the implicit single-statement
transaction, so it excludes nothing at all, silently.
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
    """Hold a transaction and a lock that lasts exactly as long as it.

    Yields True inside an open transaction when the run may proceed, and False
    -- having opened none of its own -- when another session already holds the
    lock.

    The lock is transaction-scoped, so Postgres releases it when the transaction
    ends and never before. Nothing here chooses the moment of release, which is
    the point: the ordering bug this guards against is not available to write.
    That holds when a caller has already opened a transaction of its own, too --
    the `atomic()` below is then a savepoint, the lock binds to the outer
    transaction that will really commit, and it is held until that commit rather
    than until this block exits (#36).

    Note the asymmetry with `run_lock`, which is session-scoped and must stay
    that way. The module docstring says why.
    """
    key = _advisory_key(tenant_id, operation)
    with transaction.atomic():
        if _try_acquire_until_commit(key):
            yield True
            return
    # Denied. The transaction above wrote nothing and is already closed, so the
    # caller is handed a False that is not sitting inside a transaction of ours.
    yield False


def _advisory_key(tenant_id: str, operation: str) -> int:
    """A stable 64-bit key for this (tenant, operation) pair.

    Stability across processes is the whole requirement: two workers must derive
    the same key or the lock excludes nothing. The separator keeps
    ("ab", "c") and ("a", "bc") from colliding.
    """
    digest = blake2b(f"{tenant_id}\x00{operation}".encode(), digest_size=_KEY_BYTES)
    return int.from_bytes(digest.digest(), "big", signed=False) - _SIGNED_64_BIT_MAX


def _try_acquire(key: int) -> bool:
    """Take the lock for this session, until something releases it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        row = cursor.fetchone()
    return bool(row[0])


def _try_acquire_until_commit(key: int) -> bool:
    """Take the lock for the open transaction, which alone can release it.

    Callers must already be inside a transaction. Outside one this still
    reports success, having acquired and released the lock within the implicit
    transaction of this single statement -- excluding nothing, and saying so to
    nobody.
    """
    assert connection.in_atomic_block, "a transaction-scoped lock needs a transaction"
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
        row = cursor.fetchone()
    return bool(row[0])


def _release(key: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


__all__ = ["locked_run", "run_lock"]
