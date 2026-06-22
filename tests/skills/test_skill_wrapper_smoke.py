"""Skill wrapper contract smoke tests (Wave C.8).

Parametrized subprocess invocations for the standalone skill entrypoints
that the orchestrator calls via ``skill_protocol.invoke_skill``. Each test
asserts the ``RPLN_SKILL_RESULT:`` marker is emitted and parsed (not
synthesised from exit code alone).

Indo-VAP is the sole study (Task B6 / Notes 23-28): these smoke tests no
longer read any second-study (``Synth-Demo``) raw data. The header-extraction
wrapper is exercised against the committed synthetic fixture trio
(``tests/skills/fixtures/datasets/``), seeded into the on-disk study tree for
the duration of the subprocess and removed afterwards; phi-scrubbing and
audit-verification use the in-tmp golden fixture tree.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.skills.fixtures.build_fixture import (
    FIXTURE_FORMS,
    FIXTURE_RUN_ID,
    FIXTURE_STUDY,
    build_golden_output_tree,
)

import config
from scripts.utils.skill_protocol import SKILL_RESULT_PREFIX, invoke_skill, parse_skill_result

REPO_ROOT = Path(config.BASE_DIR)
FIXTURE_DATASETS_DIR = REPO_ROOT / "tests" / "skills" / "fixtures" / "datasets"
FIXTURE_MANIFEST = REPO_ROOT / "tests" / "skills" / "fixtures" / "_forms_manifest.yaml"
_PHI_SCRUB_YAML = REPO_ROOT / "config" / "_defaults" / "phi_scrub.yaml"


@pytest.fixture
def seed_fixture_study() -> Iterator[str]:
    """Seed the committed fixture trio into the on-disk study tree.

    ``invoke_skill`` runs a real subprocess whose ``config.RAW_DATA_DIR`` /
    ``config.CONFIG_DIR`` resolve from ``config.BASE_DIR`` (no env override),
    so a monkeypatched ``tmp_path`` is invisible to the child process. To
    exercise the header-extraction wrapper against the synthetic fixture trio
    we copy the committed fixture datasets + manifest into the real on-disk
    locations the skill reads, then remove them again. All fixture data is
    fabricated and PHI-free.
    """
    datasets_dst = REPO_ROOT / "data" / "raw" / FIXTURE_STUDY / "datasets"
    config_dst = REPO_ROOT / "config" / FIXTURE_STUDY
    datasets_dst.mkdir(parents=True, exist_ok=True)
    config_dst.mkdir(parents=True, exist_ok=True)
    for form in FIXTURE_FORMS:
        shutil.copy2(FIXTURE_DATASETS_DIR / form, datasets_dst / form)
    shutil.copy2(FIXTURE_MANIFEST, config_dst / "_forms_manifest.yaml")
    try:
        yield FIXTURE_STUDY
    finally:
        shutil.rmtree(datasets_dst.parent, ignore_errors=True)
        shutil.rmtree(config_dst, ignore_errors=True)


def _assert_marker_parsed(result, *, skill: str, expect_ok: bool) -> None:
    assert result.skill == skill
    assert "no skill-result marker emitted" not in result.summary
    assert result.ok is expect_ok


def test_header_extraction_wrapper_emits_rpln_marker(
    seed_fixture_study: str,
    tmp_path: Path,
) -> None:
    """Fixture trio: header-extraction marker + exit code round-trip via invoke_skill."""
    run_dir = tmp_path / "rpln_hdr_smoke"
    result = invoke_skill(
        "header-extraction",
        ["--study", seed_fixture_study, "--run-id", "skill_smoke", "--run-dir", str(run_dir)],
    )
    _assert_marker_parsed(result, skill="header-extraction", expect_ok=True)


def test_phi_scrubbing_wrapper_emits_rpln_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden fixture tree: phi-scrubbing marker + exit code round-trip via invoke_skill."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)
    build_golden_output_tree(
        output_root=tmp_path / "output",
        raw_root=tmp_path / "data" / "raw",
        tmp_root=tmp_path / "tmp",
        phi_scrub_yaml_path=_PHI_SCRUB_YAML,
    )
    result = invoke_skill("phi-scrubbing", ["--study", FIXTURE_STUDY, "--run-id", FIXTURE_RUN_ID])
    _assert_marker_parsed(result, skill="phi-scrubbing", expect_ok=True)


def test_audit_verification_wrapper_emits_rpln_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Golden fixture tree: audit-verification verify leg exits 0 with marker."""
    import importlib.util

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)

    build_golden_output_tree(
        output_root=tmp_path / "output",
        raw_root=tmp_path / "data" / "raw",
        tmp_root=tmp_path / "tmp",
        phi_scrub_yaml_path=_PHI_SCRUB_YAML,
    )

    from scripts.utils.skill_protocol import skill_run_script

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

