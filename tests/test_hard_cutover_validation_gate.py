"""Hard cutover validation gate (issue #80).

This test file is the specification of what cutover-ready means for the
Source Truth architecture (PRD #65). One test per AC bullet asserts
that the gate returns ``status == "pass"`` for that bullet, by running
the appropriate existing validator/integration check.

The gate itself lives in ``scripts.source_truth.cutover_gate`` and only
COMPOSES the existing per-slice validators -- it never reimplements
them. The tests below pin both the gate's report shape and the
expected pass result on the worktree's frozen pilot fixtures.

Strict constraints carried over from issues #73, #79, and #83:

* No hidden keyword router in any new code.
* The verbatim AUDIT_ONLY_NOTE constant is reused from
  ``scripts.source_truth.retrieval`` -- never redefined.
* The cutover gate does not widen the agent file-access boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.source_truth.retrieval import AUDIT_ONLY_NOTE

# ---------------------------------------------------------------------------
# Pilot fixture data.
# ---------------------------------------------------------------------------


_HIV_COLUMNS = ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV", "Time_Stamp"]


def _hiv_inputs() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_path": "Indo-VAP/datasets/6_HIV.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_6_HIV", "columns": list(_HIV_COLUMNS)}],
        "column_count": len(_HIV_COLUMNS),
    }
    pdf_extraction = {
        "page_count": 1,
        "annotation_count": 4,
        "real_annotation_variables": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"],
        "annotation_pages": [
            {"page": 1, "annotations": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"]},
        ],
        "option_sets": {
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": ["Positive (+)", "Negative (-)", "Indeterminate"],
            }
        },
        "metadata": {"form_number": "Form 6", "form_title": "6 HIV"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations and visible options "
                "only; raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "ICTC": {
                "action": "pseudonymize",
                "reason": "facility_clinic_ictc_or_site_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "HIV_VISIT": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
                "option_set": "hiv_result_pdf",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "non_pdf_system_timestamp_metadata",
                "confidence": "high",
                "section": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


_FOB_COLUMNS = [
    "SUBJID",
    "FOB_VISDAT",
    "FOB_COHBOUT",
    "FOB_REVIEW_NOTE",
    "SYSTEM_ID",
    "Time_Stamp",
]


def _fob_inputs() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_path": "Indo-VAP/datasets/98B_FOB.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_98B_FOB", "columns": list(_FOB_COLUMNS)}],
        "column_count": len(_FOB_COLUMNS),
    }
    pdf_extraction = {
        "page_count": 2,
        "annotation_count": 4,
        "real_annotation_variables": [
            "SUBJID",
            "FOB_VISDAT",
            "FOB_COHBOUT",
            "FOB_REVIEW_NOTE",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "FOB_VISDAT",
                    "FOB_COHBOUT",
                    "FOB_REVIEW_NOTE",
                ],
            }
        ],
        "option_sets": {
            "fob_cohort_outcome": {
                "source": "PDF option text",
                "values": ["No TB", "Probable case", "Definite case"],
            }
        },
        "metadata": {"form_number": "Form 98B", "form_title": "Final Outcome"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/98B_FOB.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations/options only; "
                "raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "FOB_VISDAT": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome_visit",
                "pdf_annotation_status": "direct",
            },
            "FOB_COHBOUT": {
                "action": "keep",
                "reason": "clinical_outcome",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
                "option_set": "fob_cohort_outcome",
            },
            "FOB_REVIEW_NOTE": {
                "action": "review_required",
                "reason": "ambiguous_free_text_follow_up_note",
                "confidence": "low",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
            },
            "SYSTEM_ID": {
                "action": "drop",
                "reason": "system_metadata",
                "confidence": "high",
                "field_class": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "timestamp_metadata",
                "confidence": "high",
                "field_class": "timestamp_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


def _write_form(root: Path, form_id: str, inputs: dict[str, Any]) -> None:
    form_dir = root / form_id
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "column_inventory.json").write_text(
        json.dumps(inputs["column_inventory"]), encoding="utf-8"
    )
    (form_dir / "pdf_extraction.json").write_text(
        json.dumps(inputs["pdf_extraction"]), encoding="utf-8"
    )
    (form_dir / "field_policy.draft.yaml").write_text(
        yaml.safe_dump(inputs["field_policy"], sort_keys=False), encoding="utf-8"
    )


@pytest.fixture()
def policy_pilot_root(tmp_path: Path) -> Path:
    """Seed the per-AC validation gate with the two named pilot forms."""
    root = tmp_path / "policy_pilot"
    _write_form(root, "6_HIV", _hiv_inputs())
    _write_form(root, "98B_FOB", _fob_inputs())
    return root


# ---------------------------------------------------------------------------
# Gate report helpers.
# ---------------------------------------------------------------------------


def _entry_for(report: list[dict[str, Any]], ac_id: str) -> dict[str, Any]:
    matches = [entry for entry in report if entry["ac_id"] == ac_id]
    assert matches, f"gate report missing entry for {ac_id}"
    assert len(matches) == 1, f"gate report has duplicate entries for {ac_id}"
    return matches[0]


# ---------------------------------------------------------------------------
# AC1 -- 6_HIV source truth and catalog outputs match pilot quality target.
# ---------------------------------------------------------------------------


def test_ac1_6_hiv_source_truth_and_catalog_outputs_meet_pilot_quality(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC1")
    assert entry["status"] == "pass", entry
    assert "6_HIV" in str(entry.get("details", ""))


# ---------------------------------------------------------------------------
# AC2 -- 98B_FOB validates cleanup-heavy and system-metadata-heavy behavior.
# ---------------------------------------------------------------------------


def test_ac2_98b_fob_cleanup_and_system_metadata_behavior_validates(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC2")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    assert "98B_FOB" in str(details)


# ---------------------------------------------------------------------------
# AC3 -- All-form source-truth validation passes or only review-required.
# ---------------------------------------------------------------------------


def test_ac3_all_forms_pass_or_review_required_only(policy_pilot_root: Path) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC3")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    summary = details.get("summary") if isinstance(details, dict) else None
    if isinstance(summary, dict):
        assert summary.get("forms_with_blocking_errors") == [], summary


# ---------------------------------------------------------------------------
# AC4 -- Metadata retrieval, source-only notes, dropped-variable boundary,
#        and evidence-pack lazy loading pass integration tests.
# ---------------------------------------------------------------------------


def test_ac4_chat_boundary_and_lazy_evidence_pack_loading(policy_pilot_root: Path) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC4")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    audit_text = details.get("audit_only_text") if isinstance(details, dict) else None
    assert audit_text == AUDIT_ONLY_NOTE


# ---------------------------------------------------------------------------
# AC5 -- Dataset analysis flow passes descriptive analysis and validation
#        failure tests.
# ---------------------------------------------------------------------------


def test_ac5_dataset_analysis_flow_descriptive_and_validation_failures(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC5")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    if isinstance(details, dict):
        assert details.get("descriptive_ok") is True
        assert details.get("review_required_refusal_ok") is True


# ---------------------------------------------------------------------------
# AC6 -- Epidemiology analysis planning passes model-policy and causal
#        language tests.
# ---------------------------------------------------------------------------


def test_ac6_epidemiology_planning_model_policy_and_causal_language(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC6")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    if isinstance(details, dict):
        assert details.get("model_policy_ok") is True
        assert details.get("causal_language_ok") is True


# ---------------------------------------------------------------------------
# AC7 -- PHI Handling Ledger remains audit-facing only and is not exposed
#        through normal chat retrieval.
# ---------------------------------------------------------------------------


def test_ac7_phi_handling_ledger_is_audit_only_not_in_runtime_tools(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC7")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    if isinstance(details, dict):
        assert details.get("audit_only_text") == AUDIT_ONLY_NOTE
        forbidden = details.get("forbidden_tool_names_present", [])
        assert forbidden == [], forbidden


# ---------------------------------------------------------------------------
# AC8 -- Artifact lineage proves source truth, catalog, dataset schema,
#        ledgers, and dataset outputs belong to compatible runs.
# ---------------------------------------------------------------------------


def test_ac8_artifact_lineage_compatible_run_and_rejects_mismatch(
    policy_pilot_root: Path,
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)
    entry = _entry_for(report, "AC8")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    if isinstance(details, dict):
        assert details.get("compatible_bundle_ok") is True
        assert details.get("mismatched_bundle_rejected") is True


# ---------------------------------------------------------------------------
# AC9 -- Existing PHI gates, file-access boundaries, and assistant safety
#        tests pass.
# ---------------------------------------------------------------------------


def test_ac9_phi_gates_and_file_access_boundary_unchanged(
    policy_pilot_root: Path, monkeypatch_config: Path
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(
        policy_pilot_root=policy_pilot_root,
        agent_zone_root=monkeypatch_config,
    )
    entry = _entry_for(report, "AC9")
    assert entry["status"] == "pass", entry
    details = entry.get("details", {})
    if isinstance(details, dict):
        # PHI prompt-side guard exercised (benign passes, blocked refused).
        assert details.get("phi_gate_ok") is True
        # All four forbidden zones rejected by validate_agent_read.
        rejected = details.get("zones_rejected", [])
        assert "raw" in rejected
        assert "tmp" in rejected
        assert "audit" in rejected
        assert "snapshots" in rejected


# ---------------------------------------------------------------------------
# Whole-report shape regression.
# ---------------------------------------------------------------------------


def test_gate_report_includes_all_nine_acs_in_order(
    policy_pilot_root: Path, monkeypatch_config: Path
) -> None:
    from scripts.source_truth.cutover_gate import run_hard_cutover_validation

    report = run_hard_cutover_validation(
        policy_pilot_root=policy_pilot_root,
        agent_zone_root=monkeypatch_config,
    )

    ac_ids = [entry["ac_id"] for entry in report]
    assert ac_ids == [f"AC{i}" for i in range(1, 10)]
    statuses = {entry["status"] for entry in report}
    assert "fail" not in statuses


# ---------------------------------------------------------------------------
# No hidden keyword router in the cutover gate module itself.
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cutover_gate_module_has_no_hidden_keyword_router() -> None:
    """The gate orchestrator must not introduce a deterministic keyword
    router. Pin the constraint with a structural check on the module text.
    """
    import re

    text = (_REPO_ROOT / "scripts" / "source_truth" / "cutover_gate.py").read_text(encoding="utf-8")
    forbidden = [
        re.compile(r'if\s+["\']audit["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r'if\s+["\']phi["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r"\broute_by_keywords?\s*\("),
        re.compile(r"\bforce_tool\s*\("),
        re.compile(r"\bblock_tool\s*\("),
        re.compile(r"\bis_small_talk\s*\("),
    ]
    for pattern in forbidden:
        assert pattern.search(text) is None, (
            f"cutover_gate.py contains forbidden routing pattern "
            f"{pattern.pattern!r}; the maintainer's #1 constraint "
            "forbids deterministic keyword routing."
        )


def test_cutover_gate_imports_audit_only_note_from_retrieval() -> None:
    """Reuse, do not redefine. The AUDIT_ONLY_NOTE constant must be
    imported from ``scripts.source_truth.retrieval`` -- never inlined.
    """
    text = (_REPO_ROOT / "scripts" / "source_truth" / "cutover_gate.py").read_text(encoding="utf-8")
    assert "from scripts.source_truth.retrieval import" in text
    assert "AUDIT_ONLY_NOTE" in text
    # Make sure the gate did not redefine the constant locally.
    assert "AUDIT_ONLY_NOTE = " not in text
