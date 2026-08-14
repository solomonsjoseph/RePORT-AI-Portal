from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import scripts.security.llm_source_gate as llm_source_gate
from scripts.security.llm_source_gate import scan_tree_for_phi


@pytest.fixture(autouse=True)
def _reset_gate_caches():
    """The module caches the compiled scrub config and transformed
    manifest per-process (a reasonable production optimization — a single
    publish-gate invocation reads them once). Tests exercise multiple
    distinct configs/manifests within one pytest process, so the cache
    must be cleared before every test."""
    llm_source_gate._cached_transformed = None
    llm_source_gate._transformed_loaded = False
    llm_source_gate._cached_scrub_cfg = None
    llm_source_gate._scrub_cfg_loaded = False
    yield


def _write_scrub_config(path: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
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
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _write_transformed_manifest(audit_dir: Path, payload: dict) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    from scripts.security.phi_scrub import PHI_TRANSFORMED_FILENAME

    (audit_dir / PHI_TRANSFORMED_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_scan_allows_date_field_present_in_transformed_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Step 8: a date-classified field the scrubber PROVABLY transformed
    for this exact dataset file — evidence via phi_scrub_transformed.json,
    not mere rule membership — is allowed through the artifact gate."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)

    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)
    _write_transformed_manifest(audit_dir, {"6_HIV.jsonl": {"date": ["HIV_HIVDAT"]}})

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


def test_scan_blocks_date_field_absent_from_transformed_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Step 8 (D2 regression guard): a date-classified field with NO
    evidence of transformation — the manifest exists but doesn't list this
    field for this file — is blocked even though a date rule matches its
    name. This is the inversion of the old (wrong) rule-membership check:
    "classified as a date rule" is no longer treated as a proxy for
    "actually transformed"."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)

    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)
    # Manifest exists but records no transformation for HIV_HIVDAT in this file.
    _write_transformed_manifest(audit_dir, {"6_HIV.jsonl": {"date": []}})

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "6_HIV.jsonl").write_text(
        json.dumps({"HIV_HIVDAT": "2020-01-01"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "DATE_ISO"


def test_scan_blocks_date_field_when_manifest_missing(tmp_path: Path, monkeypatch) -> None:
    """Missing/unreadable transformed manifest -> allow nothing (fail-closed),
    matching the prior ``except Exception`` posture."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)
    audit_dir = tmp_path / "audit_missing"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "6_HIV.jsonl").write_text(
        json.dumps({"HIV_HIVDAT": "2020-01-01"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok


def test_scan_allows_rid_pseudonym_shape(tmp_path: Path) -> None:
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "clean.jsonl").write_text(
        json.dumps({"SUBJID": "RID_SUBJ_abcdefghijkl"}) + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_blocks_raw_subject_id_shape(tmp_path: Path) -> None:
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "bad.jsonl").write_text(
        json.dumps({"SUBJID": "SC1234"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name.startswith("SUBJECT_ID")


def test_scan_blocks_compact_date_left_raw_in_date_classified_field(
    tmp_path: Path, monkeypatch
) -> None:
    """Step 8: DATE_COMPACT fires on an 8-digit raw compact date in a field
    the compiled rule catalog classifies as date-shaped for this field
    NAME (scoping), and which the transformed manifest confirms was NEVER
    actually transformed for this file (evidence) — the exact D2 fail-open
    (classified but not transformed) this pattern exists to catch."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config, date_fields=["^SC_GENOPROCDAT$"])
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)
    _write_transformed_manifest(audit_dir, {"compact.jsonl": {"date": []}})

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "compact.jsonl").write_text(
        json.dumps({"SC_GENOPROCDAT": "25092017"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "DATE_COMPACT"


def test_scan_allows_compact_date_with_transformation_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """A compact 8-digit date that IS listed in the transformed manifest
    for this file (e.g. a correctly-jittered DMY8 value re-emitted in the
    same 8-digit shape, per Step 2's format-preservation) is exempt from
    DATE_COMPACT — same manifest-backed evidence check as every other
    DATE_-prefixed pattern."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config, date_fields=["^SC_GENOPROCDAT$"])
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)
    _write_transformed_manifest(audit_dir, {"compact.jsonl": {"date": ["SC_GENOPROCDAT"]}})

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "compact.jsonl").write_text(
        json.dumps({"SC_GENOPROCDAT": "30092017"}) + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_does_not_flag_compact_number_outside_date_classified_fields(
    tmp_path: Path, monkeypatch
) -> None:
    """DATE_COMPACT is scoped to fields the compiled rule catalog
    classifies as date-shaped — an 8-digit accession number in a
    non-date-classified field is not a false positive."""
    import config

    scrub_config = tmp_path / "phi_scrub.yaml"
    _write_scrub_config(scrub_config, date_fields=["^SC_GENOPROCDAT$"])
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", scrub_config, raising=False)
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", audit_dir / "phi_scrub_report.json", raising=False)
    _write_transformed_manifest(audit_dir, {"other.jsonl": {"date": []}})

    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "other.jsonl").write_text(
        json.dumps({"LAB_ACCESSION": "12345678"}) + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok



def test_scan_allows_url_and_date_in_dictionary_documentation_fields(tmp_path: Path) -> None:
    """Step 9a follow-up: a citation URL in 'Notes'/'Group notes / thoughts'
    and a placeholder-date convention note in 'Variable Endings', inside a
    dictionary-leg table file, are recognized study documentation -- not a
    subject value -- and do not block."""
    root = tmp_path / "llm_source"
    dict_dir = root / "dictionary_mapping" / "jsonl"
    dict_dir.mkdir(parents=True)
    (dict_dir / "tblMED_table.jsonl").write_text(
        json.dumps(
            {"Question": "Medication name", "Notes": "See https://www.whocc.no/atc_ddd_index/ for codes"}
        )
        + "\n",
        encoding="utf-8",
    )
    (dict_dir / "Codelists_table_2.jsonl").write_text(
        json.dumps({"Variable Endings": "Use 1900-01-01 as an Unknown date"}) + "\n",
        encoding="utf-8",
    )

    assert scan_tree_for_phi(root).ok


def test_scan_still_blocks_dictionary_documentation_field_for_other_pattern_types(
    tmp_path: Path,
) -> None:
    """The allowlist is scoped to exactly DATE_ISO and URL -- a different
    pattern type (e.g. an email address) in the SAME allowed field name
    must still block. Never a blanket per-field exemption."""
    root = tmp_path / "llm_source"
    dict_dir = root / "dictionary_mapping" / "jsonl"
    dict_dir.mkdir(parents=True)
    (dict_dir / "tblMED_table.jsonl").write_text(
        json.dumps({"Notes": "Contact data.manager@example.com with questions"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "EMAIL"


def test_scan_still_blocks_url_in_non_documentation_field(tmp_path: Path) -> None:
    """The allowlist is scoped to the three declared field names -- a URL
    in a differently-named field of the same dictionary table still
    blocks."""
    root = tmp_path / "llm_source"
    dict_dir = root / "dictionary_mapping" / "jsonl"
    dict_dir.mkdir(parents=True)
    (dict_dir / "tblMED_table.jsonl").write_text(
        json.dumps({"Collection Instruction": "See https://example.com/protocol"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "URL"


def test_scan_still_blocks_url_in_documentation_field_outside_dictionary_table_shape(
    tmp_path: Path,
) -> None:
    """The allowlist is scoped to dictionary-table-shaped filenames -- a
    'Notes' field in a non-'_table.jsonl' file (e.g. a dataset-leg file)
    still blocks, even though the field name matches."""
    root = tmp_path / "llm_source"
    data_dir = root / "dataset_schema" / "files"
    data_dir.mkdir(parents=True)
    (data_dir / "8_CXR.jsonl").write_text(
        json.dumps({"Notes": "See https://example.com/protocol"}) + "\n",
        encoding="utf-8",
    )

    result = scan_tree_for_phi(root)
    assert not result.ok
    assert result.findings[0].pattern_name == "URL"