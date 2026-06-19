"""N22: holding producers deposit value-free notes into the per-form queue."""

from __future__ import annotations

from pathlib import Path

from scripts.security.phi_review import FormReviewApproval, HeldReason
from scripts.skills.extract_to_llm_source import (
    _write_classification_hold_note,
    _write_scrub_quarantine_note,
)


def test_scrub_quarantine_note_is_value_free(tmp_path: Path) -> None:
    note = tmp_path / "scrub_quarantine_review.md"
    _write_scrub_quarantine_note(
        note,
        {"form": "9_SAE", "kept": 40, "quarantined": 3, "reasons": ["date_unshiftable"], "elevated": True},
    )
    text = note.read_text(encoding="utf-8")
    assert "9_SAE" in text
    assert "Quarantined rows:** 3" in text
    assert "ELEVATED" in text
    assert "date_unshiftable" in text


def test_classification_hold_note_is_value_free(tmp_path: Path) -> None:
    item = FormReviewApproval(
        form_name="2A_Base",
        status="held",
        attempts=1,
        actions={},
        classifications=(),
        reasons=("duplicate normalized header: subjid",),
        rule_bundle_sha256="abc",
        source_mode="pinned",
        held_reason=HeldReason("tried X", "ambiguous Y", "resolve Z"),
        force_drop_headers=("SIGNATURE",),
    )
    note = tmp_path / "classification_review.md"
    _write_classification_hold_note(note, item)
    text = note.read_text(encoding="utf-8")
    assert "2A_Base" in text
    assert "held" in text
    assert "duplicate normalized header" in text
    assert "resolve Z" in text
    assert "Force-dropped direct-identifier columns: 1" in text
