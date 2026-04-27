"""Tests for scripts.ai_assistant.cli — interactive REPL commands.

Covers: _handle_command, _print_answer, and command dispatch.
All tests mock stream_query to avoid real LLM calls.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

langchain = pytest.importorskip("langchain_core", reason="langchain_core required")

from scripts.ai_assistant.cli import _handle_command, _print_answer  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# _handle_command
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleCommand:
    def test_quit(self):
        _, should_continue = _handle_command(":quit", thread_id="t1")
        assert should_continue is False

    def test_exit(self):
        _, should_continue = _handle_command(":exit", thread_id="t1")
        assert should_continue is False

    @patch("scripts.ai_assistant.cli.reset_agent")
    def test_reset_returns_new_thread(self, mock_reset):
        old_thread = "old-thread-id"
        new_thread, should_continue = _handle_command(":reset", thread_id=old_thread)
        assert should_continue is True
        assert new_thread != old_thread
        mock_reset.assert_called_once()

    def test_thread_shows_current(self, capsys):
        thread_id, should_continue = _handle_command(":thread", thread_id="my-thread")
        assert should_continue is True
        assert thread_id == "my-thread"
        captured = capsys.readouterr()
        assert "my-thread" in captured.out

    def test_good_feedback(self, capsys):
        _, should_continue = _handle_command(":good", thread_id="t1")
        assert should_continue is True
        out = capsys.readouterr().out.lower()
        assert "feedback" in out or "thanks" in out

    def test_bad_feedback(self, capsys):
        _, should_continue = _handle_command(":bad", thread_id="t1")
        assert should_continue is True

    def test_unknown_command(self, capsys):
        _, should_continue = _handle_command(":foo", thread_id="t1")
        assert should_continue is True
        captured = capsys.readouterr()
        assert "unknown" in captured.out.lower()


# ═══════════════════════════════════════════════════════════════════════════
# _print_answer
# ═══════════════════════════════════════════════════════════════════════════


class TestPrintAnswer:
    def test_prints_with_formatting(self, capsys):
        _print_answer("Hello World")
        captured = capsys.readouterr()
        assert "Hello World" in captured.out
        assert "assistant>" in captured.out  # new prefix format
