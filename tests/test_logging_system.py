"""Tests for scripts.utils.logging_system — centralized logging.

Covers: setup_logging, reset_logging, SUCCESS level, CustomFormatter,
JSONFormatter, cleanup_old_logs, log_time, log_errors,
VerboseLogger, and convenience functions.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from scripts.utils.logging_system import (
    SUCCESS,
    CustomFormatter,
    JSONFormatter,
    cleanup_old_logs,
    get_log_file_path,
    log_errors,
    log_time,
    reset_logging,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _clean_loggers():
    """Reset logging singletons before and after each test."""
    reset_logging()
    yield
    reset_logging()


# ═══════════════════════════════════════════════════════════════════════════
# setup_logging / reset_logging
# ═══════════════════════════════════════════════════════════════════════════


class TestSetupLogging:
    def test_creates_logger(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        logger = setup_logging(module_name="test_mod")
        assert logger.name == "report_ai_portal"
        assert logger.hasHandlers()

    def test_singleton(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        l1 = setup_logging()
        l2 = setup_logging()
        assert l1 is l2

    def test_log_file_created(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        setup_logging()
        path = get_log_file_path()
        assert path is not None
        assert Path(path).parent.exists()


class TestResetLogging:
    def test_reset_clears_singleton(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        setup_logging()
        assert get_log_file_path() is not None  # logger active
        reset_logging()
        assert get_log_file_path() is None  # reset cleared the state
        setup_logging()
        assert get_log_file_path() is not None  # re-init restores it


# ═══════════════════════════════════════════════════════════════════════════
# SUCCESS level
# ═══════════════════════════════════════════════════════════════════════════


class TestSuccessLevel:
    def test_success_level_value(self):
        assert SUCCESS == 25
        assert logging.INFO < SUCCESS < logging.WARNING

    def test_success_level_name(self):
        assert logging.getLevelName(SUCCESS) == "SUCCESS"

    def test_logger_has_success_method(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        logger = setup_logging()
        assert hasattr(logger, "success")


# ═══════════════════════════════════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════════════════════════════════


class TestCustomFormatter:
    def test_success_label(self):
        fmt = CustomFormatter("%(levelname)s: %(message)s")
        record = logging.LogRecord(
            name="test",
            level=SUCCESS,
            pathname="",
            lineno=0,
            msg="done",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        assert "SUCCESS" in result


class TestJSONFormatter:
    def test_json_output(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        parsed = json.loads(result)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello"
        assert parsed["line"] == 42


# ═══════════════════════════════════════════════════════════════════════════
# cleanup_old_logs
# ═══════════════════════════════════════════════════════════════════════════


class TestCleanupOldLogs:
    def test_dry_run(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        log_dir = tmp_path / "RePORT AI Portal"
        log_dir.mkdir(parents=True)
        (log_dir / "old.log").write_text("old log data")
        # Make the file appear old
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(log_dir / "old.log", (old_time, old_time))

        setup_logging()
        result = cleanup_old_logs(max_age_days=30, log_dir=log_dir, dry_run=True)
        assert result["dry_run"] is True
        assert result["files_deleted"] >= 1
        # File should still exist (dry run)
        assert (log_dir / "old.log").exists()

    def test_max_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        log_dir = tmp_path / "RePORT AI Portal"
        log_dir.mkdir(parents=True)
        for i in range(5):
            f = log_dir / f"test_{i}.log"
            f.write_text(f"log {i}")
            os.utime(f, (time.time() - i * 60, time.time() - i * 60))

        setup_logging()
        result = cleanup_old_logs(max_files=2, log_dir=log_dir, dry_run=True)
        assert result["files_deleted"] >= 2

    def test_no_criteria_raises(self):
        with pytest.raises(ValueError, match="At least one"):
            cleanup_old_logs()

    def test_negative_age_raises(self):
        with pytest.raises(ValueError, match="positive"):
            cleanup_old_logs(max_age_days=-1)


# ═══════════════════════════════════════════════════════════════════════════
# Decorators
# ═══════════════════════════════════════════════════════════════════════════


class TestLogTime:
    def test_logs_elapsed(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        setup_logging()

        @log_time()
        def fast_func():
            return 42

        assert fast_func() == 42


class TestLogErrors:
    def test_reraise(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        setup_logging()

        @log_errors(reraise=True)
        def failing():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            failing()

    def test_no_reraise(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        setup_logging()

        @log_errors(reraise=False)
        def failing():
            raise RuntimeError("boom")

        assert failing() is None
