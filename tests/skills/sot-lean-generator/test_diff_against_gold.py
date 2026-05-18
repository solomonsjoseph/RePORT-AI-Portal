"""Tests for scripts/source_truth/diff_against_gold.py (Phase 1.6, diff portion).

Each test builds synthetic candidate and gold YAMLs under tmp_path so no real
data is read or mutated.  The gold is placed at the standard path layout
``tmp_path/data/SoT/<study>/<form>_policy.lean.yaml`` to exercise the
path-resolution logic.

Tests invoke the CLI via subprocess so the module is exercised end-to-end
(argument parsing, file I/O, JSON output, exit codes).
"""

# ruff: noqa: S603

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[3]
CLI = str(REPO_ROOT / "scripts" / "source_truth" / "diff_against_gold.py")

STUDY = "Test-Study"
FORM = "test_form"


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def _gold_path(repo_root: Path) -> Path:
    return repo_root / "data" / "SoT" / STUDY / f"{FORM}_policy.lean.yaml"


def _run(
    candidate: Path,
    out: Path,
    repo_root: Path,
    study: str = STUDY,
    form: str = FORM,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            CLI,
            "--study", study,
            "--form", form,
            "--candidate", str(candidate),
            "--repo-root", str(repo_root),
            "--out", str(out),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


_SIMPLE_DATA: dict = {
    "form": {"title": "Test Form", "version": "v1.0"},
    "variables": {
        "VAR_A": {"section": "s1", "pdf_question": "Question A?"},
        "VAR_B": {"section": "s1", "pdf_question": "Question B?"},
    },
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_identical_inputs_empty_diff_exit_0(tmp_path: Path) -> None:
    """Identical candidate and gold → all three diff lists empty → exit 0."""
    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, _SIMPLE_DATA)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, _SIMPLE_DATA)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert diff["cosmetic"] == [], f"Expected empty cosmetic: {diff['cosmetic']}"
    assert diff["within_rule"] == [], f"Expected empty within_rule: {diff['within_rule']}"
    assert diff["novel"] == [], f"Expected empty novel: {diff['novel']}"


def test_whitespace_only_difference_cosmetic_exit_0(tmp_path: Path) -> None:
    """String value differing only in whitespace → cosmetic, not novel → exit 0."""
    gold_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "Question A?"},
        },
    }
    candidate_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "  Question A?  "},  # leading/trailing whitespace
        },
    }

    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, gold_data)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, candidate_data)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 0, (
        f"Expected exit 0 (whitespace-only); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert len(diff["cosmetic"]) >= 1, "Expected at least one cosmetic entry"
    assert diff["novel"] == [], f"Expected no novel entries: {diff['novel']}"


def test_key_order_only_difference_cosmetic_exit_0(tmp_path: Path) -> None:
    """Dict with same keys+values in different order → cosmetic, not novel → exit 0."""
    # Write raw YAML bytes directly to control key ordering (yaml.dump may reorder)
    gold_yaml = "form:\n  title: My Form\n  version: v1.0\n  page_count: 1\n"
    candidate_yaml = "form:\n  page_count: 1\n  title: My Form\n  version: v1.0\n"

    gold_p = _gold_path(tmp_path)
    gold_p.parent.mkdir(parents=True, exist_ok=True)
    gold_p.write_text(gold_yaml, encoding="utf-8")

    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(candidate_yaml, encoding="utf-8")

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 0, (
        f"Expected exit 0 (key-order-only); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert len(diff["cosmetic"]) >= 1, "Expected at least one cosmetic entry"
    assert diff["novel"] == [], f"Expected no novel entries: {diff['novel']}"


def test_value_change_novel_exit_1(tmp_path: Path) -> None:
    """A changed string value (not whitespace) → novel entry → exit 1."""
    gold_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "Original question?"},
        },
    }
    candidate_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "Changed question?"},
        },
    }

    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, gold_data)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, candidate_data)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1 (value change); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert len(diff["novel"]) >= 1, "Expected at least one novel entry"


def test_added_variable_novel_exit_1(tmp_path: Path) -> None:
    """Candidate has an extra variable not in gold → novel → exit 1."""
    gold_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "Question A?"},
        },
    }
    candidate_data = {
        "form": {"title": "My Form"},
        "variables": {
            "VAR_A": {"section": "s1", "pdf_question": "Question A?"},
            "VAR_NEW": {"section": "s1", "pdf_question": "New question?"},
        },
    }

    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, gold_data)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, candidate_data)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1 (added variable); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert len(diff["novel"]) >= 1, "Expected at least one novel entry"


def test_missing_gold_exits_2(tmp_path: Path) -> None:
    """Gold file does not exist → exit 2 with error to stderr."""
    # Do NOT write any gold file

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, _SIMPLE_DATA)

    out = tmp_path / "diff.json"
    result = _run(
        candidate,
        out,
        tmp_path,
        study="NoSuchStudy",
        form="no_such_form",
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (missing gold); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "error" in result.stderr.lower() or "not found" in result.stderr.lower(), (
        f"Expected an error message on stderr; got: {result.stderr!r}"
    )


def test_output_json_shape(tmp_path: Path) -> None:
    """Output JSON has exactly the three keys: cosmetic, within_rule, novel."""
    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, _SIMPLE_DATA)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, _SIMPLE_DATA)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 0
    assert out.exists(), "Output JSON file was not created"

    diff = json.loads(out.read_text())
    assert set(diff.keys()) == {"cosmetic", "within_rule", "novel"}, (
        f"Unexpected JSON keys: {set(diff.keys())}"
    )
    assert isinstance(diff["cosmetic"], list)
    assert isinstance(diff["within_rule"], list)
    assert isinstance(diff["novel"], list)


# I2 -------------------------------------------------------------------


def test_missing_candidate_exits_2(tmp_path: Path) -> None:
    """Candidate file does not exist → exit 2 with 'candidate' in stderr."""
    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, _SIMPLE_DATA)

    # Point candidate at a path that does not exist
    missing_candidate = tmp_path / "no_such_candidate.yaml"
    out = tmp_path / "diff.json"
    result = _run(missing_candidate, out, tmp_path)

    assert result.returncode == 2, (
        f"Expected exit 2 (missing candidate); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "candidate" in result.stderr.lower(), (
        f"Expected 'candidate' in stderr; got: {result.stderr!r}"
    )


def test_invalid_yaml_exits_2(tmp_path: Path) -> None:
    """Malformed candidate YAML → exit 2."""
    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, _SIMPLE_DATA)

    candidate = tmp_path / "bad.yaml"
    # Genuinely malformed YAML — unclosed flow sequence
    candidate.write_text("foo: [unclosed\n", encoding="utf-8")

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 2, (
        f"Expected exit 2 (invalid YAML); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "error" in result.stderr.lower(), (
        f"Expected 'error' in stderr; got: {result.stderr!r}"
    )


# I3 -------------------------------------------------------------------


def test_newline_difference_is_novel_not_cosmetic(tmp_path: Path) -> None:
    """A multi-line gold string vs single-line candidate is novel, not cosmetic.

    'line one\\nline two' vs 'line one line two' should NOT be whitespace-only
    because newlines carry semantic meaning in clinical form questions.
    """
    gold_data = {
        "variables": {
            "VAR_A": {"pdf_question": "line one\nline two"},
        }
    }
    candidate_data = {
        "variables": {
            "VAR_A": {"pdf_question": "line one line two"},
        }
    }

    gold_p = _gold_path(tmp_path)
    _write_yaml(gold_p, gold_data)

    candidate = tmp_path / "candidate.yaml"
    _write_yaml(candidate, candidate_data)

    out = tmp_path / "diff.json"
    result = _run(candidate, out, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1 (novel diff: newline vs space); got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    diff = json.loads(out.read_text())
    assert len(diff["novel"]) >= 1, (
        f"Expected at least one novel entry for newline vs space diff; got: {diff['novel']}"
    )
    # Confirm the cosmetic list does NOT contain an entry for this path
    cosmetic_paths = {e["path"] for e in diff["cosmetic"]}
    assert "variables.VAR_A.pdf_question" not in cosmetic_paths, (
        f"Newline diff was wrongly classified cosmetic: {diff['cosmetic']}"
    )
