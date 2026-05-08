"""Phase 1 exit-criterion verifier: every variable → covered OR open HITL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.security.phi_sweep_verify import VerificationFailed, verify


def _make_findings(tmp_path: Path, with_hitl: bool) -> Path:
    findings = [
        {"form": "F", "variable_id_masked": "aaa", "category": "covered", "regulatory_anchor_hint": None, "current_action": "keep"},
        {"form": "F", "variable_id_masked": "bbb", "category": "review_required_open", "regulatory_anchor_hint": None, "current_action": "review_required"},
    ]
    p = tmp_path / "findings.json"
    p.write_text(json.dumps({"schema_version": 1, "generated_at_utc": "x", "summary": {}, "findings": findings}))
    if with_hitl:
        h = tmp_path / "hitl"
        h.mkdir()
        (h / "F_bbb.md").write_text("draft")
    return p


def test_passes_when_all_covered_or_hitl_present(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path, with_hitl=True)
    verify(findings_path=findings, hitl_drafts_dir=tmp_path / "hitl")


def test_fails_when_review_required_has_no_hitl_draft(tmp_path: Path) -> None:
    findings = _make_findings(tmp_path, with_hitl=False)
    with pytest.raises(VerificationFailed) as excinfo:
        verify(findings_path=findings, hitl_drafts_dir=tmp_path / "hitl")
    assert "F_bbb" in str(excinfo.value)


def test_fails_when_uncovered_with_no_pr_draft(tmp_path: Path) -> None:
    p = tmp_path / "findings.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "x",
                "summary": {},
                "findings": [
                    {"form": "F", "variable_id_masked": "ccc", "category": "name_phi_uncovered", "regulatory_anchor_hint": "HIPAA #5 (phone)", "current_action": "keep"}
                ],
            }
        )
    )
    pr = tmp_path / "pr"
    pr.mkdir()
    with pytest.raises(VerificationFailed):
        verify(findings_path=p, hitl_drafts_dir=tmp_path / "hitl", pr_drafts_dir=pr)
