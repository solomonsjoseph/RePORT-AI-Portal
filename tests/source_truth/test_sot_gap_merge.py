from pathlib import Path

from scripts.source_truth.sot_gap_merge import merge_approved_draft


def test_merge_overwrites_sot_yaml_and_keeps_evidence_pack(tmp_path):
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    pack_drafts_dir = drafts_dir / "evidence_packs"
    pack_drafts_dir.mkdir()

    yaml_draft = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_draft.write_text("form_id: 8_CXR\nvariables: []\n")
    pack_draft = pack_drafts_dir / "8_CXR.json"
    pack_draft.write_text('{"form": "8_CXR"}')

    merge_approved_draft(
        form="8_CXR",
        draft_yaml_path=yaml_draft,
        draft_pack_path=pack_draft,
        sot_dir=sot_dir,
    )

    assert (sot_dir / "8_CXR_policy.yaml").read_text() == "form_id: 8_CXR\nvariables: []\n"
    # Evidence pack draft remains in the drafts dir; final move to llm_source happens in Phase 2:
    assert pack_draft.is_file()


def test_merge_rejects_malformed_yaml(tmp_path):
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    pack_drafts_dir = drafts_dir / "evidence_packs"
    pack_drafts_dir.mkdir()

    yaml_draft = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_draft.write_text("form_id: 8_CXR\n  invalid:: indent\n")
    pack_draft = pack_drafts_dir / "8_CXR.json"
    pack_draft.write_text('{"form": "8_CXR"}')

    import pytest
    with pytest.raises(ValueError, match="malformed"):
        merge_approved_draft(
            form="8_CXR",
            draft_yaml_path=yaml_draft,
            draft_pack_path=pack_draft,
            sot_dir=sot_dir,
        )

    # SoT YAML must NOT have been written
    assert not (sot_dir / "8_CXR_policy.yaml").exists()
    assert not (sot_dir / "8_CXR_policy.yaml.tmp").exists()


def test_merge_raises_file_not_found_for_missing_draft(tmp_path):
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    pack_drafts_dir = drafts_dir / "evidence_packs"
    pack_drafts_dir.mkdir()

    missing_yaml = drafts_dir / "missing_policy.yaml.draft"  # not created
    pack_draft = pack_drafts_dir / "missing.json"
    pack_draft.write_text('{"form": "missing"}')

    import pytest
    with pytest.raises(FileNotFoundError):
        merge_approved_draft(
            form="missing",
            draft_yaml_path=missing_yaml,
            draft_pack_path=pack_draft,
            sot_dir=sot_dir,
        )

    # SoT yaml must NOT have been created (no half-written file).
    assert not (sot_dir / "missing_policy.yaml").exists()
    assert not (sot_dir / "missing_policy.yaml.tmp").exists()
