"""CI guard: verifier against the synthetic 3-form fixture trio.

These tests are the primary regression guard for extract_to_llm_source's
verify subcommand. They run on every PR touching the skill's surfaces via
.github/workflows/skill_verify.yml (which triggers on the same paths and
delegates to pytest).

Happy path
----------
One test builds a complete golden output tree (satisfying all 14 assertions)
and asserts verify exits 0 with overall="pass".

Fail-injection matrix
---------------------
Six mutation cases each assert a specific non-zero exit code:

  null_hash          — scrub_config_hash nulled in ledger        → EXIT_LEDGER_HASH_NULL (3)
  missing_jsonl      — one required JSONL removed                 → EXIT_MANIFEST_MISMATCH (2)
  planted_phi        — Aadhaar string injected into dataset files → EXIT_VERIFIER_FAIL (5)
  leftover_staging   — staging dir left behind                   → EXIT_DESTRUCTION_INCOMPLETE (7)
  leftover_lock      — pipeline lock file present                → EXIT_NEEDS_ADVICE (6)
  missing_attestation — destruction_attestation.json removed     → EXIT_DESTRUCTION_INCOMPLETE (7)

Assertion 14 (non-vacuous)
--------------------------
The golden fixture writes a phi_handling_approval.json and ledger keep_decisions
so assertion 14 actually fires.  A separate test removes a published column's
ledger coverage and asserts that assertion 14 fails with EXIT_AUDIT_COVERAGE_INCOMPLETE.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.skills.extract_to_llm_source import (
    EXIT_AUDIT_COVERAGE_INCOMPLETE,
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
        assert len(data["assertions"]) == 14

    def test_all_14_assertions_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
# Assertion 14 — non-vacuous coverage tests (finding #10)
# ---------------------------------------------------------------------------


class TestAssertion14NonVacuous:
    """Assertion 14 (ledger_covers_all_columns) must fire and pass in the golden fixture.

    The golden fixture writes phi_handling_approval.json and per-column
    keep_decisions so assertion 14 actually evaluates coverage rather than
    short-circuiting on "no approval file".  These tests verify:

    1. The golden fixture includes a phi_handling_approval.json.
    2. The golden fixture passes assertion 14 (all columns accounted).
    3. Removing a published column's ledger coverage trips assertion 14 with
       EXIT_AUDIT_COVERAGE_INCOMPLETE — the assertion is NOT vacuous.
    """

    def test_golden_fixture_includes_phi_handling_approval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Golden fixture must write phi_handling_approval.json so assertion 14 can fire."""
        _patch_config(monkeypatch, tmp_path)
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        approval_path = (
            tmp_path
            / "output"
            / FIXTURE_STUDY
            / "runs"
            / FIXTURE_RUN_ID
            / "phi_handling_approval.json"
        )
        assert approval_path.is_file(), (
            "phi_handling_approval.json must be written by build_golden_output_tree"
        )
        data = json.loads(approval_path.read_text())
        assert data.get("approved_forms"), "approved_forms must be non-empty"
        assert set(data["approved_forms"]) == set(FIXTURE_FORMS), (
            "all fixture forms must be in approved_forms"
        )

    def test_assertion_14_passes_with_full_ledger_coverage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Assertion 14 must pass when every published column has a ledger entry."""
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
        a14 = next(
            (a for a in data["assertions"] if a.get("name") == "ledger_covers_all_columns"),
            None,
        )
        assert a14 is not None, "assertion 14 (ledger_covers_all_columns) must be in report"
        assert a14["result"] == "pass", f"assertion 14 must pass on the golden fixture; got: {a14}"

    def test_removing_column_ledger_coverage_trips_assertion_14(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removing a column from the ledger must trip assertion 14 — coverage is not vacuous.

        This is the key regression guard: if assertion 14 short-circuits on "no
        approval file" then removing ledger entries would not be detected.  After
        this fix, the fixture has an approval file and real keep_decisions, so a
        removed keep_decision must fail assertion 14 with EXIT_AUDIT_COVERAGE_INCOMPLETE.
        """
        _patch_config(monkeypatch, tmp_path)
        audit_dir = tmp_path / "output" / FIXTURE_STUDY / "audit"
        build_golden_output_tree(
            output_root=tmp_path / "output",
            raw_root=tmp_path / "data" / "raw",
            tmp_root=tmp_path / "tmp",
            phi_scrub_yaml_path=_PHI_SCRUB_YAML,
        )
        # Remove all keep_decisions from the first form's ledger — the published
        # columns will have no ledger accounting, which must trip assertion 14.
        ledger_path = dataset_phi_ledger_path(audit_dir, FIXTURE_FORMS[0])
        ledger_data = json.loads(ledger_path.read_text())
        ledger_data.pop("keep_decisions", None)
        ledger_path.write_text(json.dumps(ledger_data, indent=2), encoding="utf-8")

        rc = main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
        assert rc == EXIT_AUDIT_COVERAGE_INCOMPLETE, (
            f"Expected EXIT_AUDIT_COVERAGE_INCOMPLETE ({EXIT_AUDIT_COVERAGE_INCOMPLETE}), "
            f"got {rc} — assertion 14 was not exercised non-vacuously"
        )


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
        "required JSONL removed from llm_source/dataset_schema/files/ → EXIT_MANIFEST_MISMATCH (2)",
        EXIT_MANIFEST_MISMATCH,
    ),
    (
        "planted_phi",
        "Aadhaar string in dataset files → EXIT_VERIFIER_FAIL (5)",
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
        ledger_path = dataset_phi_ledger_path(paths["audit_dir"], FIXTURE_FORMS[0])
        data = json.loads(ledger_path.read_text())
        data["scrub_config_hash"] = None
        ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    elif mutation_id == "missing_jsonl":
        # Remove the first required form's JSONL.
        stem = Path(FIXTURE_FORMS[0]).stem
        jsonl = paths["llm_source_dir"] / "dataset_schema" / "files" / f"{stem}.jsonl"
        jsonl.unlink()

    elif mutation_id == "planted_phi":
        # Plant a fake Aadhaar number (12-digit space-separated) into a JSONL.
        stem = Path(FIXTURE_FORMS[0]).stem
        jsonl = paths["llm_source_dir"] / "dataset_schema" / "files" / f"{stem}.jsonl"
        phi_row = {"subject_id_pseudonym": "2345 6789 0124"}  # Aadhaar pattern
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
