"""Cross-verify pipeline — orchestrates scan, fix agent, emit in order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.cross_verify_pipeline import run

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "cross_verify"


def _write_key(p: Path, byte: int) -> None:
    p.write_text(bytes([byte] * 32).hex())
    p.chmod(0o600)


def test_pipeline_runs_scanner_only_without_llm_or_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipeline runs scanner; fix agent is scanner-only; emit produces body files."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    # Override config paths to point at tmp_path so we don't touch real output
    import config

    monkeypatch.setattr(config, "CROSS_VERIFY_SAFE_REPORT_PATH", tmp_path / "safe.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_REPEAT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_PR_DRAFTS_DIR", tmp_path / "pr")
    monkeypatch.setattr(config, "CROSS_VERIFY_HITL_DRAFTS_DIR", tmp_path / "hitl")

    summary = run(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        evidence_packs_dir=tmp_path / "ep_missing",
        phi_scrub_yaml=tmp_path / "phi_scrub.yaml",
        llm_call=None,
        gh_runner=None,
        key_path=keyfile,
    )
    assert summary["scanner"]["summary"]["forms"] >= 1
    assert summary["fix_agent"]["scanner_only"] is True
    assert summary["emit"]["pr_emitted"] == 0
    # SAFE report written
    assert (tmp_path / "safe.json").is_file()


def test_pipeline_threads_llm_call_to_fix_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When llm_call is provided, fix agent invokes it."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = tmp_path / "ep"
    ep_dir.mkdir()
    (ep_dir / "F.json").write_text(
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
                        "description": "Drop me",
                    }
                ],
            }
        )
    )
    import config

    monkeypatch.setattr(config, "CROSS_VERIFY_SAFE_REPORT_PATH", tmp_path / "safe.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_REPEAT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_PR_DRAFTS_DIR", tmp_path / "pr")
    monkeypatch.setattr(config, "CROSS_VERIFY_HITL_DRAFTS_DIR", tmp_path / "hitl")

    calls: list[str] = []

    def mock_llm(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"kind": "rule_add", "rule_yaml": "drop_fields:\n  - DROPPED_VAR\n"})

    summary = run(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        evidence_packs_dir=ep_dir,
        phi_scrub_yaml=tmp_path / "phi_scrub.yaml",
        llm_call=mock_llm,
        gh_runner=None,
        key_path=keyfile,
    )
    assert len(calls) >= 1
    assert summary["fix_agent"]["scanner_only"] is False
    assert summary["fix_agent"]["proposed_fixes"] >= 1


def test_pipeline_threads_gh_runner_to_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When gh_runner provided, emit invokes it for each draft."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = tmp_path / "ep"
    ep_dir.mkdir()
    (ep_dir / "F.json").write_text(
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
                        "description": "Drop me",
                    }
                ],
            }
        )
    )
    import config

    monkeypatch.setattr(config, "CROSS_VERIFY_SAFE_REPORT_PATH", tmp_path / "safe.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_REPEAT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(config, "CROSS_VERIFY_PR_DRAFTS_DIR", tmp_path / "pr")
    monkeypatch.setattr(config, "CROSS_VERIFY_HITL_DRAFTS_DIR", tmp_path / "hitl")

    def mock_llm(prompt: str) -> str:
        return json.dumps({"kind": "rule_add", "rule_yaml": "drop_fields:\n  - DROPPED_VAR\n"})

    gh_calls: list[tuple[list[str], str]] = []

    def mock_gh(argv: list[str], body: str | None) -> int:
        gh_calls.append((argv, body or ""))
        return 0

    run(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        evidence_packs_dir=ep_dir,
        phi_scrub_yaml=tmp_path / "phi_scrub.yaml",
        llm_call=mock_llm,
        gh_runner=mock_gh,
        key_path=keyfile,
    )
    assert len(gh_calls) >= 1
