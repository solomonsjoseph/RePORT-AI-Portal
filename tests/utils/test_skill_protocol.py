"""Tests for the skill subprocess contract (Wave 4 B3.x / D3)."""

from __future__ import annotations

import pytest

from scripts.utils.skill_protocol import (
    SKILL_RESULT_PREFIX,
    SkillInvocationError,
    SkillResult,
    emit_skill_result,
    invoke_skill,
    parse_skill_result,
    skill_run_script,
)


def test_emit_then_parse_roundtrip(capsys: pytest.CaptureFixture[str]) -> None:
    emit_skill_result(SkillResult(skill="phi-scrubbing", ok=True, summary="done", data={"n": 3}))
    out = capsys.readouterr().out
    assert out.startswith(SKILL_RESULT_PREFIX)
    result = parse_skill_result(out, skill="phi-scrubbing", exit_code=0)
    assert result.ok is True
    assert result.skill == "phi-scrubbing"
    assert result.summary == "done"
    assert result.data == {"n": 3}


def test_last_marker_wins() -> None:
    stdout = (
        f"{SKILL_RESULT_PREFIX}"
        '{"skill": "x", "ok": false, "summary": "first", "data": {}}\n'
        "some log line\n"
        f"{SKILL_RESULT_PREFIX}"
        '{"skill": "x", "ok": true, "summary": "last", "data": {}}\n'
    )
    result = parse_skill_result(stdout, skill="x", exit_code=0)
    assert result.summary == "last"
    assert result.ok is True


def test_fallback_synthesises_from_exit_code_when_no_marker() -> None:
    ok = parse_skill_result("no marker here", skill="y", exit_code=0)
    assert ok.ok is True and ok.exit_code == 0

    bad = parse_skill_result("crashed early", skill="y", exit_code=7)
    assert bad.ok is False and bad.exit_code == 7


def test_exit_code_comes_from_process_not_payload() -> None:
    """The process exit code is authoritative over a payload's stale exit_code."""
    stdout = f'{SKILL_RESULT_PREFIX}{{"skill": "z", "ok": true, "exit_code": 0}}'
    result = parse_skill_result(stdout, skill="z", exit_code=5)
    assert result.exit_code == 5


def test_skill_run_script_resolves_existing_skill() -> None:
    path = skill_run_script("dataset-to-llm-source")
    assert path.name == "run.py"
    assert path.is_file()


def test_skill_run_script_raises_for_unknown_skill() -> None:
    with pytest.raises(SkillInvocationError):
        skill_run_script("no-such-skill")


def test_invoke_skill_runs_real_entrypoint() -> None:
    """End-to-end: launch a real skill subprocess and parse its marker."""
    result = invoke_skill("dataset-to-llm-source", ["status"])
    assert result.skill == "dataset-to-llm-source"
    assert result.exit_code == 0
    assert result.ok is True
