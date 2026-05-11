"""Per-form evidence pack consolidator — masks uncovered/review-required variable_ids."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.evidence_pack_consolidator import build_evidence_packs


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "llm_source" / "sot" / "Mini"


@pytest.fixture
def packs_dir(tmp_path: Path) -> Path:
    """Build the full per-form evidence pack set once and return its output dir."""
    keyfile = tmp_path / "phi.key"
    keyfile.write_text(bytes([0] * 32).hex())
    keyfile.chmod(0o600)
    out_dir = tmp_path / "evidence_packs"
    build_evidence_packs(sot_dir=_FIXTURE, output_dir=out_dir, key_path=keyfile)
    return out_dir


def test_evidence_pack_per_form(packs_dir: Path) -> None:
    assert len(sorted(packs_dir.glob("*.json"))) == 3


def test_review_required_variable_id_masked(packs_dir: Path) -> None:
    rr_path = packs_dir / "review_required_form.json"
    rr_pack = json.loads(rr_path.read_text())
    var = rr_pack["variables"][0]
    assert var["id_masked"] is True
    assert var["variable_id"] != "FIELD1"
    assert "FIELD1" not in rr_path.read_text()


def test_covered_variable_id_clear(packs_dir: Path) -> None:
    covered_pack = json.loads((packs_dir / "covered_form.json").read_text())
    vids = [v["variable_id"] for v in covered_pack["variables"]]
    assert "SUBJID" in vids
    assert all(v["id_masked"] is False for v in covered_pack["variables"])


def test_name_phi_kept_clear_variable_masked(packs_dir: Path) -> None:
    """A PHI-named variable kept in clear (action=keep) gets masked."""
    pack_path = packs_dir / "name_phi_form.json"
    pack = json.loads(pack_path.read_text())
    var = pack["variables"][0]
    assert var["id_masked"] is True
    assert var["variable_id"] != "PT_PHONE"
    assert "PT_PHONE" not in pack_path.read_text()


def test_no_row_values_emitted(packs_dir: Path) -> None:
    for f in packs_dir.glob("*.json"):
        body = json.loads(f.read_text())
        for var in body["variables"]:
            assert "values" not in var
            assert "rows" not in var
            assert "data" not in var
