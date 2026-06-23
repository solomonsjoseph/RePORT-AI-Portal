"""N22: holding producers deposit value-free notes into the per-form queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.audit.review_paths import verifier_review_path
from scripts.security.phi_review import FormReviewApproval, HeldReason
from scripts.skills.extract_to_llm_source import (
    EXIT_AUDIT_COVERAGE_INCOMPLETE,
    _write_classification_hold_note,
    _write_scrub_quarantine_note,
)
from scripts.skills.extract_to_llm_source import (
    main as verify_main,
)
from tests.skills.fixtures.build_fixture import (
    FIXTURE_FORMS,
    FIXTURE_RUN_ID,
    FIXTURE_STUDY,
    build_golden_output_tree,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PHI_SCRUB_YAML = _REPO_ROOT / "config" / "_defaults" / "phi_scrub.yaml"


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", _PHI_SCRUB_YAML, raising=False)


def test_scrub_quarantine_note_is_value_free(tmp_path: Path) -> None:
    note = tmp_path / "scrub_quarantine_review.md"
    _write_scrub_quarantine_note(
        note,
        {
            "form": "9_SAE",
            "kept": 40,
            "quarantined": 3,
            "reasons": ["date_unshiftable"],
            "elevated": True,
        },
    )
    text = note.read_text(encoding="utf-8")
    assert "9_SAE" in text
    assert "Quarantined rows:** 3" in text
    assert "ELEVATED" in text
    assert "date_unshiftable" in text


def test_scrub_quarantine_note_lists_variables_involved(tmp_path: Path) -> None:
    """A quarantine note must name WHICH columns could not be scrubbed (header
    names only), so a reviewer sees the variables involved — not just a count."""
    note = tmp_path / "scrub_quarantine_review.md"
    _write_scrub_quarantine_note(
        note,
        {
            "form": "12A_FUA",
            "kept": 100,
            "quarantined": 4,
            "reasons": ["date_unshiftable:4"],
            "elevated": False,
            "columns": ["FU_VISITDAT", "FU_NEXTDAT"],
        },
    )
    text = note.read_text(encoding="utf-8")
    assert "Variables involved" in text
    assert "FU_VISITDAT" in text
    assert "FU_NEXTDAT" in text


def test_scrub_quarantine_note_handles_whole_row_holds(tmp_path: Path) -> None:
    """Orphan/whole-row holds have no offending column — the note says so rather
    than rendering an empty section."""
    note = tmp_path / "scrub_quarantine_review.md"
    _write_scrub_quarantine_note(
        note,
        {"form": "9_SAE", "kept": 40, "quarantined": 2, "reasons": ["orphan_no_subject_id:2"]},
    )
    text = note.read_text(encoding="utf-8")
    assert "Variables involved" in text
    assert "whole-row hold" in text


def test_sot_joined_gate_clean_run_writes_no_human_review_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """human_review/ is issues-only: a clear SoT gate (no held forms) writes the
    machine-readable JSON sidecar but NO human_review .md note (no clutter)."""
    import config
    from scripts.audit.review_paths import publish_sot_joined_gate_md_path
    from scripts.pipeline.host_pipeline import _write_sot_joined_gate_outcome

    study = "S"
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "STUDY_NAME", study, raising=False)
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path / "output" / study, raising=False)
    monkeypatch.setattr(
        config, "STUDY_AUDIT_DIR", tmp_path / "output" / study / "audit", raising=False
    )

    _write_sot_joined_gate_outcome(run_id="run_clean", study=study, held_forms=[])

    sidecar = tmp_path / "output" / study / "runs" / "run_clean" / "sot_joined_gate_outcome.json"
    assert sidecar.is_file(), "JSON sidecar (run record) must still be written"
    assert json.loads(sidecar.read_text())["held_count"] == 0
    md = publish_sot_joined_gate_md_path(config.STUDY_AUDIT_DIR, "run_clean")
    assert not md.exists(), "a clear run must NOT drop a human_review note"


def test_sot_joined_gate_held_run_writes_human_review_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When forms ARE held, the note IS written and names the held forms."""
    import config
    from scripts.audit.review_paths import publish_sot_joined_gate_md_path
    from scripts.pipeline.host_pipeline import _write_sot_joined_gate_outcome

    study = "S"
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "STUDY_NAME", study, raising=False)
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path / "output" / study, raising=False)
    monkeypatch.setattr(
        config, "STUDY_AUDIT_DIR", tmp_path / "output" / study / "audit", raising=False
    )

    _write_sot_joined_gate_outcome(run_id="run_held", study=study, held_forms=["20_CoEnroll.xlsx"])

    md = publish_sot_joined_gate_md_path(config.STUDY_AUDIT_DIR, "run_held")
    assert md.is_file(), "a held run MUST write a human_review note"
    text = md.read_text(encoding="utf-8")
    assert "20_CoEnroll.xlsx" in text
    assert "held" in text


def test_classification_hold_note_is_value_free(tmp_path: Path) -> None:
    item = FormReviewApproval(
        form_name="2A_Base",
        status="held",
        attempts=1,
        actions={},
        classifications=(),
        reasons=("duplicate normalized header: subjid",),
        rule_bundle_sha256="abc",
        source_mode="pinned",
        held_reason=HeldReason("tried X", "ambiguous Y", "resolve Z"),
        force_drop_headers=("SIGNATURE",),
    )
    note = tmp_path / "classification_review.md"
    _write_classification_hold_note(note, item)
    text = note.read_text(encoding="utf-8")
    assert "2A_Base" in text
    assert "held" in text
    assert "duplicate normalized header" in text
    assert "resolve Z" in text
    assert "Force-dropped direct-identifier columns: 1" in text


def test_verifier_review_note_is_value_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifier failure deposits verifier_review.md under human_review/{run_id}/ (N22)."""
    from scripts.audit.ledger import dataset_phi_ledger_path

    _patch_config(monkeypatch, tmp_path)
    paths = build_golden_output_tree(
        output_root=tmp_path / "output",
        raw_root=tmp_path / "data" / "raw",
        tmp_root=tmp_path / "tmp",
        phi_scrub_yaml_path=_PHI_SCRUB_YAML,
    )
    ledger_path = dataset_phi_ledger_path(paths["audit_dir"], FIXTURE_FORMS[0])
    data = json.loads(ledger_path.read_text())
    data["events"] = [
        {"variable_id": "DOB", "action": "jitter_date", "rule": {"jurisdictions": ["USA"]}}
    ]
    ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    rc = verify_main(["verify", "--study", FIXTURE_STUDY, "--run", FIXTURE_RUN_ID])
    assert rc == EXIT_AUDIT_COVERAGE_INCOMPLETE

    note_path = verifier_review_path(paths["audit_dir"], FIXTURE_RUN_ID)
    assert note_path.is_file()
    text = note_path.read_text(encoding="utf-8")
    assert "Audit verifier — human review required" in text
    assert FIXTURE_RUN_ID in text
    assert "ledger_entry_fields_complete" in text
    assert "[16]" in text


def test_discrepancy_review_skips_maintainer_approved_printed_widgets(tmp_path: Path) -> None:
    """15_Feces PDF-only widgets pre-approved in TRUE_PDF_VARIABLES_WITHOUT_DATASET_HEADER."""
    from scripts.source_truth import generate_lean_outputs as glo

    policy = tmp_path / "15_Feces.lean.yaml"
    policy.write_text(
        yaml.safe_dump(
            {
                "discrepancies": [
                    {
                        "kind": "printed_widget_without_dataset_header",
                        "pdf_annotation_says": [
                            "FC_CONSIST1",
                            "FC_CONSIST2",
                            "FC_CONSIST3",
                            "FC_SIGN",
                            "FC_TECH1",
                            "FC_TECH2",
                            "FC_TECH3",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert glo._discrepancy_review_reason(policy, form="15_Feces") is None


def test_n22_producers_use_form_first_review_paths() -> None:
    """Every N22 helper routes notes under human_review/{key}/ (no category subdirs)."""
    from scripts.audit.review_paths import (
        classification_review_path,
        excel_duplicate_review_path,
        form_review_dir,
        human_review_root,
        presidio_failure_md_path,
        publish_sot_joined_gate_md_path,
        scrub_quarantine_review_path,
        sot_review_report_path,
        verifier_review_path,
    )

    audit = Path("output/X/audit")
    form = "15_Feces"
    run_id = "run_abc"
    group = "dup_group_1"
    expected_parent = form_review_dir(audit, form)

    assert human_review_root(audit) == audit / "human_review"
    assert sot_review_report_path(audit, form).parent == expected_parent
    assert classification_review_path(audit, form).parent == expected_parent
    assert scrub_quarantine_review_path(audit, form).parent == expected_parent
    assert presidio_failure_md_path(audit, form).parent == expected_parent
    assert excel_duplicate_review_path(audit, group).parent == form_review_dir(audit, group)
    assert publish_sot_joined_gate_md_path(audit, run_id).parent == form_review_dir(audit, run_id)
    assert verifier_review_path(audit, run_id).parent == form_review_dir(audit, run_id)
    for path in (
        sot_review_report_path(audit, form),
        classification_review_path(audit, form),
        scrub_quarantine_review_path(audit, form),
        presidio_failure_md_path(audit, form),
    ):
        parts = path.parts
        assert "human_review" in parts
        assert "Sot_review" not in parts
        assert "/sot/" not in str(path).replace("\\", "/")
        assert "/excel/" not in str(path).replace("\\", "/")
