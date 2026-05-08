"""Cross-verify emitter — gh pr/issue create with mocked subprocess + redactor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.cross_verify_emit import emit


def _setup_drafts(tmp_path: Path) -> dict[str, Path]:
    pr_dir = tmp_path / "pr_drafts"
    pr_dir.mkdir()
    (pr_dir / "F_DROPPED_VAR.json").write_text(
        json.dumps(
            {
                "kind": "rule_add",
                "form": "F",
                "variable_id": "DROPPED_VAR",
                "rule_yaml": "drop_fields:\n  - DROPPED_VAR\n",
            }
        )
    )
    hitl_dir = tmp_path / "hitl_drafts"
    hitl_dir.mkdir()
    (hitl_dir / "F_FIELD1.md").write_text(
        "# HITL: review_required for F/FIELD1\n\nFIELD1 needs disambiguation.\n"
    )
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    (ep_dir / "F.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "form": "F",
                "study": "Mini",
                "variables": [
                    {"variable_id": "DROPPED_VAR", "id_masked": False, "handling_action": "drop", "description": "Dropped"},
                    {"variable_id": "abc123def456", "id_masked": True, "handling_action": "review_required", "description": "Disambiguation needed"},
                ],
            }
        )
    )
    keyfile = tmp_path / "phi.key"
    keyfile.write_text(bytes([0] * 32).hex())
    keyfile.chmod(0o600)
    return {"pr": pr_dir, "hitl": hitl_dir, "ep": ep_dir, "key": keyfile}


def test_no_runner_writes_body_files(tmp_path: Path) -> None:
    inp = _setup_drafts(tmp_path)
    summary = emit(
        pr_drafts_dir=inp["pr"],
        hitl_drafts_dir=inp["hitl"],
        evidence_packs_dir=inp["ep"],
        gh_runner=None,
        key_path=inp["key"],
    )
    assert summary["pr_emitted"] == 0
    assert summary["issue_emitted"] == 0
    assert summary["skipped_no_runner"] == 2
    assert (inp["pr"] / "F_DROPPED_VAR.body.md").is_file()
    assert (inp["hitl"] / "F_FIELD1.body.md").is_file()


def test_runner_invoked_for_pr_and_issue(tmp_path: Path) -> None:
    inp = _setup_drafts(tmp_path)
    calls: list[tuple[list[str], str]] = []
    def mock_runner(argv: list[str], body: str | None) -> int:
        calls.append((argv, body or ""))
        return 0
    summary = emit(
        pr_drafts_dir=inp["pr"],
        hitl_drafts_dir=inp["hitl"],
        evidence_packs_dir=inp["ep"],
        gh_runner=mock_runner,
        key_path=inp["key"],
    )
    assert summary["pr_emitted"] == 1
    assert summary["issue_emitted"] == 1
    pr_call = next(c for c in calls if c[0][:2] == ["pr", "create"])
    issue_call = next(c for c in calls if c[0][:2] == ["issue", "create"])
    assert "--title" in pr_call[0]
    assert "--body-file" in pr_call[0]
    assert "--label" in issue_call[0]
    assert "HITL" in issue_call[0]


def test_pr_body_redacts_phi_variable_id(tmp_path: Path) -> None:
    """A PR body for a PHI-classified variable must NOT contain the cleartext id."""
    inp = _setup_drafts(tmp_path)
    # Replace the PR draft with one targeting the PHI variable.
    (inp["pr"] / "F_FIELD1.json").write_text(
        json.dumps({"kind": "rule_add", "form": "F", "variable_id": "FIELD1"})
    )
    captured: list[str] = []
    def mock_runner(argv: list[str], body: str | None) -> int:
        captured.append(body or "")
        return 0
    emit(
        pr_drafts_dir=inp["pr"],
        hitl_drafts_dir=inp["hitl"],
        evidence_packs_dir=inp["ep"],
        gh_runner=mock_runner,
        key_path=inp["key"],
    )
    pr_bodies = [b for b in captured if "rule_add" in b or "DROPPED_VAR" in b or "<phi:" in b]
    # Find the body that targets FIELD1
    for b in captured:
        # The body for FIELD1 should contain a <phi:...> token, not the cleartext.
        if "F/FIELD1" in b:
            pytest.fail("cleartext FIELD1 leaked into PR body")
    # Confirm the redactor produced a phi token somewhere.
    assert any("<phi:" in b for b in captured)


def test_runner_failure_does_not_raise(tmp_path: Path) -> None:
    inp = _setup_drafts(tmp_path)
    def failing_runner(argv: list[str], body: str | None) -> int:
        return 1  # gh failed
    summary = emit(
        pr_drafts_dir=inp["pr"],
        hitl_drafts_dir=inp["hitl"],
        evidence_packs_dir=inp["ep"],
        gh_runner=failing_runner,
        key_path=inp["key"],
    )
    # Failures counted, no exception raised.
    assert summary.get("failed", 0) >= 1
