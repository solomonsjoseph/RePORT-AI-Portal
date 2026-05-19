"""Build the synthetic fixture trio for CI verifier tests.

This script generates:
  - Three minimal .xlsx files under tests/skills/fixtures/datasets/
  - A _forms_manifest.yaml listing them as required
  - A golden output tree under tests/skills/fixtures/golden_output/ that
    satisfies all 12 verifier assertions (except the phi_scrub.yaml hash,
    which is computed dynamically at build time from the real file).

Usage
-----
Run once to regenerate the committed .xlsx fixtures:

    uv run --all-groups python tests/skills/fixtures/build_fixture.py

Or import :func:`build_golden_output_tree` from tests to construct the tree
into a tmp_path during pytest execution:

    from tests.skills.fixtures.build_fixture import build_golden_output_tree
    build_golden_output_tree(tmp_path, phi_scrub_yaml_path)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURE_STUDY = "fixture"
FIXTURE_RUN_ID = "run_fixture_001"

# Three forms that constitute the fixture trio.
FIXTURE_FORMS = [
    "01_demographics.xlsx",
    "02_visit.xlsx",
    "03_outcome.xlsx",
]

# Data rows for each form (non-PHI, minimal columns).
FIXTURE_FORM_DATA: dict[str, list[dict[str, Any]]] = {
    "01_demographics": [
        {"subject_id_pseudonym": "subj_001", "age_group": "30-40", "site": "site_A"},
        {"subject_id_pseudonym": "subj_002", "age_group": "50-60", "site": "site_B"},
    ],
    "02_visit": [
        {"subject_id_pseudonym": "subj_001", "visit_day": 1, "temperature_c": 37.2},
        {"subject_id_pseudonym": "subj_002", "visit_day": 3, "temperature_c": 38.1},
    ],
    "03_outcome": [
        {"subject_id_pseudonym": "subj_001", "icu_days": 5, "outcome": "discharged"},
        {"subject_id_pseudonym": "subj_002", "icu_days": 12, "outcome": "discharged"},
    ],
}

# Corresponding JSONL rows for llm_source/datasets/ (PHI-free representations).
FIXTURE_JSONL_ROWS: dict[str, list[dict[str, Any]]] = FIXTURE_FORM_DATA


# ---------------------------------------------------------------------------
# .xlsx builder
# ---------------------------------------------------------------------------


def _build_xlsx_files(fixtures_dir: Path) -> None:
    """Write three minimal .xlsx files to fixtures_dir/datasets/."""
    try:
        import openpyxl
    except ImportError:
        print("openpyxl not available — skipping .xlsx generation", file=sys.stderr)
        return

    datasets_dir = fixtures_dir / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    for form_name, rows in FIXTURE_FORM_DATA.items():
        xlsx_path = datasets_dir / f"{form_name}.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        if ws is None:
            ws = wb.create_sheet()

        # Header row
        headers = list(rows[0].keys())
        ws.append(headers)  # type: ignore[arg-type]

        # Data rows
        for row in rows:
            ws.append([row[h] for h in headers])  # type: ignore[arg-type]

        wb.save(xlsx_path)
        print(f"  wrote {xlsx_path.relative_to(fixtures_dir.parent.parent)}")


# ---------------------------------------------------------------------------
# _forms_manifest.yaml builder
# ---------------------------------------------------------------------------


def _build_manifest(fixtures_dir: Path) -> None:
    """Write _forms_manifest.yaml to fixtures_dir/."""
    manifest = {
        "required": FIXTURE_FORMS,
        "optional": [],
        "reject": [],
    }
    manifest_path = fixtures_dir / "_forms_manifest.yaml"
    manifest_path.write_text(yaml.dump(manifest, sort_keys=True), encoding="utf-8")
    print(f"  wrote {manifest_path.name}")


# ---------------------------------------------------------------------------
# Golden output tree builder
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(payload, indent=2, sort_keys=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}_", suffix=".tmp")
    try:
        try:
            os.write(tmp_fd, serialised.encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        Path(tmp_name).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def build_golden_output_tree(
    output_root: Path,
    raw_root: Path,
    tmp_root: Path,
    phi_scrub_yaml_path: Path,
    *,
    run_id: str = FIXTURE_RUN_ID,
    study: str = FIXTURE_STUDY,
    forms: list[str] | None = None,
) -> dict[str, Path]:
    """Construct a complete golden output tree that satisfies all 12 verifier assertions.

    Populates:
      - output_root/{study}/runs/{run_id}/destruction_attestation.json
      - output_root/{study}/runs/{run_id}/status.json
      - output_root/{study}/audit/phi_handling_ledger.as_written.json
      - output_root/{study}/audit/.NO_LLM_ZONE
      - output_root/{study}/llm_source/datasets/{stem}.jsonl  (one per form)
      - raw_root/{study}/datasets/{form_name}.xlsx  (stub bytes)
      - raw_root/{study}/_forms_manifest.yaml

    Staging dir (tmp_root/{study}/) is intentionally NOT created (assertion 3).

    Returns a dict of named paths for mutation in fail-injection tests.
    """
    if forms is None:
        forms = FIXTURE_FORMS

    study_output_dir = output_root / study
    study_raw_dir = raw_root / study
    datasets_dir = study_raw_dir / "datasets"
    run_dir = study_output_dir / "runs" / run_id
    audit_dir = study_output_dir / "audit"
    llm_source_dir = study_output_dir / "llm_source"

    # ── a. phi_scrub.yaml hash ───────────────────────────────────────────────
    scrub_config_hash = hashlib.sha256(phi_scrub_yaml_path.read_bytes()).hexdigest()

    # ── b. Manifest + stub datasets dir ─────────────────────────────────────
    manifest_data = {"required": forms, "optional": [], "reject": []}
    study_raw_dir.mkdir(parents=True, exist_ok=True)
    (study_raw_dir / "_forms_manifest.yaml").write_text(
        yaml.dump(manifest_data, sort_keys=True), encoding="utf-8"
    )
    datasets_dir.mkdir(parents=True, exist_ok=True)
    for form in forms:
        stub = datasets_dir / form
        if not stub.exists():
            stub.write_bytes(b"PK\x03\x04stub-xlsx-fixture")  # minimal xlsx magic

    # ── c. Audit: ledger + .NO_LLM_ZONE ─────────────────────────────────────
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger_payload: dict[str, Any] = {
        "run_id": run_id,
        "scrub_config_hash": scrub_config_hash,
        "input_dataset_hash": "deadbeef" * 8,  # 64-char placeholder hash
    }
    _atomic_write_json(audit_dir / "phi_handling_ledger.as_written.json", ledger_payload)
    (audit_dir / ".NO_LLM_ZONE").write_text(
        "This directory is outside the LLM read zone.\n", encoding="utf-8"
    )

    # ── d. Destruction attestation ───────────────────────────────────────────
    run_dir.mkdir(parents=True, exist_ok=True)
    attest_payload: dict[str, Any] = {
        "apfs_cow_disclaimer": (
            "Filesystem-level overwrite was performed via secrets.token_bytes + fsync; "
            "APFS copy-on-write means prior blocks may persist until trimmed. "
            "Skill scope is operational untraceability, not forensic erasure."
        ),
        "completed_utc": _iso_now(),
        "cryptographic_erasure": False,
        "files_destroyed": 3,
        "removed_paths": [
            "datasets/01_demographics.xlsx",
            "datasets/02_visit.xlsx",
            "datasets/03_outcome.xlsx",
        ],
        "run_id": run_id,
        "staging_path": str(tmp_root / study),
        "started_utc": _iso_now(),
        "study": study,
    }
    _atomic_write_json(run_dir / "destruction_attestation.json", attest_payload)

    # ── e. llm_source/datasets/ JSONLs ──────────────────────────────────────
    datasets_out = llm_source_dir / "datasets"
    datasets_out.mkdir(parents=True, exist_ok=True)
    for form in forms:
        stem = Path(form).stem
        jsonl_rows = FIXTURE_JSONL_ROWS.get(stem, [{"col_a": "val_a", "col_b": "val_b"}])
        jsonl_path = datasets_out / f"{stem}.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for row in jsonl_rows:
                fh.write(json.dumps(row) + "\n")

    # ── f. status.json (verifier_passed = None initially) ───────────────────
    status_payload: dict[str, Any] = {
        "completed_utc": _iso_now(),
        "destruction_attestation_path": str(run_dir / "destruction_attestation.json"),
        "exit_code": 0,
        "ledger_hash_present": True,
        "run_id": run_id,
        "scope": "HIPAA Safe Harbor (per phi_scrub.yaml)",
        "started_utc": _iso_now(),
        "study": study,
        "verifier_passed": None,
    }
    _atomic_write_json(run_dir / "status.json", status_payload)

    # ── g. No staging dir (assertion 3 must pass) ───────────────────────────
    staging_dir = tmp_root / study
    assert not staging_dir.exists(), f"Staging dir must not exist: {staging_dir}"

    return {
        "study_output_dir": study_output_dir,
        "study_raw_dir": study_raw_dir,
        "datasets_dir": datasets_dir,
        "run_dir": run_dir,
        "audit_dir": audit_dir,
        "llm_source_dir": llm_source_dir,
        "staging_dir": staging_dir,
    }


# ---------------------------------------------------------------------------
# CLI entry point — regenerates committed fixture files
# ---------------------------------------------------------------------------


def _main() -> None:
    """Regenerate the committed fixture files under tests/skills/fixtures/."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    fixtures_dir = repo_root / "tests" / "skills" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    print("Building fixture .xlsx files …")
    _build_xlsx_files(fixtures_dir)

    print("Building _forms_manifest.yaml …")
    _build_manifest(fixtures_dir)

    print("Done. Committed fixture inputs are up to date.")
    print(
        "\nNote: golden_output/ is NOT committed — it is built dynamically during "
        "tests by build_golden_output_tree() using the live phi_scrub.yaml hash."
    )


if __name__ == "__main__":
    _main()
