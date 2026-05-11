"""Cross-verify fix agent — injectable LLM, no-invent guards, repeat ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.source_truth.cross_verify_fix_agent import run_fix_agent
from tests.conftest import skip_as_root


def _setup_inputs(tmp_path: Path) -> dict[str, Path]:
    safe = tmp_path / "safe.json"
    safe.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "study": "Mini",
                "generated_at_utc": "x",
                "summary": {"forms": 1, "variables_scanned": 1, "discrepancies": 1},
                "findings": [
                    {
                        "form": "F",
                        "variable_id": "DROPPED_VAR",
                        "column_present": True,
                        "scrubbed_count": 0,
                        "sot_action": "drop",
                    }
                ],
            }
        )
    )
    sot = tmp_path / "sot"
    sot.mkdir()
    (sot / "F_policy.yaml").write_text(
        "schema_version: 1\nstudy: Mini\nform: F\nvariables:\n"
        "  - variable_id: DROPPED_VAR\n    handling_intent:\n      action: drop\n"
    )
    ep = tmp_path / "evidence_packs"
    ep.mkdir()
    (ep / "F.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "form": "F",
                "study": "Mini",
                "variables": [
                    {
                        "variable_id": "DROPPED_VAR",
                        "id_masked": False,
                        "handling_action": "drop",
                        "description": "Dropped variable",
                    }
                ],
            }
        )
    )
    phi_yaml = tmp_path / "phi_scrub.yaml"
    phi_yaml.write_text("drop_fields: []\n")
    return {
        "safe_report": safe,
        "sot_dir": sot,
        "evidence_packs": ep,
        "phi_scrub_yaml": phi_yaml,
        "ledger": tmp_path / "ledger.json",
        "pr_drafts": tmp_path / "pr_drafts",
        "hitl_drafts": tmp_path / "hitl_drafts",
    }


def test_scanner_only_mode_runs_without_llm(tmp_path: Path) -> None:
    inp = _setup_inputs(tmp_path)
    summary = run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[],
        llm_call=None,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert summary["proposed_fixes"] == 0
    assert summary["auto_fix_exhausted"] == 0
    assert summary["scanner_only"] is True


def test_mock_llm_proposes_rule_add(tmp_path: Path) -> None:
    inp = _setup_inputs(tmp_path)
    def mock_llm(prompt: str) -> str:
        return json.dumps({"kind": "rule_add", "rule_yaml": "drop_fields:\n  - DROPPED_VAR\n"})
    summary = run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[],
        llm_call=mock_llm,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert summary["proposed_fixes"] == 1
    drafts = sorted(inp["pr_drafts"].glob("*.json"))
    assert len(drafts) == 1


def test_no_invent_guard_rejects_rule_for_unknown_variable(tmp_path: Path) -> None:
    inp = _setup_inputs(tmp_path)
    def mock_llm(prompt: str) -> str:
        return json.dumps({"kind": "rule_add", "rule_yaml": "drop_fields:\n  - GHOST_VAR\n", "variable_id": "GHOST_VAR"})
    summary = run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[],
        llm_call=mock_llm,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert summary["proposed_fixes"] == 0
    assert summary["rejected_no_invent"] == 1


def test_repeat_threshold_marks_auto_fix_exhausted(tmp_path: Path) -> None:
    inp = _setup_inputs(tmp_path)
    inp["ledger"].write_text(json.dumps({"F:DROPPED_VAR": 2}))
    def mock_llm(prompt: str) -> str:
        return json.dumps({"kind": "rule_add", "rule_yaml": "drop_fields:\n  - DROPPED_VAR\n"})
    summary = run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[],
        llm_call=mock_llm,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert summary["auto_fix_exhausted"] == 1
    hitl = sorted(inp["hitl_drafts"].glob("*.md"))
    assert len(hitl) == 1


@skip_as_root
def test_deny_paths_enforced_during_run(tmp_path: Path) -> None:
    """When deny_paths is non-empty, those paths become unreadable during the run."""
    inp = _setup_inputs(tmp_path)
    secret = tmp_path / "row.jsonl"
    secret.write_text("{}\n")
    captured = {"could_read": None}
    def mock_llm(prompt: str) -> str:
        captured["could_read"] = os.access(secret, os.R_OK)
        return json.dumps({"kind": "hitl", "reason": "ok"})
    run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[secret],
        llm_call=mock_llm,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert captured["could_read"] is False, "deny_paths should make the row JSONL unreadable mid-run"
    assert os.access(secret, os.R_OK), "read access restored after run"


def test_sot_stub_sets_claude_drafted_true(tmp_path: Path) -> None:
    inp = _setup_inputs(tmp_path)
    def mock_llm(prompt: str) -> str:
        return json.dumps({
            "kind": "sot_stub_add",
            "variable_record": {"variable_id": "DROPPED_VAR", "handling_intent": {"action": "drop"}},
        })
    summary = run_fix_agent(
        safe_report_path=inp["safe_report"],
        sot_dir=inp["sot_dir"],
        evidence_packs_dir=inp["evidence_packs"],
        phi_scrub_yaml=inp["phi_scrub_yaml"],
        deny_paths=[],
        llm_call=mock_llm,
        repeat_ledger_path=inp["ledger"],
        output_pr_drafts_dir=inp["pr_drafts"],
        output_hitl_drafts_dir=inp["hitl_drafts"],
    )
    assert summary["proposed_fixes"] == 1
    drafts = sorted(inp["pr_drafts"].glob("*.json"))
    body = json.loads(drafts[0].read_text())
    assert body["variable_record"]["claude_drafted"] is True
