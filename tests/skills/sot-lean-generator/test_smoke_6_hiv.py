"""Smoke tests for the sot-lean-generator skill wired via study_intake CLI.

Test 1 — Stage 0 (source-pack generation):
    Runs ``python -m scripts.source_truth.study_intake`` via subprocess for the
    Indo-VAP / 6_HIV form and asserts the source pack JSON and a render PNG are
    written to the expected /tmp paths.

Test 2 — Stage 4 (lean policy verification):
    Runs ``check_lean_policy.py`` against the reference lean YAML and the source
    pack produced by Test 1, and asserts exit code 0.

Both tests are skipped when their required input files are absent so the suite
can run cleanly in CI environments that do not have the raw data.
"""

# ruff: noqa: S108, S603, S607

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[3]
RAW_PDF = REPO_ROOT / "data" / "raw" / "Indo-VAP" / "annotated_pdfs" / "6 HIV v1.0.pdf"
SOURCE_PACK = Path("/tmp/sot_source_pack_6_HIV.json")
RENDER_DIR = Path("/tmp/sot_render_6_HIV")
LEAN_YAML = REPO_ROOT / "data" / "SoT" / "Indo-VAP" / "6_HIV_policy.lean.yaml"
CHECK_SCRIPT = REPO_ROOT / "skills" / "sot-lean-generator" / "scripts" / "check_lean_policy.py"
GENERATOR_SCRIPT = REPO_ROOT / "skills" / "sot-lean-generator" / "scripts" / "generate_pdf_aware_candidate.py"


@pytest.mark.skipif(
    not RAW_PDF.exists(),
    reason="raw PDF not present: data/raw/Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
)
def test_stage0_source_pack() -> None:
    """Stage 0: study_intake wrapper produces source pack JSON and render PNG."""
    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            "-m", "scripts.source_truth.study_intake",
            "--study", "Indo-VAP",
            "--form", "6_HIV",
            "--repo-root", str(REPO_ROOT),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"study_intake exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    assert SOURCE_PACK.exists(), f"source pack not written: {SOURCE_PACK}"

    pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    assert "headers" in pack, "source pack missing 'headers' key"
    assert len(pack["headers"]) > 0, "source pack has no headers"
    assert "pdf_sha256" in pack, "source pack missing 'pdf_sha256' key"
    assert len(pack["pdf_sha256"]) == 64, "source pack pdf_sha256 is not a SHA-256 hex digest"
    assert "renders" in pack, "source pack missing 'renders' key"
    assert len(pack["renders"]) == pack["page_count"], "source pack must render every PDF page"
    assert pack["screenshot"] == pack["renders"][0], "screenshot must remain first-render compatibility alias"
    assert "header_duplicates" in pack, "source pack missing duplicate-header summary"
    assert "annotation_duplicates" in pack, "source pack missing duplicate-annotation summary"

    pngs = list(RENDER_DIR.glob("*.png")) if RENDER_DIR.is_dir() else []
    assert len(pngs) == pack["page_count"], (
        f"no render PNG found under {RENDER_DIR}; "
        "check that ghostscript (gs) is installed"
    )


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_lean_verify() -> None:
    """Stage 4: check_lean_policy passes against the reference lean YAML."""
    assert LEAN_YAML.exists(), f"reference lean YAML not found: {LEAN_YAML}"
    assert CHECK_SCRIPT.exists(), f"check_lean_policy.py not found: {CHECK_SCRIPT}"

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(LEAN_YAML),
            "--source-pack", str(SOURCE_PACK),
            "--repo-root", str(REPO_ROOT),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_lean_policy.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "passed" in result.stdout.lower(), (
        f"expected 'passed' in output, got: {result.stdout!r}"
    )


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_pdf_sha_mismatch_exits_2(tmp_path: Path) -> None:
    """Stage 4 reports stale source packs with the documented SHA mismatch code."""
    assert LEAN_YAML.exists(), f"reference lean YAML not found: {LEAN_YAML}"
    assert CHECK_SCRIPT.exists(), f"check_lean_policy.py not found: {CHECK_SCRIPT}"

    stale_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    stale_pack["pdf_sha256"] = "0" * 64
    stale_pack_path = tmp_path / "sot_source_pack_6_HIV_stale_sha.json"
    stale_pack_path.write_text(json.dumps(stale_pack), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(LEAN_YAML),
            "--source-pack", str(stale_pack_path),
            "--repo-root", str(REPO_ROOT),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "SHA mismatch" in result.stderr


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_rejects_generic_annotation_placeholders(tmp_path: Path) -> None:
    """Stage 4 rejects locator placeholders that are not printed-form signal."""
    assert LEAN_YAML.exists(), f"reference lean YAML not found: {LEAN_YAML}"
    assert CHECK_SCRIPT.exists(), f"check_lean_policy.py not found: {CHECK_SCRIPT}"

    bad = yaml.safe_load(LEAN_YAML.read_text(encoding="utf-8"))
    bad["variables"]["HIV_VISIT"]["pdf_question"] = (
        "Visible printed field associated with PDF annotation HIV_VISIT"
    )
    bad_path = tmp_path / "6_HIV_generic_placeholder.lean.yaml"
    bad_path.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(bad_path),
            "--source-pack", str(SOURCE_PACK),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "generic annotation placeholder" in result.stderr


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_rejects_duplicate_dataset_headers(tmp_path: Path) -> None:
    """Stage 4 reports undocumented duplicate row-1 headers as unsafe."""
    assert LEAN_YAML.exists(), f"reference lean YAML not found: {LEAN_YAML}"
    assert CHECK_SCRIPT.exists(), f"check_lean_policy.py not found: {CHECK_SCRIPT}"

    bad_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    bad_pack["headers"] = [*bad_pack["headers"], "SUBJID"]
    bad_pack_path = tmp_path / "sot_source_pack_6_HIV_duplicate_headers.json"
    bad_pack_path.write_text(json.dumps(bad_pack), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(LEAN_YAML),
            "--source-pack", str(bad_pack_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "duplicate binding names" in result.stderr


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_accepts_documented_duplicate_header_collapse(tmp_path: Path) -> None:
    """Stage 4 allows duplicate source headers only when final collapse is documented."""
    assert LEAN_YAML.exists(), f"reference lean YAML not found: {LEAN_YAML}"
    assert CHECK_SCRIPT.exists(), f"check_lean_policy.py not found: {CHECK_SCRIPT}"

    duplicate_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    duplicate_pack["headers"] = [*duplicate_pack["headers"], "SUBJID"]
    duplicate_pack_path = tmp_path / "sot_source_pack_6_HIV_duplicate_headers.json"
    duplicate_pack_path.write_text(json.dumps(duplicate_pack), encoding="utf-8")

    combined = yaml.safe_load(LEAN_YAML.read_text(encoding="utf-8"))
    combined.setdefault("discrepancies", []).append(
        {
            "kind": "dataset_duplicate_header_combined_binding",
            "where": "dataset row-1 headers",
            "pdf_annotation_says": None,
            "printed_form_truth": "Duplicate source columns compile to the same final subject identifier binding",
            "dataset_column_binding": ["SUBJID"],
            "resolution": "Source-level duplicate reviewed during compile; final lean keeps one combined SUBJID variable",
        }
    )
    combined_path = tmp_path / "6_HIV_combined_duplicate_header.lean.yaml"
    combined_path.write_text(yaml.safe_dump(combined, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(combined_path),
            "--source-pack", str(duplicate_pack_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_lean_policy.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_rejects_unreconciled_variable_like_pdf_annotation(tmp_path: Path) -> None:
    """Stage 4 requires variable-like PDF annotation labels to be reconciled."""
    bad_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    bad_pack["pages"][0]["annotations"].append("HIV_FAKEFIELD")
    bad_pack_path = tmp_path / "sot_source_pack_6_HIV_unreconciled_annotation.json"
    bad_pack_path.write_text(json.dumps(bad_pack), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(LEAN_YAML),
            "--source-pack", str(bad_pack_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not reconciled as an alias, non-variable label, or printed-widget discrepancy" in result.stderr


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_accepts_pdf_annotation_alias_to_dataset_header(tmp_path: Path) -> None:
    """Stage 4 accepts a documented annotation alias to an existing dataset header."""
    alias_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    alias_pack["pages"][0]["annotations"].append("HIV_VISITTYPO")
    alias_pack_path = tmp_path / "sot_source_pack_6_HIV_alias_annotation.json"
    alias_pack_path.write_text(json.dumps(alias_pack), encoding="utf-8")

    aliased = yaml.safe_load(LEAN_YAML.read_text(encoding="utf-8"))
    aliased.setdefault("discrepancies", []).append(
        {
            "kind": "pdf_annotation_alias_to_dataset_header",
            "where": "PDF annotations",
            "pdf_annotation_says": [
                {"label": "HIV_VISITTYPO", "dataset_column": "HIV_VISIT"}
            ],
            "printed_form_truth": "Annotation label is a locator typo for the printed visit field",
            "dataset_column_binding": ["HIV_VISIT"],
            "resolution": "Dataset row-1 header retained as the variable key",
        }
    )
    aliased_path = tmp_path / "6_HIV_alias_annotation.lean.yaml"
    aliased_path.write_text(yaml.safe_dump(aliased, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(aliased_path),
            "--source-pack", str(alias_pack_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_lean_policy.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_stage4_accepts_documented_printed_widget_without_dataset_header(tmp_path: Path) -> None:
    """Stage 4 accepts real PDF widgets without headers only when documented."""
    missing_pack = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    missing_pack["pages"][0]["annotations"].append("HIV_UNBOUND_FIELD")
    missing_pack_path = tmp_path / "sot_source_pack_6_HIV_printed_widget_without_header.json"
    missing_pack_path.write_text(json.dumps(missing_pack), encoding="utf-8")

    documented = yaml.safe_load(LEAN_YAML.read_text(encoding="utf-8"))
    documented.setdefault("discrepancies", []).append(
        {
            "kind": "printed_widget_without_dataset_header",
            "where": "PDF annotations",
            "pdf_annotation_says": ["HIV_UNBOUND_FIELD"],
            "printed_form_truth": "PDF annotation identifies a real printed data-entry field with no row-1 header",
            "dataset_column_binding": None,
            "resolution": "Documented source/dataset discrepancy; no lean variable added without a dataset binding key",
        }
    )
    documented_path = tmp_path / "6_HIV_printed_widget_without_header.lean.yaml"
    documented_path.write_text(yaml.safe_dump(documented, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(documented_path),
            "--source-pack", str(missing_pack_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_lean_policy.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


@pytest.mark.skipif(
    not SOURCE_PACK.exists(),
    reason="source pack not present at /tmp/sot_source_pack_6_HIV.json (run test_stage0_source_pack first)",
)
def test_pdf_aware_generator_preserves_6_hiv_calibration(tmp_path: Path) -> None:
    """A fresh generated 6_HIV candidate preserves terminal skip and mutex rules."""
    out = tmp_path / "6_HIV_policy.lean.yaml"
    result = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(GENERATOR_SCRIPT),
            "--repo-root", str(REPO_ROOT),
            "--form", "6_HIV",
            "--source-pack", str(SOURCE_PACK),
            "--out", str(out),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generate_pdf_aware_candidate.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    verify = subprocess.run(
        [
            "uv", "run", "--all-groups", "python",
            str(CHECK_SCRIPT),
            "--lean", str(out),
            "--source-pack", str(SOURCE_PACK),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, (
        f"check_lean_policy.py exited {verify.returncode}\n"
        f"stdout: {verify.stdout}\n"
        f"stderr: {verify.stderr}"
    )
