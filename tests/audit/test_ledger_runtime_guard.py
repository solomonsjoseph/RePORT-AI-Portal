"""LedgerWriter refuses writes when REPORTAL_PROCESS_ROLE=llm-agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit.ledger import LedgerWriter


def _phi_kwargs() -> dict:
    return dict(
        form="1A_ICScreening",
        variable_id="PATIENT_NAME",
        action="drop",
        rule_taxonomy="hipaa_safe_harbor:1_names",
        rule_project_category="name_address",
        rationale="Direct identifier (name); SoT-declared drop",
        dataset_file="1A_ICScreening.xlsx",
        pdf_source="data/raw/Indo-VAP/annotated_pdfs/1A_ICScreening.pdf",
        count=3,
    )


def test_write_succeeds_when_role_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPORTAL_PROCESS_ROLE", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    out = audit_dir / "ledger.json"
    w = LedgerWriter(output_path=out)
    w.add_phi_event(**_phi_kwargs())
    w.flush()
    assert out.is_file()


def test_write_succeeds_when_role_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "pipeline")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    out = audit_dir / "ledger.json"
    w = LedgerWriter(output_path=out)
    w.add_phi_event(**_phi_kwargs())
    w.flush()
    assert out.is_file()


def test_write_refused_when_role_llm_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    out = audit_dir / "ledger.json"
    w = LedgerWriter(output_path=out)
    with pytest.raises(PermissionError):
        w.add_phi_event(**_phi_kwargs())
