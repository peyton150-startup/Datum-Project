"""Bounded retry with exponential backoff, for provider calls.

DESIGN section 11: provider calls are retried with exponential backoff on 429
and 5xx, up to a bounded number of attempts. Kept provider-neutral and in its
own module because the OCI collector needs the same behaviour and a second copy
would be a second thing to get the bound wrong in.

**Bounded is the whole point.** An unbounded retry against a provider that is
genuinely down converts a failed run into a hung one, and a hung run holds the
collector lock (section 11, concurrency) while producing nothing. Exhausting the
attempts is a legitimate outcome here, not a last resort.

What counts as transient is the caller's to decide, because only the caller
knows its SDK's exception shape. This module owns the schedule, not the
taxonomy.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 0.5
# 0.5, 1, 2 between four attempts: about three and a half seconds of patience.
# Long enough to ride out a rate limit, short enough that a scheduled run does
# not still be sleeping when the next tick arrives.
BACKOFF_MULTIPLIER = 2.0

T = TypeVar("T")


def with_retry(
    call: Callable[[], T],
    is_transient: Callable[[BaseException], bool],
    description: str,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay_seconds: float = BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `call`, retrying while `is_transient` says the failure may pass.

    Re-raises the last exception when the attempts run out, so the caller
    decides what an exhausted retry means for its run -- which differs by
    whether anything had already been read.

    A failure `is_transient` rejects is raised immediately and never retried:
    retrying a malformed request just makes the same mistake more slowly.

    `sleep` is injected so tests exercise the schedule without waiting through
    it. Its default is the only reason this function is not pure.
    """
    assert max_attempts >= 1, f"{description} needs at least one attempt, got {max_attempts}"

    delay = base_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except BaseException as exc:
            if not is_transient(exc):
                raise
            if attempt == max_attempts:
                logger.warning(
                    "%s failed on attempt %s of %s and will not be retried again",
                    description,
                    attempt,
                    max_attempts,
                )
                raise
            logger.info(
                "%s failed on attempt %s of %s (%s); retrying in %ss",
                description,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            sleep(delay)
            delay *= BACKOFF_MULTIPLIER

    # Unreachable: the loop either returns, raises on a non-transient failure,
    # or raises on the final attempt. Present so every path ends in a value, and
    # excluded from coverage because a test for it would have to break the loop
    # invariant it exists to catch.
    raise AssertionError(  # pragma: no cover
        f"{description} exhausted its retry loop without returning or raising"
    )


__all__ = ["BASE_DELAY_SECONDS", "MAX_ATTEMPTS", "with_retry"]
