"""Tests for the RePORTError structured envelope."""

from __future__ import annotations

import json

from scripts.utils import errors


def test_wrap_captures_class_and_message() -> None:
    try:
        raise FileNotFoundError("missing.json")
    except Exception as exc:
        err = errors.wrap(
            exc,
            stage="agent.tool",
            operation="query_dataset",
            path="/var/empty/missing.json",
            hint="Run the pipeline first.",
        )

    assert err.cause == "FileNotFoundError"
    assert "missing.json" in err.message
    assert err.stage == "agent.tool"
    assert err.operation == "query_dataset"
    assert err.path == "/var/empty/missing.json"
    assert err.hint == "Run the pipeline first."
    assert err.traceback is not None
    assert "FileNotFoundError" in err.traceback


def test_wrap_can_suppress_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except Exception as exc:
        err = errors.wrap(exc, stage="test", operation="op", include_traceback=False)
    assert err.traceback is None


def test_to_json_roundtrip() -> None:
    err = errors.RePORTError(
        stage="pipeline.dataset",
        operation="publish_staging",
        cause="OSError",
        message="No space left on device",
    )
    payload = json.loads(err.to_json())
    assert payload["stage"] == "pipeline.dataset"
    assert payload["operation"] == "publish_staging"
    assert payload["message"] == "No space left on device"
    assert payload["path"] is None
    assert "timestamp" in payload


def test_format_for_user_is_single_line() -> None:
    err = errors.RePORTError(
        stage="ui.load_study",
        operation="run_pipeline",
        cause="PipelineFailure",
        message="exit code 1",
        hint="Check the log",
    )
    rendered = errors.format_for_user(err)
    assert "\n" not in rendered
    assert "ui.load_study" in rendered
    assert "run_pipeline" in rendered
    assert "hint" in rendered


def test_format_for_log_includes_traceback() -> None:
    err = errors.RePORTError(
        stage="s",
        operation="o",
        cause="C",
        message="m",
        traceback="Traceback (most recent call last):\n  frame",
    )
    block = errors.format_for_log(err)
    assert "traceback" in block
    assert "frame" in block


def test_format_for_log_suppresses_traceback_when_include_traceback_false() -> None:
    """Verify that pipeline error handlers using include_traceback=False
    do not surface traceback lines that might embed PHI from row data."""
    try:
        raise ValueError("synthetic-subject-9999 failed date parsing in IC_VISDAT")
    except Exception as exc:
        wrapped = errors.wrap(
            exc,
            stage="pipeline.extract",
            operation="datasets",
            include_traceback=False,
        )
    log_block = errors.format_for_log(wrapped)
    assert "Traceback" not in log_block


def test_wrap_includes_traceback_by_default() -> None:
    """Regression guard: wrap() should include traceback by default."""
    try:
        raise ValueError("boom")
    except Exception as exc:
        wrapped = errors.wrap(exc, stage="s", operation="o")
    assert wrapped.traceback is not None
    assert "ValueError" in wrapped.traceback
