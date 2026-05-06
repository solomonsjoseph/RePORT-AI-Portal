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


def test_run_build_emits_catalog_and_evidence_packs(tmp_path):
    import json
    fixture = Path("tests/fixtures/build_mini").resolve()
    output_root = tmp_path / "output" / "Mini"
    run_build(
        study="Mini",
        policies_dir=fixture / "data" / "Mini",
        concepts_file=fixture / "data" / "Mini" / "study_concepts.yaml",
        output_root=output_root,
        column_inventory=None,
    )

    catalog_path = output_root / "llm_source" / "study_metadata_catalog.json"
    assert catalog_path.is_file()
    catalog = json.loads(catalog_path.read_text())
    assert catalog["artifact_type"] == "study_metadata_catalog"
    assert isinstance(catalog["compact_records"], list)
    assert len(catalog["compact_records"]) > 0

    evidence_dir = output_root / "llm_source" / "evidence_packs"
    pack_files = list(evidence_dir.glob("*.json"))
    assert len(pack_files) > 0
    sample = json.loads(pack_files[0].read_text())
    assert sample["variable_id"] == pack_files[0].stem


def test_run_build_idempotent_byte_identical(tmp_path):
    fixture = Path("tests/fixtures/build_mini").resolve()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    for out in (out_a, out_b):
        run_build(
            study="Mini",
            policies_dir=fixture / "data" / "Mini",
            concepts_file=fixture / "data" / "Mini" / "study_concepts.yaml",
            output_root=out,
            column_inventory=None,
        )

    cat_a = (out_a / "llm_source" / "study_metadata_catalog.json").read_bytes()
    cat_b = (out_b / "llm_source" / "study_metadata_catalog.json").read_bytes()
    assert cat_a == cat_b
