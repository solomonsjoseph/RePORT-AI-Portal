"""Tests for scripts.utils.telemetry — PHI-masked, append-only telemetry logging.

Covers: _mask_phi, _append_event, and TelemetryLogger callback handler.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

langchain = pytest.importorskip("langchain_core", reason="langchain_core required")

from scripts.utils.telemetry import TelemetryLogger, _append_event, _mask_phi  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# _mask_phi
# ═══════════════════════════════════════════════════════════════════════════


class TestMaskPhi:
    def test_ssn_redacted(self):
        assert "[REDACTED]" in _mask_phi("My SSN is 123-45-6789")

    def test_email_redacted(self):
        assert "[REDACTED]" in _mask_phi("Contact user@example.com for details")

    def test_date_redacted(self):
        assert "[REDACTED]" in _mask_phi("Born on 01/15/1990")

    def test_clean_text_unchanged(self):
        text = "This is a normal clinical observation"
        assert _mask_phi(text) == text

    def test_multiple_patterns(self):
        text = "SSN 123-45-6789, email a@b.com"
        masked = _mask_phi(text)
        assert "123-45-6789" not in masked
        assert "a@b.com" not in masked


# ═══════════════════════════════════════════════════════════════════════════
# _append_event
# ═══════════════════════════════════════════════════════════════════════════


class TestAppendEvent:
    def test_writes_valid_jsonl(self, tmp_path: Path, monkeypatch):
        sink = tmp_path / "telemetry.jsonl"
        monkeypatch.setattr("scripts.utils.telemetry.config.TELEMETRY_SINK", str(sink))

        event = {"type": "test", "message": "hello"}
        _append_event(event)

        assert sink.exists()
        lines = sink.read_text().strip().split("\n")
        assert len(lines) >= 1
        parsed = json.loads(lines[-1])
        assert parsed["type"] == "test"

    def test_creates_parent_directory(self, tmp_path: Path, monkeypatch):
        sink = tmp_path / "nested" / "dir" / "telemetry.jsonl"
        monkeypatch.setattr("scripts.utils.telemetry.config.TELEMETRY_SINK", str(sink))

        _append_event({"type": "test"})
        assert sink.exists()


# ═══════════════════════════════════════════════════════════════════════════
# TelemetryLogger
# ═══════════════════════════════════════════════════════════════════════════


class TestTelemetryLogger:
    def test_on_tool_start_logs_event(self, tmp_path: Path, monkeypatch):
        sink = tmp_path / "telemetry.jsonl"
        monkeypatch.setattr("scripts.utils.telemetry.config.TELEMETRY_SINK", str(sink))

        logger = TelemetryLogger()
        logger.on_tool_start(
            serialized={"name": "search_variables"},
            input_str="What is SUBJID?",
        )

        lines = sink.read_text().strip().split("\n")
        event = json.loads(lines[-1])
        assert event["type"] == "tool_start"
        assert event["tool"] == "search_variables"

    def test_on_custom_event_masks_phi(self, tmp_path: Path, monkeypatch):
        sink = tmp_path / "telemetry.jsonl"
        monkeypatch.setattr("scripts.utils.telemetry.config.TELEMETRY_SINK", str(sink))

        logger = TelemetryLogger()
        logger.on_custom_event(
            name="test_event",
            data={"query": "SSN is 123-45-6789"},
        )

        lines = sink.read_text().strip().split("\n")
        event = json.loads(lines[-1])
        assert "123-45-6789" not in event.get("query", "")
