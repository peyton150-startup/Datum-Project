"""Exclusion between collector runs: DESIGN section 11.

One run per collector per tenant. A second run starting while one is in flight
is skipped rather than queued -- the later run would read the same estate and
the two would race on the same rows to no benefit.

**Why a Postgres advisory lock.** There is no natural row to lock: the thing
being excluded is a run that does not exist yet. A broker-level lock would work
but adds a second authority that can disagree with the first about who holds
it, and the database is already the thing both workers agree on. Advisory locks
are also held per *session*, so a worker that dies takes its lock with it --
there is no stale lock row for someone to clean up at three in the morning.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import blake2b

from django.db import connection

logger = logging.getLogger(__name__)

# Postgres advisory locks are keyed on a signed 64-bit integer, and the key here
# is a pair of strings, so it is hashed down. blake2b rather than Python's hash()
# because hash() is salted per process: two workers would derive different keys
# for the same collector and neither would ever block the other.
_KEY_BYTES = 8
_SIGNED_64_BIT_MAX = 2**63


@contextmanager
def collector_lock(tenant_id: str, collector_name: str) -> Iterator[bool]:
    """Hold the run lock for one collector and tenant, or report it taken.

    Yields True when the lock was acquired and False when another session holds
    it. Never blocks: a caller that has to wait for the lock would be waiting to
    do work the holder is already doing.

    Releases on exit, including when the body raises -- a run that crashes must
    not wedge the collector until someone restarts the worker.
    """
    key = _advisory_key(tenant_id, collector_name)
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


def _advisory_key(tenant_id: str, collector_name: str) -> int:
    """A stable 64-bit key for this (tenant, collector) pair.

    Stability across processes is the whole requirement: two workers must derive
    the same key or the lock excludes nothing. The separator keeps
    ("ab", "c") and ("a", "bc") from colliding.
    """
    digest = blake2b(f"{tenant_id}\x00{collector_name}".encode(), digest_size=_KEY_BYTES)
    return int.from_bytes(digest.digest(), "big", signed=False) - _SIGNED_64_BIT_MAX


def _try_acquire(key: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        row = cursor.fetchone()
    return bool(row[0])


def _release(key: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


__all__ = ["collector_lock"]
