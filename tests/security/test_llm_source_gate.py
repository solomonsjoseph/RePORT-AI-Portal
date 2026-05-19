from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.security.llm_source_gate import scan_tree_for_phi


def _write_scrub_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "subject_id_fields": ["SUBJID"],
                "date_fields": ["HIVDAT"],
                "id_fields": [],
                "drop_fields": [],
                "keep_fields": [],
                "cap_fields": {},
                "generalize_fields": {},
                "suppress_small_cell_fields": [],
                "birthdate_field": "",
                "max_jitter_days": 30,
            }
        ),
        encoding="utf-8",
    )


def test_scan_allows_configured_jittered_date_field(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "6_HIV.jsonl").write_text(
        json.dumps({"HIV_HIVDAT": "2020-01-01", "_provenance": {"extraction_utc": "2026-05-19T00:00:00+00:00"}})
        + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_blocks_unapproved_date_field(tmp_path: Path, monkeypatch) -> None:
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "bad.jsonl").write_text(
        json.dumps({"unapproved_date": "2020-01-01"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)

    assert not result.ok
    assert result.findings[0].pattern_name == "DATE_ISO"


def test_scan_allows_rid_pseudonym_shape(tmp_path: Path) -> None:
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "safe.jsonl").write_text(
        json.dumps({"SUBJID": "RID_SUBJ_abcdefghijkl"}) + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_blocks_raw_subject_id_shape(tmp_path: Path) -> None:
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "bad.jsonl").write_text(
        json.dumps({"SUBJID": "SUBJ_123456"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)

    assert not result.ok
    assert result.findings[0].pattern_name.startswith("SUBJECT_ID")
