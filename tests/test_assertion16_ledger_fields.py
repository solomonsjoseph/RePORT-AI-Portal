"""N10 assertion 16: PHI ledger events must carry taxonomy/jurisdictions/method."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.skills.extract_to_llm_source import _verify_assertion_16_ledger_fields_complete


def _write_ledger(audit_dir: Path, stem: str, events: list[dict]) -> None:
    path = dataset_phi_ledger_path(audit_dir, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"events": events}), encoding="utf-8")


_COMPLETE = {
    "variable_id": "DOB",
    "action": "jitter_date",
    "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": ["USA"]},
    "method": {"name": "SANT_date_jitter"},
}


def test_assertion16_passes_complete_events(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write_ledger(audit, "1_Form", [_COMPLETE])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_fails_missing_taxonomy(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    bad = {**_COMPLETE, "rule": {"taxonomy": None, "jurisdictions": ["USA"]}}
    _write_ledger(audit, "1_Form", [bad])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"
    assert "missing" in detail


def test_assertion16_fails_missing_method(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    bad = {k: v for k, v in _COMPLETE.items() if k != "method"}
    _write_ledger(audit, "1_Form", [bad])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"


def test_assertion16_vacuous_pass_when_no_events(tmp_path: Path) -> None:
    # All-keep forms have no PHI events → assertion passes vacuously.
    audit = tmp_path / "audit"
    _write_ledger(audit, "1_Form", [])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass"
