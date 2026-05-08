"""SoT-driven PHI sweep — finds covered, name-PHI-uncovered, and review-required cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.security.phi_sot_sweep import run_sweep


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "phi_sweep" / "data" / "SoT" / "Mini"


def _write_key(keyfile: Path, byte: int) -> None:
    """Write a 32-byte HMAC key in the hex-text format `phi_scrub.load_key` expects."""
    keyfile.write_text(bytes([byte] * 32).hex())
    keyfile.chmod(0o600)


def test_covered_variables_classified_as_covered(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    findings_path = tmp_path / "findings.json"
    run_sweep(sot_dir=_FIXTURE, output_path=findings_path, key_path=keyfile)
    data = json.loads(findings_path.read_text())
    covered = [f for f in data["findings"] if f["category"] == "covered"]
    assert len(covered) == 3
    assert all(f["form"] == "covered_form" for f in covered)


def test_name_phi_kept_flagged_uncovered(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    findings_path = tmp_path / "findings.json"
    run_sweep(sot_dir=_FIXTURE, output_path=findings_path, key_path=keyfile)
    data = json.loads(findings_path.read_text())
    name_phi = [f for f in data["findings"] if f["category"] == "name_phi_uncovered"]
    assert len(name_phi) == 3
    forms = {f["form"] for f in name_phi}
    assert forms == {"name_phi_form"}


def test_review_required_action_classified(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    findings_path = tmp_path / "findings.json"
    run_sweep(sot_dir=_FIXTURE, output_path=findings_path, key_path=keyfile)
    data = json.loads(findings_path.read_text())
    rr = [f for f in data["findings"] if f["category"] == "review_required_open"]
    assert len(rr) == 1
    assert rr[0]["form"] == "review_required_form"


def test_variable_id_never_appears_in_clear(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    findings_path = tmp_path / "findings.json"
    run_sweep(sot_dir=_FIXTURE, output_path=findings_path, key_path=keyfile)
    raw = findings_path.read_text()
    forbidden_clear = ["SUBJID", "VISITDAT", "PT_NAME", "HHC_PHONE", "AADHAAR", "FIELD1", "AGEYRS"]
    for v in forbidden_clear:
        assert v not in raw, f"clear-text variable_id {v!r} leaked into findings JSON"


def test_summary_counts_match(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    findings_path = tmp_path / "findings.json"
    run_sweep(sot_dir=_FIXTURE, output_path=findings_path, key_path=keyfile)
    data = json.loads(findings_path.read_text())
    assert data["summary"]["covered"] == 3
    assert data["summary"]["name_phi_uncovered"] == 3
    assert data["summary"]["review_required_open"] == 1
    assert data["summary"]["total_variables"] == 7
