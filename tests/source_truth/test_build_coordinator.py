# tests/source_truth/test_build_coordinator.py
import subprocess
from pathlib import Path

import pytest

from scripts.source_truth.build import BuildCoordinatorError, run_build


def test_run_build_resolves_paths_and_creates_output_dirs(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini",
        concepts_file=fixture / "data" / "Mini" / "study_concepts.yaml",
        output_root=output_root,
        column_inventory=None,
    )
    assert (output_root / "llm_source").is_dir()
    assert (output_root / "llm_source" / "evidence_packs").is_dir()
    assert (output_root / "audit").is_dir()
    assert (output_root / "staging" / "llm_source").is_dir()


def test_run_build_blocks_on_missing_policies_dir(tmp_path):
    with pytest.raises(BuildCoordinatorError, match="policies_dir"):
        run_build(
            study="Mini",
            policies_dir=tmp_path / "does_not_exist",
            concepts_file=tmp_path / "concepts.yaml",
            output_root=tmp_path / "output",
            column_inventory=None,
        )


def test_cli_module_invocable(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python", "-m", "scripts.source_truth.build",
            "--study", "Mini",
            "--policies-dir", str(fixture / "data" / "Mini"),
            "--concepts-file", str(fixture / "data" / "Mini" / "study_concepts.yaml"),
            "--output-root", str(tmp_path / "cli_run"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}"
