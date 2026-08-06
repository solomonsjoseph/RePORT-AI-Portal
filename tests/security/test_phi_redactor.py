"""Phase 3 redactor — masks PHI variable_ids in LLM-visible artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.security.phi_id_masker import mask_variable_id
from scripts.security.phi_redactor import redact


def _write_key(p: Path, byte: int) -> None:
    p.write_text(bytes([byte] * 32).hex())
    p.chmod(0o600)


def _ep_dir_with_form(tmp_path: Path, form: str, variables: list[dict]) -> Path:
    d = tmp_path / "evidence_packs"
    d.mkdir()
    (d / f"{form}.json").write_text(
        json.dumps({"schema_version": 1, "form": form, "study": "Mini", "variables": variables})
    )
    return d


def test_covered_variable_returns_clear_id_and_description(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = _ep_dir_with_form(
        tmp_path,
        "F",
        [
            {
                "variable_id": "AGEYRS",
                "id_masked": False,
                "handling_action": "cap",
                "description": "Age in years",
            }
        ],
    )
    token, desc = redact("F", "AGEYRS", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token == "AGEYRS"  # noqa: S105
    assert desc == "Age in years"


def test_review_required_variable_returns_phi_token(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    # Pack stores the masked token (matches what evidence_pack_consolidator emits)
    masked = mask_variable_id("F", "FIELD1", key_path=keyfile)
    ep_dir = _ep_dir_with_form(
        tmp_path,
        "F",
        [
            {
                "variable_id": masked,
                "id_masked": True,
                "handling_action": "review_required",
                "description": "Disambiguation needed",
            }
        ],
    )
    token, desc = redact("F", "FIELD1", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token.startswith("<phi:")
    assert token.endswith(">")
    assert "FIELD1" not in token
    assert desc == "Disambiguation needed"


def test_phi_named_variable_kept_clear_returns_phi_token(tmp_path: Path) -> None:
    """A PHI-named variable that the consolidator masked (action=keep) is also masked here."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    masked = mask_variable_id("F", "PT_PHONE", key_path=keyfile)
    ep_dir = _ep_dir_with_form(
        tmp_path,
        "F",
        [
            {
                "variable_id": masked,
                "id_masked": True,
                "handling_action": "keep",
                "description": "Patient phone (PHI #5)",
            }
        ],
    )
    token, desc = redact("F", "PT_PHONE", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token.startswith("<phi:")
    assert "PHONE" not in token
    assert desc == "Patient phone (PHI #5)"


def test_unknown_variable_defaults_to_mask(tmp_path: Path) -> None:
    """When the variable is missing from the evidence pack, default-to-mask."""
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = _ep_dir_with_form(tmp_path, "F", [])
    token, desc = redact("F", "UNKNOWN_VAR", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token.startswith("<phi:")
    assert desc == ""


def test_missing_evidence_pack_dir_defaults_to_mask(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = tmp_path / "evidence_packs_missing"  # never created
    token, _desc = redact("F", "X", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token.startswith("<phi:")


def test_malformed_evidence_pack_defaults_to_mask(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0)
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    (ep_dir / "F.json").write_text("{not json")
    token, _desc = redact("F", "X", evidence_packs_dir=ep_dir, key_path=keyfile)
    assert token.startswith("<phi:")
