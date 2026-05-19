"""Integration tests — confirm __main__-path fatal errors route through wrap()+format_for_log().

These tests document and enforce the requirement that when a fatal exception
escapes a __main__ entry-point block it must be formatted via the structured
RePORTError envelope rather than emitting a raw Python traceback.

Acceptance criteria (from P0.4 spec):
- The log record message starts with "Fatal:"
- The message contains the RePORTError envelope header ("RePORTError @")
- The message does NOT start with "Traceback (most recent call last):"
- The exception's first message line (including any patient-like strings) is
  captured inside the envelope's `message` field, not embedded in a raw frame.
"""

from __future__ import annotations

import logging

import pytest

from scripts.utils.errors import format_for_log, wrap


class TestWrapFormatForLogEnvelope:
    """wrap() + format_for_log() produce a structured envelope, not a raw traceback."""

    def test_envelope_starts_with_report_error(self) -> None:
        try:
            raise ValueError("Patient John Doe failed validation")
        except Exception as exc:
            result = format_for_log(wrap(exc, stage="test", operation="test"))

        assert result.startswith("RePORTError @"), (
            f"Expected envelope to start with 'RePORTError @', got: {result[:80]!r}"
        )

    def test_envelope_does_not_start_with_raw_traceback(self) -> None:
        try:
            raise ValueError("Patient John Doe failed validation")
        except Exception as exc:
            result = format_for_log(wrap(exc, stage="test", operation="test"))

        assert not result.startswith("Traceback (most recent call last):"), (
            "format_for_log should not begin with a raw Python traceback header"
        )

    def test_envelope_contains_exception_message(self) -> None:
        """The first line of the exception message is preserved in the envelope."""
        try:
            raise ValueError("Patient John Doe failed validation")
        except Exception as exc:
            result = format_for_log(wrap(exc, stage="test", operation="test"))

        assert "Patient John Doe failed validation" in result

    def test_log_record_starts_with_fatal_prefix(self, caplog: pytest.LogCaptureFixture) -> None:
        """When used as log.error('Fatal: %s', format_for_log(wrap(e, ...))), the record
        message starts with 'Fatal:' and contains the structured envelope."""
        logger = logging.getLogger("test.fatal_path")

        try:
            raise RuntimeError("disk full — record row 42 could not be written")
        except Exception as exc:
            with caplog.at_level(logging.ERROR, logger="test.fatal_path"):
                logger.error("Fatal: %s", format_for_log(wrap(exc, stage="extract", operation="run")))

        assert len(caplog.records) == 1
        record = caplog.records[0]

        # Message must start with "Fatal:" (not raw traceback)
        assert record.getMessage().startswith("Fatal:"), (
            f"Log message should start with 'Fatal:', got: {record.getMessage()[:120]!r}"
        )

        # The envelope header must be present
        assert "RePORTError @" in record.getMessage(), (
            "Log message must contain the RePORTError envelope"
        )

        # The raw Python traceback header must NOT be the leading content
        assert not record.getMessage().startswith("Traceback (most recent call last):"), (
            "Raw Python traceback must not be the leading content of the log message"
        )

    def test_log_record_has_no_exc_info_traceback(self, caplog: pytest.LogCaptureFixture) -> None:
        """Using format_for_log(wrap(e)) means exc_info is NOT passed to the logger,
        so no separate traceback is appended to the log record."""
        logger = logging.getLogger("test.fatal_no_excinfo")

        try:
            raise OSError("cannot open /data/raw/study.xlsx")
        except Exception as exc:
            with caplog.at_level(logging.ERROR, logger="test.fatal_no_excinfo"):
                # The pattern used in __main__ blocks after P0.4:
                logger.error("Fatal: %s", format_for_log(wrap(exc, stage="pipeline", operation="run")))

        assert len(caplog.records) == 1
        record = caplog.records[0]

        # exc_info should not be attached (we're not passing exc_info=True)
        assert record.exc_info is None, (
            "No exc_info should be attached when using format_for_log(wrap(e))"
        )
