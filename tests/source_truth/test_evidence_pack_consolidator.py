"""Per-form evidence pack consolidator — masks uncovered/review-required variable_ids."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.evidence_pack_consolidator import build_evidence_packs


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "llm_source" / "sot" / "Mini"


def _write_key(keyfile: Path, byte: int) -> None:
    """Write a 32-byte HMAC key in the hex-text format `phi_scrub.load_key` expects."""
    keyfile.write_text(bytes([byte] * 32).hex())
    keyfile.chmod(0o600)


def test_evidence_pack_per_form(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    files = sorted(out_dir.glob("*.json"))
    assert len(files) == 3


def test_review_required_variable_id_masked(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    rr_pack = json.loads((out_dir / "review_required_form.json").read_text())
    var = rr_pack["variables"][0]
    assert var["id_masked"] is True
    assert var["variable_id"] != "FIELD1"
    raw = (out_dir / "review_required_form.json").read_text()
    assert "FIELD1" not in raw


def test_covered_variable_id_clear(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    covered_pack = json.loads((out_dir / "covered_form.json").read_text())
    vids = [v["variable_id"] for v in covered_pack["variables"]]
    assert "SUBJID" in vids
    assert all(v["id_masked"] is False for v in covered_pack["variables"])


def test_name_phi_kept_clear_variable_masked(tmp_path: Path) -> None:
    """A PHI-named variable kept in clear (action=keep) gets masked."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    pack = json.loads((out_dir / "name_phi_form.json").read_text())
    var = pack["variables"][0]
    assert var["id_masked"] is True
    assert var["variable_id"] != "PT_PHONE"
    assert "PT_PHONE" not in (out_dir / "name_phi_form.json").read_text()


def test_no_row_values_emitted(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    for f in out_dir.glob("*.json"):
        body = json.loads(f.read_text())
        for var in body["variables"]:
            assert "values" not in var
            assert "rows" not in var
            assert "data" not in var
