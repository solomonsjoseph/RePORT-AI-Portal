"""llm_source dictionary_mapping — relocate jsonl subdirs and write lean ToC."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config

from scripts.source_truth.llm_source_catalogs import (
    build_dictionary_catalog,
    build_study_metadata_catalog,
    relocate_dictionary,
)
from scripts.source_truth.llm_source_catalogs import build_dataset_schema_catalog


def _make_legacy_subdir_layout(legacy: Path, forms: list[str]) -> None:
    legacy.mkdir(parents=True)
    for f in forms:
        sub = legacy / f
        sub.mkdir()
        (sub / f"{f}_table.jsonl").write_text('{"variable_id": "X"}\n')


def test_relocate_preserves_subdir_jsonl_shape(tmp_path: Path) -> None:
    legacy = tmp_path / "trio_bundle" / "dictionary"
    _make_legacy_subdir_layout(legacy, ["tblENROL", "tblCXR"])
    new = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
    relocate_dictionary(legacy_dir=legacy, new_jsonl_dir=new)
    assert (new / "tblENROL" / "tblENROL_table.jsonl").is_file()
    assert (new / "tblCXR" / "tblCXR_table.jsonl").is_file()


def test_relocate_keeps_legacy_intact(tmp_path: Path) -> None:
    legacy = tmp_path / "trio_bundle" / "dictionary"
    _make_legacy_subdir_layout(legacy, ["tblENROL"])
    new = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
    relocate_dictionary(legacy_dir=legacy, new_jsonl_dir=new)
    assert (legacy / "tblENROL" / "tblENROL_table.jsonl").is_file()  # not deleted


def test_relocate_is_idempotent(tmp_path: Path) -> None:
    legacy = tmp_path / "trio_bundle" / "dictionary"
    _make_legacy_subdir_layout(legacy, ["tblENROL"])
    new = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
    relocate_dictionary(legacy_dir=legacy, new_jsonl_dir=new)
    relocate_dictionary(legacy_dir=legacy, new_jsonl_dir=new)
    assert (new / "tblENROL" / "tblENROL_table.jsonl").read_text() == '{"variable_id": "X"}\n'


def test_dictionary_catalog_lists_forms_from_jsonl_subdirs(tmp_path: Path) -> None:
    jsonl_dir = tmp_path / "dictionary_mapping" / "jsonl"
    for form in ["tblENROL", "tblCXR"]:
        sub = jsonl_dir / form
        sub.mkdir(parents=True)
        (sub / f"{form}_table.jsonl").write_text("{}\n")
    catalog_path = tmp_path / "dictionary_mapping" / "catalog.json"
    build_dictionary_catalog(jsonl_dir=jsonl_dir, output_path=catalog_path)
    catalog = json.loads(catalog_path.read_text())
    assert catalog["schema_version"] == 1
    assert set(catalog["forms"].keys()) == {"tblENROL", "tblCXR"}
    assert catalog["forms"]["tblENROL"]["file"] == "jsonl/tblENROL/tblENROL_table.jsonl"
    assert catalog["forms"]["tblCXR"]["file"] == "jsonl/tblCXR/tblCXR_table.jsonl"


def test_catalog_under_size_threshold(tmp_path: Path) -> None:
    jsonl_dir = tmp_path / "dictionary_mapping" / "jsonl"
    for i in range(40):
        sub = jsonl_dir / f"form_{i:02d}"
        sub.mkdir(parents=True)
        (sub / f"form_{i:02d}_table.jsonl").write_text("{}\n")
    catalog_path = tmp_path / "dictionary_mapping" / "catalog.json"
    build_dictionary_catalog(jsonl_dir=jsonl_dir, output_path=catalog_path)
    assert catalog_path.stat().st_size <= config.LEAN_CATALOG_DICTIONARY_MAX_BYTES


def test_catalog_excludes_self(tmp_path: Path) -> None:
    """A pre-existing catalog.json next to jsonl/ must not be listed as a form."""
    mapping_dir = tmp_path / "dictionary_mapping"
    mapping_dir.mkdir()
    (mapping_dir / "catalog.json").write_text("{}")
    jsonl_dir = mapping_dir / "jsonl"
    jsonl_dir.mkdir()
    sub = jsonl_dir / "tblENROL"
    sub.mkdir()
    (sub / "tblENROL_table.jsonl").write_text("{}\n")
    catalog_path = mapping_dir / "catalog.json"
    build_dictionary_catalog(jsonl_dir=jsonl_dir, output_path=catalog_path)
    catalog = json.loads(catalog_path.read_text())
    assert "catalog" not in catalog["forms"]
    assert set(catalog["forms"].keys()) == {"tblENROL"}


def test_study_metadata_catalog_is_lean_toc(tmp_path: Path) -> None:
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    (ep_dir / "10_TST.json").write_text(
        json.dumps(
            {
                "form": "10_TST",
                "study": "Mini",
                "variables": [{"variable_id": "A"}, {"variable_id": "B"}],
            }
        )
    )
    (ep_dir / "11_IGRA.json").write_text(
        json.dumps({"form": "11_IGRA", "study": "Mini", "variables": [{"variable_id": "X"}]})
    )
    out = tmp_path / "study_metadata_catalog.json"
    build_study_metadata_catalog(evidence_packs_dir=ep_dir, output_path=out)
    catalog = json.loads(out.read_text())
    assert catalog["schema_version"] == 1
    assert catalog["study"] == "Mini"
    assert set(catalog["forms"].keys()) == {"10_TST", "11_IGRA"}
    assert catalog["forms"]["10_TST"]["evidence_pack"] == "evidence_packs/10_TST.json"
    assert catalog["forms"]["10_TST"]["variable_count"] == 2


def test_study_metadata_catalog_skips_legacy_per_variable_packs(tmp_path: Path) -> None:
    """Legacy per-variable JSONs in the same dir must be skipped."""
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    # New per-form (kept)
    (ep_dir / "10_TST.json").write_text(
        json.dumps({"form": "10_TST", "study": "Mini", "variables": [{"variable_id": "A"}]})
    )
    # Legacy per-variable (skipped)
    (ep_dir / "AE_AGE.json").write_text(json.dumps({"variable_id": "AE_AGE", "type": "integer"}))
    (ep_dir / "AE_DEATHDAT.json").write_text(json.dumps({"variable_id": "AE_DEATHDAT"}))
    out = tmp_path / "study_metadata_catalog.json"
    build_study_metadata_catalog(evidence_packs_dir=ep_dir, output_path=out)
    catalog = json.loads(out.read_text())
    assert set(catalog["forms"].keys()) == {"10_TST"}


def test_study_metadata_catalog_under_size_threshold(tmp_path: Path) -> None:
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    for i in range(40):
        (ep_dir / f"form_{i:02d}.json").write_text(
            json.dumps(
                {
                    "form": f"form_{i:02d}",
                    "study": "Mini",
                    "variables": [{"variable_id": "A"}] * 50,
                }
            )
        )
    out = tmp_path / "study_metadata_catalog.json"
    build_study_metadata_catalog(evidence_packs_dir=ep_dir, output_path=out)
    assert out.stat().st_size <= config.LEAN_CATALOG_STUDY_METADATA_MAX_BYTES


def test_dataset_schema_catalog_lean_toc_with_handling_summary(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    (sot_dir / "10_TST_policy.yaml").write_text(
        "schema_version: 1\nstudy: Mini\nform: 10_TST\nvariables:\n"
        "  - variable_id: A\n    handling_intent:\n      action: keep\n"
        "  - variable_id: B\n    handling_intent:\n      action: pseudonymize\n"
        "  - variable_id: C\n    handling_intent:\n      action: review_required\n"
    )
    files_dir = tmp_path / "dataset_schema" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "10_TST.jsonl").write_text("{}\n")
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    (ep_dir / "10_TST.json").write_text("{}")
    out = tmp_path / "dataset_schema" / "catalog.json"
    build_dataset_schema_catalog(
        sot_dir=sot_dir,
        dataset_files_dir=files_dir,
        evidence_packs_dir=ep_dir,
        output_path=out,
    )
    catalog = json.loads(out.read_text())
    assert catalog["forms"]["10_TST"]["file"] == "files/10_TST.jsonl"
    assert catalog["forms"]["10_TST"]["sot_yaml"].endswith("10_TST_policy.yaml")
    assert catalog["forms"]["10_TST"]["evidence_pack"] == "../evidence_packs/10_TST.json"
    assert catalog["forms"]["10_TST"]["handling_summary"] == {
        "keep": 1,
        "pseudonymize": 1,
        "review_required": 1,
    }


def test_dataset_schema_catalog_supports_dict_form_variables(tmp_path: Path) -> None:
    """Real production SoT YAMLs use dict-keyed variables (not lists)."""
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    (sot_dir / "F_policy.yaml").write_text(
        "schema_version: 1\nstudy: Mini\nform: F\nvariables:\n"
        "  V1:\n    handling_intent:\n      action: keep\n"
        "  V2:\n    handling_intent:\n      action: drop\n"
    )
    files_dir = tmp_path / "dataset_schema" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "F.jsonl").write_text("{}\n")
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    out = tmp_path / "dataset_schema" / "catalog.json"
    build_dataset_schema_catalog(
        sot_dir=sot_dir,
        dataset_files_dir=files_dir,
        evidence_packs_dir=ep_dir,
        output_path=out,
    )
    catalog = json.loads(out.read_text())
    assert catalog["forms"]["F"]["handling_summary"] == {"keep": 1, "drop": 1}


def test_dataset_schema_catalog_under_size_threshold(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    files_dir = tmp_path / "dataset_schema" / "files"
    files_dir.mkdir(parents=True)
    ep_dir = tmp_path / "evidence_packs"
    ep_dir.mkdir()
    for i in range(40):
        form = f"form_{i:02d}"
        (sot_dir / f"{form}_policy.yaml").write_text(
            f"schema_version: 1\nstudy: Mini\nform: {form}\nvariables:\n"
            + "".join(
                f"  - variable_id: V{j:03d}\n    handling_intent:\n      action: keep\n"
                for j in range(50)
            )
        )
        (files_dir / f"{form}.jsonl").write_text("{}\n")
    out = tmp_path / "dataset_schema" / "catalog.json"
    build_dataset_schema_catalog(
        sot_dir=sot_dir,
        dataset_files_dir=files_dir,
        evidence_packs_dir=ep_dir,
        output_path=out,
    )
    assert out.stat().st_size <= config.LEAN_CATALOG_DATASET_SCHEMA_MAX_BYTES
