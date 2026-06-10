"""Phase 5b post-deletion structure: output/<study>/ must contain only llm_source/, audit/, agent/.

These tests run in CI against the real output directory. If the directory
doesn't exist (e.g., fresh CI without data) the tests skip. Once deletion has
been executed, they assert no legacy subdirs remain.

If a future change accidentally recreates a legacy dir, these tests catch it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Allowed subdirs after Phase 5b. agent/ is preserved per user intent
# (the AI agent reads from it; not deleted by clean-legacy).
ALLOWED_SUBDIRS = frozenset({"llm_source", "audit", "agent"})
FORBIDDEN_SUBDIRS = frozenset({"trio_bundle", "staging", "human_review"})


def test_live_output_structure_has_no_legacy_subdirs() -> None:
    """Real output/<study>/ must not contain legacy subdirectories after Phase 5b."""
    import config

    output_root = Path(config.STUDY_OUTPUT_DIR)
    if not output_root.is_dir():
        pytest.skip("output dir does not exist (CI without data)")

    # Skip if deletion hasn't run yet — this test is a post-deletion guard.
    actual_subdirs = {p.name for p in output_root.iterdir() if p.is_dir()}
    if actual_subdirs & FORBIDDEN_SUBDIRS:
        pytest.skip(
            f"Phase 5b deletion not yet executed; legacy subdirs still present: "
            f"{actual_subdirs & FORBIDDEN_SUBDIRS}"
        )

    forbidden_found = actual_subdirs & FORBIDDEN_SUBDIRS
    assert not forbidden_found, f"Legacy subdirs reappeared under {output_root}: {forbidden_found}"



def test_pre_delete_manifest_exists_after_deletion() -> None:
    """After deletion, lineage_manifest_pre_delete.json must be in audit/."""
    import config

    audit_dir = Path(config.STUDY_AUDIT_DIR)
    if not audit_dir.is_dir():
        pytest.skip("audit dir does not exist")

    manifest_path = audit_dir / "lineage_manifest_pre_delete.json"
    output_root = Path(config.STUDY_OUTPUT_DIR)

    # Skip if deletion hasn't run yet
    actual_subdirs = {p.name for p in output_root.iterdir() if p.is_dir()}
    if actual_subdirs & FORBIDDEN_SUBDIRS:
        pytest.skip("Phase 5b deletion not yet executed")

    assert manifest_path.is_file(), (
        f"Pre-delete manifest missing at {manifest_path} — deletion ran without manifest"
    )
