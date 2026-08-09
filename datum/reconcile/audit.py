"""Emission of comparison audit entries, at the three configured levels.

A comparison always produces an `AuditLogEntry`. This module decides which of
them an operator actually sees, and it is the only thing that decides that:
`comparison` stays pure and returns its entry whatever the level says, so what
gets logged can never change what gets compared.

**The sink is a logger, not a table.** `AuditLogEntry` names an auditable
event, not a row; nothing here persists anything, and whether audit events are
ever persisted is a separate question with its own retention, volume, and
indexing answers to give. See WBS 1.5.2 phase 2G in the project plan.

The three levels:

- `debug` emits every entry
- `discrepancy` emits only entries that found one
- `sampled_audit` emits every Nth entry, per field
"""

import logging
from collections.abc import Callable

from django.conf import settings

from datum.reconcile.comparison import AuditLogEntry
from datum.reconcile.schema import (
    LOGGING_DEBUG,
    LOGGING_DISCREPANCY,
    LOGGING_SAMPLED_AUDIT,
    FieldConfig,
)

logger = logging.getLogger("datum.reconcile.audit")

# Below this, a rate names no sampling at all: 0 makes the modulo raise, and a
# negative rate can never be reached by a count that only rises, so it emits
# nothing forever -- which is `sampled_audit` meaning "never", under a name that
# says the opposite. Refused at construction for the reason `schema.py` refuses
# `tolerance(inf)`: a setting that quietly turns logging off is worse than one
# that fails when it is read.
MINIMUM_SAMPLE_RATE = 1

# The two words an operator greps for. Spelled once, because a second spelling
# of either is a line that the search built around the first will not find --
# the failure the degraded-comparison helpers in `comparison` exist to prevent.
#
# **Disjoint, not merely distinct.** The match word was `NO DISCREPANCY`, which
# spells the other one inside itself, so `grep DISCREPANCY` -- the obvious
# query, and the one the constant's own name suggests -- returned every
# agreeing comparison as well. Anchoring the search to `result: DISCREPANCY`
# did exclude them, but a vocabulary that is only safe when grepped a
# particular way is the same trap one level down. Neither word may contain the
# other.
MATCH_RESULT = "MATCH"
DISCREPANCY_RESULT = "DISCREPANCY"


class InvalidSampleRate(ValueError):
    """The configured audit sample rate names no sampling."""


class AuditLogWriter:
    """Emits audit entries for one reconciliation run.

    Holds the sampling counters, which is why it is an object rather than a
    function: `sampled_audit` has to remember how many entries it has seen, and
    the two places that state could otherwise live are both worse. A module
    global would make one run's output depend on every run before it in the
    same process, and put tests in each other's way. A counter on the field
    config would make the count outlive the run that produced it.

    **One counter per `(kind_name, field_name)` stream, not one per run.** A
    single run-wide counter would make each field's sampling depend on which
    *other* fields exist: at N=10, a fifth entry for `replicas` and a fifth for
    `region` interleave into writer entry 10, so adding a sampled field changes
    which entries an existing field emits, and iteration order becomes
    observable in the log.

    **One run, one thread.** The counters are a plain read-modify-write, which
    is safe because a reconciliation run compares its fields in sequence. A
    writer shared across threads would need a lock; a writer per thread would
    give each its own sequence, which is not what a rate means. Stated because
    the docstring above explains why the state is not a module global and would
    otherwise be read as having considered every alternative.

    **The isolation that key buys is not observable yet.** Nothing populates
    `_kind_name`, so every entry arrives with `kind_name` reading `unknown` and
    two kinds declaring a same-named field share one stream. The key is still
    `(kind_name, field_name)`: that is the identity wanted once phase 2H wires
    real kinds through, and 2H turns the isolation on by supplying the name and
    changing nothing here. **No test in this phase may claim to demonstrate
    per-kind independence** -- it would pass against a writer that had none.
    That test belongs to 2H.
    """

    def __init__(self, sample_rate: int) -> None:
        """Prepare a writer for one run.

        Args:
            sample_rate: Emit every Nth eligible entry, per stream

        Raises:
            InvalidSampleRate: If the rate is below MINIMUM_SAMPLE_RATE
        """
        if sample_rate < MINIMUM_SAMPLE_RATE:
            raise InvalidSampleRate(
                f"audit sample rate {sample_rate!r} names no sampling; "
                f"expected an integer >= {MINIMUM_SAMPLE_RATE}"
            )
        self._sample_rate = sample_rate
        self._counts: dict[tuple[str, str], int] = {}

        # A table rather than an if-chain, per the construction conventions, and
        # keyed by the level names `schema` validates against. `FieldConfig`
        # has already refused any level outside that vocabulary, so there is no
        # fall-through to write and no unreachable branch for the coverage gate
        # to count -- the same argument `_validate_comparison_config` makes for
        # its own table.
        self._emits: dict[str, Callable[[AuditLogEntry], bool]] = {
            LOGGING_DEBUG: _always,
            LOGGING_DISCREPANCY: _found_a_discrepancy,
            LOGGING_SAMPLED_AUDIT: self._sample_is_due,
        }

    def write(self, entry: AuditLogEntry, field_config: FieldConfig) -> bool:
        """Emit one entry if its field's configured level calls for it.

        Args:
            entry: The comparison decision to record
            field_config: The configuration that comparison was asked for

        Returns:
            True if the entry was emitted
        """
        if not self._emits[field_config.logging](entry):
            return False
        logger.info(_rendered(entry))
        return True

    def _sample_is_due(self, entry: AuditLogEntry) -> bool:
        """Advance this entry's stream, and say whether it lands on a sample.

        Advances only for the level that samples, because `write` reaches this
        for no other: a `debug` or `discrepancy` entry must not shift a
        sampled field's sequence, or the entries an operator sees would depend
        on how *other* fields are configured.

        Rate N emits the Nth eligible entry, then the 2Nth, and so on; entry 1
        is a sample only at N=1.
        """
        stream = (entry.kind_name, entry.field_name)
        count = self._counts.get(stream, 0) + 1
        self._counts[stream] = count
        return count % self._sample_rate == 0


def _always(entry: AuditLogEntry) -> bool:
    """Every entry, which is what `debug` means."""
    return True


def _found_a_discrepancy(entry: AuditLogEntry) -> bool:
    """Only entries whose comparison disagreed, which is what `discrepancy` means."""
    return not entry.result


def audit_log_writer() -> AuditLogWriter:
    """A writer for one run, at the configured rate.

    The one place the setting is read, so a caller cannot supply a rate that
    came from somewhere else and disagree with the deployment about it.

    Raises:
        InvalidSampleRate: If DATUM_AUDIT_SAMPLE_RATE names no sampling
    """
    return AuditLogWriter(settings.AUDIT_SAMPLE_RATE)


def _rendered(entry: AuditLogEntry) -> str:
    """One audit entry as the lines an operator reads.

    Both planes are spelled by `PlaneValue.__repr__`, which distinguishes
    `PlaneValue.absent()` from `PlaneValue.of(None)`. Rendering the values
    instead would print `None` for both and lose, at the last step before the
    log, precisely the distinction the entry was changed to carry.
    """
    return "\n".join(
        [
            f"[DIFF] {entry.field_type} comparison: kind={entry.kind_name}, "
            f"field={entry.field_name}, mode={entry.comparison_mode}",
            f"  declared: {entry.declared!r} -> {entry.declared_transformed!r}",
            f"  discovered: {entry.discovered!r} -> {entry.discovered_transformed!r}",
            f"  result: {MATCH_RESULT if entry.result else DISCREPANCY_RESULT}",
            *(f"  step: {step}" for step in entry.steps),
        ]
    )
