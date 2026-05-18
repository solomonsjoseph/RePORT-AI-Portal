"""Sentinel-file presence asserted on every audit write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.audit.ledger import LedgerWriter


def _phi_kwargs() -> dict:
    return {
        "form": "1A_ICScreening",
        "variable_id": "PATIENT_NAME",
        "action": "drop",
        "rule_taxonomy": "hipaa_safe_harbor:1_names",
        "rule_project_category": "name_address",
        "rationale": "Direct identifier (name); SoT-declared drop",
        "dataset_file": "1A_ICScreening.xlsx",
        "pdf_source": "data/raw/Indo-VAP/annotated_pdfs/1A_ICScreening.pdf",
        "count": 3,
    }


def test_first_write_creates_sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPORTAL_PROCESS_ROLE", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    out = audit_dir / "ledger.json"
    w = LedgerWriter(output_path=out)
    w.add_phi_event(**_phi_kwargs())
    w.flush()
    assert (audit_dir / config.AUDIT_NO_LLM_SENTINEL_NAME).is_file()


def test_missing_sentinel_emits_alarm_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPORTAL_PROCESS_ROLE", raising=False)
    monkeypatch.setattr(config, "AUDIT_SENTINEL_ALARM_PATH", tmp_path / "alarms.jsonl")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    out = audit_dir / "ledger.json"
    w = LedgerWriter(output_path=out)
    w.add_phi_event(**_phi_kwargs())
    w.flush()
    sentinel = audit_dir / config.AUDIT_NO_LLM_SENTINEL_NAME
    assert sentinel.is_file()
    sentinel.unlink()  # simulate tampering
    with pytest.raises(PermissionError):
        w.add_phi_event(**_phi_kwargs())
        w.flush()
    assert (tmp_path / "alarms.jsonl").is_file()
    alarm = json.loads((tmp_path / "alarms.jsonl").read_text().splitlines()[0])
    assert alarm["event"] == "sentinel_missing"
