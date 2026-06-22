"""Skill wrapper contract smoke tests (Wave C.8).

Parametrized subprocess invocations for the four standalone skill entrypoints
that the orchestrator calls via ``skill_protocol.invoke_skill``. Each test
asserts the ``RPLN_SKILL_RESULT:`` marker is emitted and parsed (not
synthesised from exit code alone).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.utils.skill_protocol import SKILL_RESULT_PREFIX, invoke_skill, parse_skill_result
from tests.skills.fixtures.build_fixture import (
    FIXTURE_RUN_ID,
    FIXTURE_STUDY,
    build_golden_output_tree,
)

REPO_ROOT = Path(config.BASE_DIR)
STUDY = "Synth-Demo"
ENROLLMENT_XLSX = REPO_ROOT / "data" / "raw" / STUDY / "datasets" / "1_Enrollment.xlsx"
_PHI_SCRUB_YAML = REPO_ROOT / "config" / "_defaults" / "phi_scrub.yaml"


def _require_synth_demo_raw() -> None:
    if not ENROLLMENT_XLSX.is_file():
        pytest.skip(f"Synth-Demo raw data not present: {ENROLLMENT_XLSX}")


def _assert_marker_parsed(result, *, skill: str, expect_ok: bool) -> None:
    assert result.skill == skill
    assert "no skill-result marker emitted" not in result.summary
    assert result.ok is expect_ok


@pytest.mark.parametrize(
    "skill_name,extra_args,expect_ok",
    [
        pytest.param(
            "header-extraction",
            ["--run-id", "skill_smoke", "--run-dir", "/tmp/rpln_hdr_smoke"],
            True,
            id="header-extraction",
        ),
        pytest.param(
            "dictionary-to-llm-source",
            ["--run-id", "skill_smoke", "--run-dir", "/tmp/rpln_dict_smoke", "--leg", "extract"],
            True,
            id="dictionary-to-llm-source",
        ),
        pytest.param(
            "phi-scrubbing",
            ["--run-id", FIXTURE_RUN_ID],
            True,
            id="phi-scrubbing",
        ),
    ],
)
def test_skill_wrapper_emits_rpln_marker(
    skill_name: str,
    extra_args: list[str],
    expect_ok: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synth-Demo raw study: marker + exit code round-trip via invoke_skill."""
    _require_synth_demo_raw()
    if skill_name == "phi-scrubbing":
        import config as cfg

        monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path / "output", raising=False)
        monkeypatch.setattr(cfg, "TMP_DIR", tmp_path / "tmp", raising=False)
        monkeypatch.setattr(cfg, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / "data" / "raw", raising=False)
        monkeypatch.setattr(cfg, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        study = FIXTURE_STUDY
    else:
        study = STUDY
    result = invoke_skill(skill_name, ["--study", study, *extra_args])
    _assert_marker_parsed(result, skill=skill_name, expect_ok=expect_ok)


def test_audit_verification_wrapper_emits_rpln_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Golden fixture tree: audit-verification verify leg exits 0 with marker."""
    import importlib.util

    import config as cfg

    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(cfg, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(cfg, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(cfg, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)

    build_golden_output_tree(
        output_root=tmp_path / "output",
        raw_root=tmp_path / "data" / "raw",
        tmp_root=tmp_path / "tmp",
        phi_scrub_yaml_path=_PHI_SCRUB_YAML,
    )

    from scripts.utils.skill_protocol import SKILL_RESULT_PREFIX, skill_run_script

    script = skill_run_script("audit-verification")
    spec = importlib.util.spec_from_file_location("audit_verification_run", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main(["--study", FIXTURE_STUDY, "--run-id", FIXTURE_RUN_ID])
    captured = capsys.readouterr()
    assert SKILL_RESULT_PREFIX in captured.out
    parsed = parse_skill_result(
        captured.out,
        skill="audit-verification",
        exit_code=rc,
    )
    _assert_marker_parsed(parsed, skill="audit-verification", expect_ok=True)
    assert rc == 0
