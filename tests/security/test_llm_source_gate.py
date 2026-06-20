from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.security.llm_source_gate import scan_tree_for_phi


@pytest.fixture(autouse=True)
def _gate_scrub_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the gate's session-global scrub-config cache per test and point its
    resolution at a per-test minimal config (only ``HIVDAT`` approved as a date
    field).

    Task A7: ``load_scrub_config()`` now deep-merges
    ``config/_defaults/phi_scrub.yaml`` (base) with the per-study override, so
    the gate's ``load_scrub_config()`` reads ``CONFIG_DEFAULTS_DIR``. Without
    this, the gate would cache the REAL default config (broad date approvals)
    and stop blocking unapproved ISO dates. Pointing CONFIG_DEFAULTS_DIR at a
    minimal HIVDAT-only config restores the per-test intent."""
    import config
    import scripts.security.llm_source_gate as gate

    defaults_dir = tmp_path / "_scrub_defaults"
    defaults_dir.mkdir(parents=True, exist_ok=True)
    _write_scrub_config(defaults_dir / "phi_scrub.yaml")
    monkeypatch.setattr(config, "CONFIG_DEFAULTS_DIR", defaults_dir)
    # Reset the gate's module-global config cache so this test re-resolves.
    monkeypatch.setattr(gate, "_cached_scrub_cfg", None, raising=False)
    monkeypatch.setattr(gate, "_scrub_cfg_loaded", False, raising=False)


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
        json.dumps(
            {
                "HIV_HIVDAT": "2020-01-01",
                "_provenance": {"extraction_utc": "2026-05-19T00:00:00+00:00"},
            }
        )
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


def test_scan_allows_fid_column_names_in_sot_joined_view(tmp_path: Path) -> None:
    """Regression: Family-ID COLUMN NAMES ('FID', 'FID2'..'FID5' — family-member
    index headers) in SoT joined-view metadata must not trip the SUBJECT_ID
    heuristic. Only real multi-digit FID *values* are subject PHI; header tokens
    are not. (N3: the joined query view is the sole LLM-facing SoT file; policy
    YAML + dataset schema are fenced to the audit zone, not under llm_source.)"""
    root = tmp_path / "llm_source"
    joined_dir = root / "SoT" / "9_EEval" / "joined"
    joined_dir.mkdir(parents=True)
    (joined_dir / "9_EEval_joined_query_view.yaml").write_text(
        "\n".join(
            [
                "study: T",
                "form: 9_EEval",
                "variables:",
                "  FID: {dataset: {phi_action: pseudonymize}}",
                "  FID2: {dataset: {source_order: 130}}",
                "  FID3: {dataset: {source_order: 131}}",
                "  FID4: {dataset: {source_order: 132}}",
                "  FID5: {dataset: {source_order: 133}}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_blocks_real_fid_value(tmp_path: Path) -> None:
    """A real Family-ID *value* (FID + >=4 digits) leaking into a published file
    must still be blocked — tightening the pattern must not weaken detection."""
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "bad.jsonl").write_text(
        json.dumps({"family_id": "FID12345"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)

    assert not result.ok
    assert result.findings[0].pattern_name.startswith("SUBJECT_ID")


# ── DICT-DATE_ISO: dictionary_mapping/ date-pattern exemption ────────────────


def test_date_iso_exempted_inside_dictionary_mapping(tmp_path: Path) -> None:
    """DICT-DATE_ISO (part 1): an ISO date string inside a dictionary_mapping/
    file must NOT trigger a scan finding — it is help-text prose, not PHI."""
    root = tmp_path / "llm_source"
    dict_dir = root / "dictionary_mapping" / "jsonl"
    dict_dir.mkdir(parents=True)
    (dict_dir / "codelist.jsonl").write_text(
        json.dumps(
            {
                "code": 99,
                "label": "Unknown",
                "note": "Use 1900-01-01 as an Unknown date sentinel",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert result.ok, (
        f"DATE_ISO inside dictionary_mapping/ should be exempt, but got finding: {result.findings}"
    )


def test_date_iso_still_blocks_outside_dictionary_mapping(tmp_path: Path) -> None:
    """DICT-DATE_ISO (part 2): the SAME ISO date string in a non-dictionary_mapping
    JSONL must be blocked — the exemption is scoped to dictionary_mapping/ only."""
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "dataset.jsonl").write_text(
        json.dumps({"some_date": "1900-01-01"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "DATE_ISO"


def test_blocking_pattern_still_detected_inside_dictionary_mapping(tmp_path: Path) -> None:
    """DICT-DATE_ISO (part 3): only DATE-class patterns are exempted inside
    dictionary_mapping/. A blocking ID/contact pattern (email) inside that
    subtree must still raise a finding."""
    root = tmp_path / "llm_source"
    dict_dir = root / "dictionary_mapping" / "jsonl"
    dict_dir.mkdir(parents=True)
    (dict_dir / "codelist_bad.jsonl").write_text(
        json.dumps(
            {
                "code": 1,
                "label": "Contact",
                "email": "admin@example.com",  # blocking EMAIL pattern
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "EMAIL"
