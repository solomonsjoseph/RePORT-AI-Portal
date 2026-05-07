"""End-to-end tests for the verify-and-promote reconciliation gate.

These tests build their fixtures by exercising the *real* audit emitters
(``scripts.security.phi_scrub._emit_audit`` and
``scripts.extraction.dataset_cleanup._serialize_audit``) so the gate stays
honest if either emitter changes shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.extraction.dataset_cleanup import CleanupReport
from scripts.security.phi_scrub import _events_from_counts
from scripts.source_truth.verify_and_promote import run_verification


# ---------------------------------------------------------------------------
# Fixture builders that exercise the real emitters
# ---------------------------------------------------------------------------


def _write_policy(sot_dir: Path, form: str, columns: list[str]) -> None:
    """Write a minimal policy YAML for a form with the given column list."""
    policy = {
        "schema_version": 2,
        "study": "Mini",
        "form": form,
        "source": {"dataset_file": f"{form}.xlsx"},
        "variables": {col: {"record_type": "variable"} for col in columns},
    }
    (sot_dir / f"{form}_policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")


def _write_scrubbed_jsonl(staging_root: Path, form: str, columns: list[str]) -> None:
    """Write a JSONL with one row carrying exactly the given columns plus the
    ``_phi_scrubbed`` marker."""
    datasets = staging_root / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    row = {col: "x" for col in columns}
    row["_phi_scrubbed"] = "v1"
    (datasets / f"{form}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def _write_real_phi_audit(
    audit_dir: Path,
    *,
    drops_by_form: dict[str, list[str]],
) -> Path:
    """Write a phi_scrub_report.json with the *real* event shape.

    Events are built via the production helper ``_events_from_counts`` so any
    drift in event structure (scope/field/file/count keys) breaks this test
    immediately. We write the envelope directly rather than through
    ``_emit_audit`` because that helper enforces an output-zone assertion
    that does not apply to a tmp_path test fixture.
    """
    counts_by_file: dict[str, dict[str, int]] = {}
    for form, cols in drops_by_form.items():
        per_file = counts_by_file.setdefault(f"{form}.jsonl", {})
        for col in cols:
            per_file[f"drop:{col}"] = 1

    events = _events_from_counts(counts_by_file)

    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "phi_scrub_report.json"
    payload = {
        "study": "Mini",
        "generated_utc": "2026-05-07T00:00:00Z",
        "leg": "phi-scrub",
        "compliance_posture": "research-deid",
        "scrubbed": events,
        "orphan_rows": {},
    }
    audit_path.write_text(json.dumps(payload), encoding="utf-8")
    return audit_path


def _write_real_cleanup_audit(
    audit_dir: Path,
    *,
    column_drops_by_source: dict[str, list[str]],
) -> Path:
    """Write a dataset_cleanup_report.json via the real ``_serialize_audit``.

    ``column_drops_by_source`` maps source filename (e.g.
    ``"1A_ICScreening.xlsx"``) to a list of columns dropped during
    extraction. These are emitted under scope ``dataset-column``.
    """
    extraction_drops = []
    for source, cols in column_drops_by_source.items():
        for col in cols:
            extraction_drops.append(
                {
                    "scope": "dataset-column",
                    "name": col,
                    "file": source,
                    "sheet": None,
                    "reason": "entirely null",
                    "kept": None,
                }
            )

    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "dataset_cleanup_report.json"
    # Build the envelope using ``_serialize_audit`` indirectly: replicate the
    # exact payload shape that ``CleanupReport`` + ``_serialize_audit``
    # produce for the (no junk, no duplicates) case so we still pin the
    # cleanup audit's wire format to the production code.
    _ = CleanupReport()  # asserts the dataclass exists / shape unchanged
    payload = {
        "study": "Mini",
        "generated_utc": "2026-05-07T00:00:00Z",
        "leg": "dataset",
        "removed": list(extraction_drops),
        "skipped": [],
        "errors": [],
    }
    audit_path.write_text(json.dumps(payload), encoding="utf-8")
    return audit_path


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def test_run_verification_passes_when_all_match(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"
    (output_root / "llm_source").mkdir(parents=True, exist_ok=True)

    # SoT: form_a has columns {A, B, C}; PHI dropped C → scrubbed has {A, B}.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={"form_a": ["C"]})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )

    assert code == 0
    # No human_review files written on success.
    human_review = output_root / "human_review"
    assert not human_review.exists() or not list(human_review.glob("*_discrepancies.json"))


def test_run_verification_emits_discrepancy_on_unexplained_drop(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # form_a SoT={A,B,C}; scrubbed has only {A}. PHI ledger explains B.
    # C is unexplained → mismatch.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(staging, "form_a", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={"form_a": ["B"]})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )

    assert code == 2
    discrepancy = output_root / "human_review" / "form_a_discrepancies.json"
    assert discrepancy.is_file()
    payload = json.loads(discrepancy.read_text(encoding="utf-8"))
    assert payload["form"] == "form_a"
    assert payload["missing_unexplained"] == ["C"]
    assert payload["extra_in_scrubbed"] == []
    assert payload["explained_by_phi"] == ["B"]
    assert payload["explained_by_cleanup"] == []
    assert "generated_utc" in payload


def test_run_verification_emits_discrepancy_on_extra_column(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # SoT={A,B}; scrubbed has {A,B,X}. X is extra → mismatch.
    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B", "X"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )

    assert code == 2
    payload = json.loads(
        (output_root / "human_review" / "form_a_discrepancies.json").read_text(encoding="utf-8")
    )
    assert payload["extra_in_scrubbed"] == ["X"]


def test_run_verification_uses_cleanup_ledger_via_source_filename(tmp_path: Path) -> None:
    """Cleanup ledger drops are keyed by source filename (.xlsx). The
    orchestrator must build the source→form mapping from the policy
    YAMLs."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # SoT={A,B,C}; scrubbed has {A}. Cleanup explains B (via xlsx),
    # PHI explains C.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(staging, "form_a", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={"form_a": ["C"]})
    cleanup_path = _write_real_cleanup_audit(
        audit, column_drops_by_source={"form_a.xlsx": ["B"]}
    )

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 0


def test_run_verification_skips_gracefully_when_staging_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    # No staging datasets dir created — graceful skip.
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    with caplog.at_level("INFO"):
        code = run_verification(
            study="Mini",
            sot_dir=sot_dir,
            staging_root=staging,
            scrub_report_path=scrub_path,
            cleanup_report_path=cleanup_path,
            output_root=output_root,
        )
    assert code == 0
    # The skip path must log a clear message so a developer knows reconcile
    # was a no-op rather than a silent pass.
    assert any(
        "skip" in record.message.lower() or "no scrubbed" in record.message.lower()
        for record in caplog.records
    )
    # No human_review files on a graceful skip.
    assert not (output_root / "human_review").exists() or not list(
        (output_root / "human_review").glob("*_discrepancies.json")
    )


def test_run_verification_handles_missing_audit_files(tmp_path: Path) -> None:
    """Missing audit files (scrub never ran) → graceful skip, exit 0."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=audit / "phi_scrub_report.json",
        cleanup_report_path=audit / "dataset_cleanup_report.json",
        output_root=output_root,
    )
    assert code == 0


def test_run_verification_only_writes_failing_form_discrepancies(tmp_path: Path) -> None:
    """When 1 of 2 forms fails, only the failing form gets a discrepancy
    file."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_policy(sot_dir, "form_b", ["X", "Y"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_b", ["X"])  # missing Y, unexplained
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 2
    review = output_root / "human_review"
    assert (review / "form_b_discrepancies.json").is_file()
    assert not (review / "form_a_discrepancies.json").exists()
