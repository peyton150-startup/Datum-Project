"""The audit writer: which entries an operator sees, and which sequence decides.

Three levels decide emission, and only one of them keeps state. The tests that
matter here are about that state: which counter an entry advances, which
entries advance one at all, and what a rate below 1 does before it can silently
turn logging off.

**One thing this file deliberately does not test: per-kind sampling isolation.**
Nothing populates `_kind_name`, so every entry reaches the writer with
`kind_name` reading `unknown`, and a test asserting that two kinds keep
separate streams would pass against a writer that keyed on nothing but the
field name. It would demonstrate the opposite of what its name claimed. That
test belongs to phase 2H, which supplies the real kind name. What *is*
testable now -- that two different fields keep separate streams -- is tested
below, and is the same mechanism seen through the dimension that varies.
"""

import logging

import pytest

from datum.reconcile.audit import (
    DISCREPANCY_RESULT,
    MATCH_RESULT,
    AuditLogWriter,
    InvalidSampleRate,
    audit_log_writer,
)
from datum.reconcile.comparison import AuditLogEntry, compare_numeric
from datum.reconcile.domain import PlaneValue
from datum.reconcile.schema import VALID_LOGGING_LEVELS, FieldConfig

AUDIT_LOGGER = "datum.reconcile.audit"


def config(level):
    return FieldConfig("replicas", "numeric", {"mode": "exact_value"}, level)


def entry(field_name="replicas", kind_name="unknown", result=True):
    """An audit entry with only the fields the writer reads varied."""
    return AuditLogEntry(
        kind_name=kind_name,
        field_name=field_name,
        field_type="numeric",
        comparison_mode="exact_value",
        declared=PlaneValue.of(3),
        declared_transformed=3,
        discovered=PlaneValue.of(3),
        discovered_transformed=3,
        result=result,
        steps=["Mode: exact_value", "Result: True"],
    )


class TestTheRateIsRefusedBeforeItCanTurnLoggingOff:
    """A rate below 1 fails at construction rather than at the first entry.

    The bug excluded: clamping or sanitising a bad rate. Under that bug
    `AuditLogWriter(0)` returns a writer, and the deployment quietly logs
    something other than what its configuration says -- which is the failure
    mode, not the exception.
    """

    @pytest.mark.parametrize("rate", [0, -1, -100])
    def test_a_rate_below_one_is_refused(self, rate):
        with pytest.raises(InvalidSampleRate):
            AuditLogWriter(rate)

    def test_one_is_accepted_because_it_is_the_boundary(self):
        assert AuditLogWriter(1) is not None

    def test_the_message_names_the_offending_rate(self):
        """So an operator reads which setting to change, not that one is wrong."""
        with pytest.raises(InvalidSampleRate, match="0"):
            AuditLogWriter(0)


class TestDebugEmitsEverything:
    def test_a_match_is_emitted(self):
        assert AuditLogWriter(10).write(entry(result=True), config("debug")) is True

    def test_a_discrepancy_is_emitted(self):
        assert AuditLogWriter(10).write(entry(result=False), config("debug")) is True


class TestDiscrepancyEmitsOnlyDisagreements:
    def test_a_discrepancy_is_emitted(self):
        assert AuditLogWriter(10).write(entry(result=False), config("discrepancy")) is True

    def test_a_match_is_not_emitted(self):
        assert AuditLogWriter(10).write(entry(result=True), config("discrepancy")) is False


class TestSampledAuditEmitsEveryNth:
    """Rate N emits the Nth eligible entry, then the 2Nth.

    The bug excluded by the first test: emitting entry 1 -- an off-by-one that
    a rate of 1 would hide entirely, because at N=1 every entry is the Nth.
    """

    def test_the_first_entry_is_not_a_sample_at_a_rate_above_one(self):
        writer = AuditLogWriter(3)
        assert writer.write(entry(), config("sampled_audit")) is False

    def test_the_third_of_three_is_the_sample_at_rate_three(self):
        writer = AuditLogWriter(3)
        emitted = [writer.write(entry(), config("sampled_audit")) for _ in range(3)]
        assert emitted == [False, False, True]

    def test_sampling_continues_at_multiples(self):
        writer = AuditLogWriter(3)
        emitted = [writer.write(entry(), config("sampled_audit")) for _ in range(9)]
        assert emitted == [False, False, True, False, False, True, False, False, True]

    def test_a_rate_of_one_emits_every_entry(self):
        writer = AuditLogWriter(1)
        emitted = [writer.write(entry(), config("sampled_audit")) for _ in range(4)]
        assert emitted == [True, True, True, True]


class TestEachStreamCountsAlone:
    """Two fields interleaving must not borrow each other's position.

    The bug excluded: one counter per writer. Under that bug the interleaved
    run below emits on writer entries 2 and 4 -- which is `region`'s first
    entry and `replicas`'s second -- rather than on each field's own second.
    The fixture interleaves rather than running the fields in sequence,
    because a sequential run gives the same answer under both designs.
    """

    def test_interleaved_fields_each_emit_on_their_own_second_entry(self):
        writer = AuditLogWriter(2)
        level = config("sampled_audit")

        first_replicas = writer.write(entry(field_name="replicas"), level)
        first_region = writer.write(entry(field_name="region"), level)
        second_replicas = writer.write(entry(field_name="replicas"), level)
        second_region = writer.write(entry(field_name="region"), level)

        assert (first_replicas, first_region) == (False, False)
        assert (second_replicas, second_region) == (True, True)

    def test_a_quiet_field_does_not_advance_a_busy_one(self):
        """Nine entries for one field leave the other still on its first."""
        writer = AuditLogWriter(2)
        level = config("sampled_audit")

        for _ in range(9):
            writer.write(entry(field_name="replicas"), level)

        assert writer.write(entry(field_name="region"), level) is False


class TestOnlySampledEntriesAdvanceAStream:
    """`debug` and `discrepancy` entries must not shift a sampled sequence.

    The bug excluded: advancing the counter in `write` before the level is
    consulted. Under that bug the two entries logged at other levels below
    consume positions 1 and 2, so the sampled entry that follows lands on
    position 3 and is emitted at rate 3 -- one entry early, and early by
    however many entries other levels happened to log.
    """

    def test_entries_at_other_levels_do_not_consume_positions(self):
        writer = AuditLogWriter(3)

        writer.write(entry(result=False), config("discrepancy"))
        writer.write(entry(), config("debug"))

        emitted = [writer.write(entry(), config("sampled_audit")) for _ in range(3)]
        assert emitted == [False, False, True]


class TestTheWriterCoversEveryLevelTheSchemaAllows:
    def test_every_valid_level_can_be_written(self):
        """A fourth level added to the vocabulary would KeyError here.

        The writer's table has no fall-through, on the grounds that
        `FieldConfig` has already refused any level outside
        `VALID_LOGGING_LEVELS`. That reasoning holds only while the two agree,
        and this is what makes a disagreement fail loudly rather than at
        whichever deployment first configures the new level.
        """
        writer = AuditLogWriter(1)
        for level in VALID_LOGGING_LEVELS:
            writer.write(entry(result=False), config(level))


class TestTheRenderedLineKeepsWhatTheEntryCarries:
    def test_absent_and_null_render_differently(self, caplog):
        """The bug excluded: rendering the plane's value instead of the plane.

        Under that bug both lines read `None`, and the distinction the entry
        was widened to carry is lost at the last step before an operator sees
        it -- the same collapse, one layer further out.
        """
        writer = AuditLogWriter(1)
        absent_entry, null_entry = _entries_for(PlaneValue.absent(), PlaneValue.of(None))

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            writer.write(absent_entry, config("debug"))
            writer.write(null_entry, config("debug"))

        absent_line, null_line = caplog.messages
        assert absent_line != null_line
        assert "PlaneValue.absent()" in absent_line
        assert "PlaneValue.of(None)" in null_line

    def test_a_match_and_a_discrepancy_are_spelled_differently(self, caplog):
        writer = AuditLogWriter(1)

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            writer.write(entry(result=True), config("debug"))
            writer.write(entry(result=False), config("debug"))

        match_line, discrepancy_line = caplog.messages
        assert f"result: {MATCH_RESULT}" in match_line
        assert f"result: {DISCREPANCY_RESULT}" in discrepancy_line

    def test_a_match_line_does_not_contain_the_discrepancy_word_at_all(self, caplog):
        """The bug excluded: a match word that spells the discrepancy word.

        `NO DISCREPANCY` passes the test above and fails this one, because
        `grep DISCREPANCY` then returns every agreeing comparison too. The
        assertion is deliberately unanchored -- anchoring it to `result: ` is
        what made the old spelling look safe.
        """
        writer = AuditLogWriter(1)

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            writer.write(entry(result=True), config("debug"))

        assert DISCREPANCY_RESULT not in caplog.messages[0]

    def test_the_steps_reach_the_line(self, caplog):
        writer = AuditLogWriter(1)

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            writer.write(entry(), config("debug"))

        assert "step: Mode: exact_value" in caplog.messages[0]

    def test_nothing_is_logged_when_nothing_is_emitted(self, caplog):
        writer = AuditLogWriter(10)

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            writer.write(entry(result=True), config("discrepancy"))

        assert caplog.messages == []


def _entries_for(declared, discovered):
    """Two real entries from the comparison path, not hand-built ones.

    Built through `compare_numeric` so the rendering is tested against what the
    comparison functions actually produce, rather than against a fixture that
    could drift from them.
    """
    _, absent_entry = compare_numeric(declared, PlaneValue.of(3), config("debug"))
    _, null_entry = compare_numeric(discovered, PlaneValue.of(3), config("debug"))
    return absent_entry, null_entry


class TestTheFactoryReadsTheSetting:
    def test_it_builds_a_writer_at_the_configured_rate(self, settings):
        settings.AUDIT_SAMPLE_RATE = 2
        writer = audit_log_writer()

        emitted = [writer.write(entry(), config("sampled_audit")) for _ in range(2)]
        assert emitted == [False, True]

    def test_a_configured_rate_below_one_is_refused(self, settings):
        """The setting is not validated where it is defined, so this is the gate."""
        settings.AUDIT_SAMPLE_RATE = 0
        with pytest.raises(InvalidSampleRate):
            audit_log_writer()
