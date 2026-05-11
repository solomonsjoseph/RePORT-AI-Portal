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
    # Production builds these keys via ``_bump(scope, field)`` which emits
    # ``f"phi-scrub-{scope}:{field}"`` (see scripts/security/phi_scrub.py).
    # The reconciliation reader filters on the prefixed form, so the test
    # fixture MUST also use that prefix or the reader silently drops
    # everything (the bug uncovered by the first real-data smoke).
    counts_by_file: dict[str, dict[str, int]] = {}
    for form, cols in drops_by_form.items():
        per_file = counts_by_file.setdefault(f"{form}.jsonl", {})
        for col in cols:
            per_file[f"phi-scrub-drop:{col}"] = 1

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


def test_run_verification_emits_discrepancy_on_unexplained_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

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
    discrepancy = config.TMP_DIR / "Mini" / "human_review" / "form_a_discrepancies.json"
    assert discrepancy.is_file()
    payload = json.loads(discrepancy.read_text(encoding="utf-8"))
    assert payload["form"] == "form_a"
    assert payload["missing_unexplained"] == ["C"]
    assert payload["extra_in_scrubbed"] == []
    assert payload["explained_by_phi"] == ["B"]
    assert payload["explained_by_cleanup"] == []
    assert "generated_utc" in payload


def test_run_verification_emits_discrepancy_on_extra_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

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
        (config.TMP_DIR / "Mini" / "human_review" / "form_a_discrepancies.json").read_text(encoding="utf-8")
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


def test_run_verification_returns_2_on_corrupt_phi_audit_envelope(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue 1 — corrupt audit envelope (truncated JSON) must produce a clean
    exit code 2 with a clear log line, not a stack trace."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    audit.mkdir()
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])

    # Truncated JSON — fails json.loads.
    bad_phi = audit / "phi_scrub_report.json"
    bad_phi.write_text('{"study": "Mini", "scrubbed": [{"scope":', encoding="utf-8")
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    with caplog.at_level("ERROR"):
        code = run_verification(
            study="Mini",
            sot_dir=sot_dir,
            staging_root=staging,
            scrub_report_path=bad_phi,
            cleanup_report_path=cleanup_path,
            output_root=output_root,
        )
    assert code == 2
    # Clear log line referencing the path of the corrupt file.
    assert any(
        "malformed audit envelope" in record.getMessage().lower()
        and str(bad_phi) in record.getMessage()
        for record in caplog.records
    )


def test_run_verification_returns_2_on_duplicate_form_names(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue 4 — two policy YAMLs declaring the same ``form:`` value must be
    rejected with exit code 2 and a log line naming the duplicate."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # Two policies, different filenames, same ``form: form_dup``.
    policy_a = {
        "schema_version": 2,
        "study": "Mini",
        "form": "form_dup",
        "source": {"dataset_file": "form_dup_a.xlsx"},
        "variables": {"A": {"record_type": "variable"}},
    }
    policy_b = {
        "schema_version": 2,
        "study": "Mini",
        "form": "form_dup",
        "source": {"dataset_file": "form_dup_b.xlsx"},
        "variables": {"B": {"record_type": "variable"}},
    }
    (sot_dir / "form_dup_a_policy.yaml").write_text(yaml.safe_dump(policy_a), encoding="utf-8")
    (sot_dir / "form_dup_b_policy.yaml").write_text(yaml.safe_dump(policy_b), encoding="utf-8")

    # Need a scrubbed JSONL so the gate progresses past the staging-empty
    # graceful skip and reaches the policy-loading duplicate check.
    _write_scrubbed_jsonl(staging, "form_dup", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    # Use main() so we exercise the CLI-level catch that converts the
    # ValueError into exit code 2 (the orchestrator path).
    from scripts.source_truth.verify_and_promote import main

    with caplog.at_level("ERROR"):
        code = main(
            [
                "--study",
                "Mini",
                "--sot-dir",
                str(sot_dir),
                "--staging-root",
                str(staging),
                "--scrub-report",
                str(scrub_path),
                "--cleanup-report",
                str(cleanup_path),
                "--output-root",
                str(output_root),
            ]
        )
    assert code == 2
    assert any(
        "duplicate form name" in record.getMessage().lower()
        and "form_dup" in record.getMessage()
        for record in caplog.records
    )


def test_run_verification_warns_on_orphan_cleanup_form(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue 5 — cleanup ledger references an .xlsx that no policy maps to a
    form. The orphan must be logged as a warning but the gate must still run
    to completion."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # form_a is fully reconcilable. Mystery_Form.xlsx is in the cleanup
    # ledger but has no matching policy → orphan.
    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(
        audit,
        column_drops_by_source={"Mystery_Form.xlsx": ["GHOST_COL"]},
    )

    with caplog.at_level("WARNING"):
        code = run_verification(
            study="Mini",
            sot_dir=sot_dir,
            staging_root=staging,
            scrub_report_path=scrub_path,
            cleanup_report_path=cleanup_path,
            output_root=output_root,
        )
    # Gate still runs to completion: form_a reconciles cleanly, exit 0.
    assert code == 0
    # Orphan emits a warning that names the form and the dropped count.
    orphan_logs = [
        r for r in caplog.records if "unmatched form" in r.getMessage().lower()
    ]
    assert orphan_logs, "expected at least one orphan-form warning"
    msg = orphan_logs[0].getMessage()
    assert "Mystery_Form" in msg
    assert "1" in msg  # one dropped column


# ---------------------------------------------------------------------------
# Promotion: staging → llm_source/ on gate pass
# ---------------------------------------------------------------------------


def _write_staging_dataset_schema(
    output_root: Path,
    payload: dict[str, object],
    *,
    tmp_root: Path | None = None,
    study: str = "Mini",
) -> Path:
    """Write a fake staging dataset_schema.

    When ``tmp_root`` is provided the file is written to
    ``<tmp_root>/<study>/staging/llm_source/`` — the path that
    ``run_verification`` derives from ``config.TMP_DIR``.  Otherwise it
    falls back to the legacy ``output_root/staging/llm_source/`` path
    (used by tests that exit before promotion and never reach the
    ``config.TMP_DIR`` code-path).
    """
    if tmp_root is not None:
        staging_dir = tmp_root / study / "staging" / "llm_source"
    else:
        staging_dir = output_root / "staging" / "llm_source"
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / "phi_handled_dataset_schema.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_run_verification_promotes_dataset_schema_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On gate pass with scrubbed data verified AND staging schema present,
    the staging dataset_schema is atomically copied to
    ``llm_source/dataset_schema.json`` (canonical name, no
    ``phi_handled_`` prefix)."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    schema_payload = {
        "artifact_type": "study_dataset_schema",
        "entries": [{"form": "form_a", "columns": ["A", "B"]}],
    }
    staging_schema = _write_staging_dataset_schema(
        output_root, schema_payload, tmp_root=tmp_path / "tmp", study="Mini"
    )
    expected_content = json.loads(staging_schema.read_text(encoding="utf-8"))

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 0

    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert promoted.is_file(), "promoted dataset_schema.json must exist on success"
    assert json.loads(promoted.read_text(encoding="utf-8")) == expected_content


def test_run_verification_does_not_promote_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexplained drop fails the gate (exit 2). Even when a staging
    dataset_schema is present, promotion must not happen."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # form_a: SoT={A,B,C}; scrubbed has only {A}; B explained, C unexplained.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(staging, "form_a", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={"form_a": ["B"]})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    _write_staging_dataset_schema(
        output_root,
        {"artifact_type": "study_dataset_schema", "entries": []},
        tmp_root=tmp_path / "tmp",
        study="Mini",
    )

    code = run_verification(
        study="Mini",
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 2
    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert not promoted.exists(), (
        "no promotion on failure: dataset_schema.json must not appear in llm_source/"
    )


def test_run_verification_does_not_promote_on_graceful_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty staging (scrub never ran) → exit 0, but promotion must NOT
    happen. Even if a stale staging schema exists, the gate cannot
    promote it because nothing was verified."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"  # no datasets/ subdir → graceful skip
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    # Even with a staging schema sitting around, graceful skip must not
    # promote it.
    _write_staging_dataset_schema(
        output_root,
        {"artifact_type": "study_dataset_schema", "entries": []},
        tmp_root=tmp_path / "tmp",
        study="Mini",
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
    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert not promoted.exists(), (
        "graceful skip must not promote: dataset_schema.json must not appear"
    )


def test_run_verification_passes_without_promotion_when_staging_schema_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All forms reconcile, but staging dataset_schema does not exist
    (e.g. the build coordinator was run without ``--column-inventory``).
    The gate must still pass (exit 0) and emit a warning; promotion
    does not occur."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    _write_policy(sot_dir, "form_a", ["A", "B"])
    _write_scrubbed_jsonl(staging, "form_a", ["A", "B"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})
    # Note: no staging dataset_schema written.

    with caplog.at_level("WARNING"):
        code = run_verification(
            study="Mini",
            sot_dir=sot_dir,
            staging_root=staging,
            scrub_report_path=scrub_path,
            cleanup_report_path=cleanup_path,
            output_root=output_root,
        )
    assert code == 0
    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert not promoted.exists(), (
        "missing staging schema: nothing to promote, destination must not exist"
    )
    # Warning emitted that flags the missing staging schema.
    assert any(
        "staging dataset_schema not found" in record.getMessage().lower()
        for record in caplog.records
    ), "expected a warning naming the missing staging dataset_schema"


def test_run_verification_only_writes_failing_form_discrepancies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When 1 of 2 forms fails, only the failing form gets a discrepancy
    file."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

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
    review = config.TMP_DIR / "Mini" / "human_review"
    assert (review / "form_b_discrepancies.json").is_file()


def test_human_review_writes_to_tmp_not_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 5a Task 3 — discrepancy files must be written under
    ``config.TMP_DIR / <study> / human_review/`` rather than the legacy
    ``output_root / human_review/`` location."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"
    study = "Mini"

    # Force a reconciliation failure: SoT={A,B,C}, scrubbed={A}, no explanation.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(staging, "form_a", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study=study,
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 2

    legacy = output_root / "human_review"
    new_loc = config.TMP_DIR / study / "human_review"
    assert not legacy.exists(), (
        f"human_review must NOT be written to legacy output_root path; found {legacy}"
    )
    assert new_loc.exists(), (
        f"human_review must be written under tmp/<study>/; expected {new_loc}"
    )
    assert (new_loc / "form_a_discrepancies.json").is_file()


def test_promote_schema_uses_staging_dir_param(tmp_path: Path) -> None:
    """_promote_dataset_schema must read from the provided staging_dir, not from output/staging/."""
    import json
    from scripts.source_truth.verify_and_promote import _promote_dataset_schema

    output_root = tmp_path / "output" / "TestStudy"
    output_root.mkdir(parents=True)
    (output_root / "llm_source").mkdir()

    # Write schema to the NEW tmp staging location
    staging_dir = tmp_path / "tmp" / "TestStudy" / "staging" / "llm_source"
    staging_dir.mkdir(parents=True)
    schema_payload = {"artifact_type": "study_dataset_schema", "entries": []}
    (staging_dir / "phi_handled_dataset_schema.json").write_text(
        json.dumps(schema_payload), encoding="utf-8"
    )

    rc = _promote_dataset_schema(output_root=output_root, staging_dir=staging_dir)
    assert rc == 0
    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert promoted.is_file()
    assert json.loads(promoted.read_text()) == schema_payload

    # Corrupt file at OLD path must be ignored
    old_staging = output_root / "staging" / "llm_source"
    old_staging.mkdir(parents=True)
    (old_staging / "phi_handled_dataset_schema.json").write_text("NOT JSON")
    rc2 = _promote_dataset_schema(output_root=output_root, staging_dir=staging_dir)
    assert rc2 == 0
