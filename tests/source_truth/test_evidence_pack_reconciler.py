"""Legacy evidence-pack reconciler — diff legacy per-variable vs new per-form."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.source_truth.legacy_evidence_pack_reconciler import reconcile


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a single dir containing both legacy per-variable and new per-form packs."""
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    # Legacy per-variable packs (variable_id field)
    (ep_dir / "AE_AGE.json").write_text(json.dumps({"variable_id": "AE_AGE"}))
    (ep_dir / "PT_NAME.json").write_text(json.dumps({"variable_id": "PT_NAME"}))
    (ep_dir / "FOO_LEGACY.json").write_text(json.dumps({"variable_id": "FOO_LEGACY"}))
    # New per-form packs (form + variables[])
    (ep_dir / "AE.json").write_text(
        json.dumps({"form": "AE", "variables": [{"variable_id": "AE_AGE"}, {"variable_id": "AE_NEW"}]})
    )
    (ep_dir / "PT.json").write_text(
        json.dumps({"form": "PT", "variables": [{"variable_id": "PT_NAME"}]})
    )
    keyfile = tmp_path / "phi.key"
    keyfile.write_text(bytes([0] * 32).hex())
    keyfile.chmod(0o600)
    hitl_dir = tmp_path / "hitl"
    summary_path = tmp_path / "summary.json"
    return ep_dir, keyfile, hitl_dir, summary_path


def test_legacy_only_emits_hitl_draft(tmp_path: Path) -> None:
    ep_dir, key, hitl_dir, summary = _setup(tmp_path)
    reconcile(evidence_packs_dir=ep_dir, key_path=key, hitl_drafts_dir=hitl_dir, summary_path=summary)
    drafts = list(hitl_dir.glob("*.md"))
    assert len(drafts) == 1
    body = drafts[0].read_text()
    assert "FOO_LEGACY" not in body  # cleartext masked
    assert "legacy-only variable" in body.lower()


def test_summary_counters(tmp_path: Path) -> None:
    ep_dir, key, hitl_dir, summary = _setup(tmp_path)
    reconcile(evidence_packs_dir=ep_dir, key_path=key, hitl_drafts_dir=hitl_dir, summary_path=summary)
    data = json.loads(summary.read_text())
    assert data["legacy_only_count"] == 1
    assert data["new_only_count"] == 1  # AE_NEW
    assert data["matched_count"] == 2  # AE_AGE, PT_NAME


def test_summary_contains_no_cleartext_variable_ids(tmp_path: Path) -> None:
    ep_dir, key, hitl_dir, summary = _setup(tmp_path)
    reconcile(evidence_packs_dir=ep_dir, key_path=key, hitl_drafts_dir=hitl_dir, summary_path=summary)
    raw = summary.read_text()
    for v in ("AE_AGE", "AE_NEW", "PT_NAME", "FOO_LEGACY"):
        assert v not in raw


def test_idempotent(tmp_path: Path) -> None:
    ep_dir, key, hitl_dir, summary = _setup(tmp_path)
    reconcile(evidence_packs_dir=ep_dir, key_path=key, hitl_drafts_dir=hitl_dir, summary_path=summary)
    first = sorted(hitl_dir.glob("*.md"))
    first_body = first[0].read_text()
    reconcile(evidence_packs_dir=ep_dir, key_path=key, hitl_drafts_dir=hitl_dir, summary_path=summary)
    second = sorted(hitl_dir.glob("*.md"))
    assert first == second
    assert second[0].read_text() == first_body
