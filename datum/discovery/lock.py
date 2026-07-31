"""Exclusion between collector runs: DESIGN section 11.

One run per collector per tenant. A second run starting while one is in flight
is skipped rather than queued -- the later run would read the same estate and
the two would race on the same rows to no benefit.

The mechanism moved to `datum/locks.py` when reconciliation needed the same
exclusion. This module keeps the collector's name for it, because a collector
run is what section 11 decided about and what `run_collector` asks for.

Deliberately `run_lock` and not `locked_run`: a collector holds its lock across
a provider read, and wrapping that in a transaction would hold one open across
network calls of unbounded duration. The writes inside `_read_once` manage their
own atomicity.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from datum.locks import run_lock


@contextmanager
def collector_lock(tenant_id: str, collector_name: str) -> Iterator[bool]:
    """One run per collector per tenant. See `datum.locks.run_lock`."""
    with run_lock(tenant_id, collector_name) as acquired:
        yield acquired


__all__ = ["collector_lock"]
