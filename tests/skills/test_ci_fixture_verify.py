"""CI guard: verifier against the synthetic 3-form fixture trio.

These tests are the primary regression guard for extract_to_llm_source's
verify subcommand. They run on every PR touching the skill's surfaces via
.github/workflows/skill_verify.yml (which triggers on the same paths and
delegates to pytest).

Happy path
----------
One test builds a complete golden output tree (satisfying all 12 assertions)
and asserts verify exits 0 with overall="pass".

Fail-injection matrix
---------------------
Six mutation cases each assert a specific non-zero exit code:

  null_hash          — scrub_config_hash nulled in ledger        → EXIT_LEDGER_HASH_NULL (3)
  missing_jsonl      — one required JSONL removed                 → EXIT_MANIFEST_MISMATCH (2)
  planted_phi        — Aadhaar string injected into llm_source/  → EXIT_VERIFIER_FAIL (5)
  leftover_staging   — staging dir left behind                   → EXIT_DESTRUCTION_INCOMPLETE (7)
  leftover_lock      — pipeline lock file present                → EXIT_NEEDS_ADVICE (6)
  missing_attestation — destruction_attestation.json removed     → EXIT_DESTRUCTION_INCOMPLETE (7)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.skills.extract_to_llm_source import (
    EXIT_DESTRUCTION_INCOMPLETE,
    EXIT_LEDGER_HASH_NULL,
    EXIT_MANIFEST_MISMATCH,
    EXIT_NEEDS_ADVICE,
    EXIT_OK,
    EXIT_VERIFIER_FAIL,
    main,
)
from tests.skills.fixtures.build_fixture import (
    FIXTURE_FORMS,
    FIXTURE_RUN_ID,
    FIXTURE_STUDY,
    build_golden_output_tree,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PHI_SCRUB_YAML = _REPO_ROOT / "scripts" / "security" / "phi_scrub.yaml"


# ---------------------------------------------------------------------------
# Config redirect helper
# ---------------------------------------------------------------------------


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect config path constants into tmp_path so tests are hermetic."""
    import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------


class TestFixtureVerifyHappyPath:
    """Verifier exits 0 against an intact golden output tree."""

    def test_verify_exits_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, tmp_path)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        rc = main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        assert rc == EXIT_OK, f"Expected exit 0, got {rc}"

    def test_verifier_report_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, tmp_path)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        report = (
            tmp_path / "output" / FIXTURE_STUDY / "runs" / FIXTURE_RUN_ID / "verifier_report.json"
        )
        assert report.exists(), "verifier_report.json must be written"
        data = json.loads(report.read_text())
        assert data["overall"] == "pass"
        assert data["exit_code"] == EXIT_OK
        assert len(data["assertions"]) == 12

    def test_all_12_assertions_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, tmp_path)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        report = (
            tmp_path / "output" / FIXTURE_STUDY / "runs" / FIXTURE_RUN_ID / "verifier_report.json"
        )
        data = json.loads(report.read_text())
        failures = [a for a in data["assertions"] if a["result"] == "fail"]
        assert not failures, f"Unexpected failures: {failures}"

    def test_status_json_updated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, tmp_path)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        status = tmp_path / "output" / FIXTURE_STUDY / "runs" / FIXTURE_RUN_ID / "status.json"
        data = json.loads(status.read_text())
        assert data["verifier_passed"] is True


# ---------------------------------------------------------------------------
# Fail-injection matrix
# ---------------------------------------------------------------------------

# Each entry: (mutation_id, description, mutation_fn, expected_exit_code)
# The mutation_fn receives the paths dict returned by build_golden_output_tree.

_FAIL_CASES = [
    (
        "null_hash",
        "scrub_config_hash nulled in ledger → EXIT_LEDGER_HASH_NULL (3)",
        EXIT_LEDGER_HASH_NULL,
    ),
    (
        "missing_jsonl",
        "required JSONL removed from llm_source/datasets/ → EXIT_MANIFEST_MISMATCH (2)",
        EXIT_MANIFEST_MISMATCH,
    ),
    (
        "planted_phi",
        "Aadhaar string in llm_source/ → EXIT_VERIFIER_FAIL (5)",
        EXIT_VERIFIER_FAIL,
    ),
    (
        "leftover_staging",
        "staging dir present → EXIT_DESTRUCTION_INCOMPLETE (7)",
        EXIT_DESTRUCTION_INCOMPLETE,
    ),
    (
        "leftover_lock",
        "pipeline lock file present → EXIT_NEEDS_ADVICE (6)",
        EXIT_NEEDS_ADVICE,
    ),
    (
        "missing_attestation",
        "destruction_attestation.json removed → EXIT_DESTRUCTION_INCOMPLETE (7)",
        EXIT_DESTRUCTION_INCOMPLETE,
    ),
]


def _apply_mutation(mutation_id: str, paths: dict[str, Path], tmp_root: Path) -> None:
    """Apply the named mutation to the golden output tree."""
    if mutation_id == "null_hash":
        # Null the scrub_config_hash in the ledger.
        ledger_path = paths["audit_dir"] / "phi_handling_ledger.as_written.json"
        data = json.loads(ledger_path.read_text())
        data["scrub_config_hash"] = None
        ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    elif mutation_id == "missing_jsonl":
        # Remove the first required form's JSONL.
        stem = Path(FIXTURE_FORMS[0]).stem
        jsonl = paths["llm_source_dir"] / "datasets" / f"{stem}.jsonl"
        jsonl.unlink()

    elif mutation_id == "planted_phi":
        # Plant a fake Aadhaar number (12-digit space-separated) into a JSONL.
        stem = Path(FIXTURE_FORMS[0]).stem
        jsonl = paths["llm_source_dir"] / "datasets" / f"{stem}.jsonl"
        phi_row = {"subject_id_pseudonym": "1234 5678 9012"}  # Aadhaar pattern
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(phi_row) + "\n")

    elif mutation_id == "leftover_staging":
        # Create the staging dir to simulate an incomplete destruction.
        paths["staging_dir"].mkdir(parents=True, exist_ok=True)

    elif mutation_id == "leftover_lock":
        # Create the pipeline lock file.
        tmp_root.mkdir(parents=True, exist_ok=True)
        lock = tmp_root / f".{FIXTURE_STUDY}.pipeline.lock"
        lock.write_text(f"pid=99999\nstudy={FIXTURE_STUDY}\n", encoding="utf-8")

    elif mutation_id == "missing_attestation":
        # Remove the destruction attestation.
        attest = paths["run_dir"] / "destruction_attestation.json"
        attest.unlink()

    else:
        raise ValueError(f"Unknown mutation_id: {mutation_id!r}")


@pytest.mark.parametrize(
    "mutation_id,description,expected_exit",
    _FAIL_CASES,
    ids=[c[0] for c in _FAIL_CASES],
)
class TestFixtureVerifyFailInjection:
    """Each mutation trips the expected non-zero exit code."""

    def test_mutated_fixture_trips_correct_exit(
        self,
        mutation_id: str,
        description: str,
        expected_exit: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        tmp_root = tmp_path / "tmp"
        paths = build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_root,
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        _apply_mutation(mutation_id, paths, tmp_root)
        rc = main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        assert rc == expected_exit, (
            f"Mutation {mutation_id!r} ({description}): expected exit {expected_exit}, got {rc}"
        )

    def test_mutated_fixture_verifier_report_shows_fail(
        self,
        mutation_id: str,
        description: str,
        expected_exit: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """verifier_report.json must record overall=fail after each mutation."""
        _patch_config(monkeypatch, tmp_path)
        tmp_root = tmp_path / "tmp"
        paths = build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_root,
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        _apply_mutation(mutation_id, paths, tmp_root)
        main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        report = (
            tmp_path / "output" / FIXTURE_STUDY / "runs" / FIXTURE_RUN_ID / "verifier_report.json"
        )
        assert report.exists(), "verifier_report.json must be written even on failure"
        data = json.loads(report.read_text())
        assert data["overall"] == "fail", (
            f"Mutation {mutation_id!r}: expected overall=fail in report"
        )
