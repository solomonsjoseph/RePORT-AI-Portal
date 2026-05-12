"""Sweep emitter — PR-draft and HITL-draft files only, no live gh."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.security.phi_sot_sweep import emit_drafts


def _make_findings(tmp_path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at_utc": "2026-05-08T00:00:00+00:00",
        "summary": {
            "total_variables": 4,
            "covered": 1,
            "name_phi_uncovered": 2,
            "column_shape_phi_uncovered": 0,
            "review_required_open": 1,
        },
        "findings": [
            {
                "form": "F1",
                "variable_id_masked": "abc123def456",
                "category": "covered",
                "regulatory_anchor_hint": None,
                "current_action": "pseudonymize",
            },
            {
                "form": "F1",
                "variable_id_masked": "111111111111",
                "category": "name_phi_uncovered",
                "regulatory_anchor_hint": "HIPAA #5 (phone)",
                "current_action": "keep",
            },
            {
                "form": "F2",
                "variable_id_masked": "222222222222",
                "category": "name_phi_uncovered",
                "regulatory_anchor_hint": "HIPAA #5 (phone)",
                "current_action": "keep",
            },
            {
                "form": "F2",
                "variable_id_masked": "333333333333",
                "category": "review_required_open",
                "regulatory_anchor_hint": None,
                "current_action": "review_required",
            },
        ],
    }
    p = tmp_path / "findings.json"
    p.write_text(json.dumps(payload))
    return p


def test_pr_draft_one_per_anchor(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path)
    emit_drafts(
        findings_path=findings, pr_drafts_dir=tmp_path / "pr", hitl_drafts_dir=tmp_path / "hitl"
    )
    pr_files = sorted((tmp_path / "pr").glob("*.md"))
    assert len(pr_files) == 1
    body = pr_files[0].read_text()
    assert "HIPAA #5 (phone)" in body
    assert "111111111111" in body
    assert "222222222222" in body


def test_hitl_draft_one_per_review_required(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path)
    emit_drafts(
        findings_path=findings, pr_drafts_dir=tmp_path / "pr", hitl_drafts_dir=tmp_path / "hitl"
    )
    hitl_files = sorted((tmp_path / "hitl").glob("*.md"))
    assert len(hitl_files) == 1
    assert "333333333333" in hitl_files[0].read_text()


def test_drafts_contain_only_masked_ids(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path)
    emit_drafts(
        findings_path=findings, pr_drafts_dir=tmp_path / "pr", hitl_drafts_dir=tmp_path / "hitl"
    )
    for md in list((tmp_path / "pr").glob("*.md")) + list((tmp_path / "hitl").glob("*.md")):
        assert "PHONE" not in md.read_text()
        assert "AADHAAR" not in md.read_text()


def test_emit_is_idempotent(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path)
    emit_drafts(
        findings_path=findings, pr_drafts_dir=tmp_path / "pr", hitl_drafts_dir=tmp_path / "hitl"
    )
    first_pr = sorted((tmp_path / "pr").glob("*.md"))
    first_pr_body = first_pr[0].read_text()
    emit_drafts(
        findings_path=findings, pr_drafts_dir=tmp_path / "pr", hitl_drafts_dir=tmp_path / "hitl"
    )
    second_pr = sorted((tmp_path / "pr").glob("*.md"))
    assert first_pr == second_pr
    assert second_pr[0].read_text() == first_pr_body
