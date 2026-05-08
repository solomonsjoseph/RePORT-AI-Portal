"""Phase 0 config constants must exist and resolve to project-relative paths."""

from pathlib import Path

import config


def test_phase0_paths_exist_and_are_path_like():
    for name in (
        "SOT_DIR",
        "RAW_PDF_DIR",
        "PILOT_RESULTS_DIR",
        "SOT_GAP_DRAFTS_DIR",
        "SOT_GAP_COVERAGE_PATH",
        "SOT_GAP_REPORT_PATH",
        "SOT_EVIDENCE_PACK_DRAFTS_DIR",
    ):
        value = getattr(config, name)
        assert isinstance(value, (str, Path)), f"{name} not a path-like"
        assert str(value), f"{name} is empty"


def test_sot_dir_resolves_to_default_study():
    indo_vap = Path(str(config.SOT_DIR))
    assert indo_vap.name == "SoT"
    assert "Indo-VAP" in str(indo_vap)
