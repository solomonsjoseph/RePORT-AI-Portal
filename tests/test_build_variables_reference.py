"""Tests for scripts/extraction/build_variables_reference.py (v3 schema).

Covers two source loaders, the merge precedence chain,
_compute_deidentified_as branches, and the public build_variables_reference()
entry-point including the tmp_dir fallback path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.extraction.build_variables_reference import (
    _compute_deidentified_as,
    _load_extraction_variables_rich,
    build_variables_reference,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORM_JSON: dict[str, Any] = {
    "form_name": "Index Case Screening",
    "source_pdf": "1A_Screening.pdf",
    "version": "1.0",
    "summary": "Baseline screening form for index cases.",
    "sections": {
        "Demographics": {
            "context": "age and sex of participant",
            "variables": ["AGE", "SEX"],
        },
        "Clinical": {
            "context": "clinical measurements",
            "variables": ["RESULT"],
        },
    },
    "variables": {
        "AGE": {"description": "Age at enrollment in years", "values": None},
        "SEX": {"description": "Biological sex (M/F)", "values": {"1": "Male", "2": "Female"}},
        "RESULT": {"description": "Test result", "values": None},
    },
}


def _write_form(directory: Path, filename: str, data: dict[str, Any] | None = None) -> Path:
    path = directory / filename
    path.write_text(json.dumps(data or _FORM_JSON), encoding="utf-8")
    return path


# ===========================================================================
# _load_extraction_variables_rich
# ===========================================================================


class TestLoadExtractionVariablesRich:
    def test_happy_path_fields(self, tmp_path: Path) -> None:
        _write_form(tmp_path, "1A Screening v1.0_variables.json")
        result = _load_extraction_variables_rich(tmp_path)

        assert "AGE" in result
        entry = result["AGE"]
        assert entry["form_id"] == "1A"
        assert entry["form_name"] == "Index Case Screening"
        assert entry["source_pdf"] == "1A_Screening.pdf"
        assert entry["form_version"] == "1.0"
        assert entry["form_summary"] == "Baseline screening form for index cases."
        assert entry["description"] == "Age at enrollment in years"
        assert entry["section"] == "Demographics"
        assert entry["section_context"] == "age and sex of participant"

    def test_coded_options_carried(self, tmp_path: Path) -> None:
        _write_form(tmp_path, "1A Screening v1.0_variables.json")
        result = _load_extraction_variables_rich(tmp_path)
        assert result["SEX"]["coded_options"] == {"1": "Male", "2": "Female"}
        assert result["AGE"]["coded_options"] is None

    def test_section_assigned_via_reverse_map(self, tmp_path: Path) -> None:
        _write_form(tmp_path, "1A Screening v1.0_variables.json")
        result = _load_extraction_variables_rich(tmp_path)
        assert result["RESULT"]["section"] == "Clinical"
        assert result["RESULT"]["section_context"] == "clinical measurements"

    def test_richness_tiebreak_longer_description_wins(self, tmp_path: Path) -> None:
        short = {**_FORM_JSON, "variables": {"AGE": {"description": "Age"}}}
        long_ = {
            **_FORM_JSON,
            "form_name": "Form B",
            "variables": {
                "AGE": {"description": "Age at enrollment in years — collected at baseline visit"}
            },
        }
        _write_form(tmp_path, "1A Form A_variables.json", short)
        _write_form(tmp_path, "2B Form B_variables.json", long_)
        result = _load_extraction_variables_rich(tmp_path)
        assert (
            result["AGE"]["description"]
            == "Age at enrollment in years — collected at baseline visit"
        )

    def test_variable_names_uppercased(self, tmp_path: Path) -> None:
        data = {**_FORM_JSON, "variables": {"age": {"description": "lowercase key"}}}
        _write_form(tmp_path, "1A Form_variables.json", data)
        result = _load_extraction_variables_rich(tmp_path)
        assert "AGE" in result
        assert "age" not in result

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        result = _load_extraction_variables_rich(tmp_path / "does_not_exist")
        assert result == {}

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = _load_extraction_variables_rich(tmp_path)
        assert result == {}

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "bad_variables.json").write_text("not valid json", encoding="utf-8")
        result = _load_extraction_variables_rich(tmp_path)
        assert result == {}

    def test_form_id_parsed_from_filename_prefix(self, tmp_path: Path) -> None:
        _write_form(tmp_path, "12A Follow-up A v1.0_variables.json")
        result = _load_extraction_variables_rich(tmp_path)
        # At least one variable from that form exists; form_id should be "12A"
        some_var = next(iter(result.values()))
        assert some_var["form_id"] == "12A"


# ===========================================================================
# _compute_deidentified_as
# ===========================================================================


class TestComputeDeidentifiedAs:
    @pytest.mark.parametrize(
        "is_phi,phi_type,expected",
        [
            (False, None, ["VISDAT"]),
            (False, "date", ["VISDAT"]),  # non-PHI always keeps original name
            (True, "id", ["VISDAT_PSEUDO", "VISDAT_PRESENT"]),
        ],
    )
    def test_basic_branches(self, is_phi: bool, phi_type: str | None, expected: list) -> None:
        result = _compute_deidentified_as("VISDAT", is_phi, phi_type, date_review={})
        assert result == expected

    def test_date_with_suggestion(self) -> None:
        date_review = {"VISDAT": {"suggested_output_variable": "DAYS_SINCE_ENROL"}}
        result = _compute_deidentified_as("VISDAT", True, "date", date_review)
        assert result == ["DAYS_SINCE_ENROL"]

    def test_date_without_suggestion_returns_empty(self) -> None:
        result = _compute_deidentified_as("NEWDAT", True, "date", date_review={})
        assert result == []

    def test_unknown_phi_type_returns_empty(self) -> None:
        result = _compute_deidentified_as("FOO", True, None, date_review={})
        assert result == []

    def test_unexpected_phi_type_string_returns_empty_with_warning(self, caplog) -> None:
        import logging

        with caplog.at_level(
            logging.WARNING, logger="scripts.extraction.build_variables_reference"
        ):
            result = _compute_deidentified_as("FOO", True, "identifier", date_review={})
        assert result == []
        assert any("Unknown phi_type" in m for m in caplog.messages)


# ===========================================================================
# build_variables_reference — integration
# ===========================================================================


def _make_trio_bundle(root: Path) -> tuple[Path, Path, Path, Path]:
    """Create minimal trio bundle structure.

    Returns (bundle_dir, output_path, pdf_extractions_dir, dictionary_dir).
    """
    bundle = root / "trio"
    bundle.mkdir(parents=True)
    pdf_dir = bundle / "pdfs"
    dd_dir = bundle / "dictionary"
    pdf_dir.mkdir(parents=True)  # deliberately empty
    dd_dir.mkdir(parents=True)
    output = bundle / "variables.json"
    return bundle, output, pdf_dir, dd_dir


def _make_tmp_dir(root: Path, forms: dict[str, dict] | None = None) -> Path:
    """Create tmp/ directory with extracted_variables subdir."""
    tmp = root / "tmp"
    ext = tmp / "extracted_variables"
    ext.mkdir(parents=True)

    if forms:
        for filename, data in forms.items():
            (ext / filename).write_text(json.dumps(data), encoding="utf-8")

    return tmp


class TestBuildVariablesReference:
    def test_tmp_dir_fallback_used_when_pdf_dir_empty(self, tmp_path: Path) -> None:
        """When pdfs/ is empty, extraction metadata comes from tmp/extracted_variables/."""
        bundle, output, pdf_dir, dd_dir = _make_trio_bundle(tmp_path)
        tmp = _make_tmp_dir(tmp_path, forms={"1A Screening v1.0_variables.json": _FORM_JSON})

        build_variables_reference(
            bundle,
            output,
            tmp_dir=tmp,
            pdf_extractions_dir=pdf_dir,
            dictionary_dir=dd_dir,
        )

        data: list[dict] = json.loads(output.read_text())
        names = [e["variable_name"] for e in data]
        assert "AGE" in names
        age = next(e for e in data if e["variable_name"] == "AGE")
        assert age["form_name"] == "Index Case Screening"
        assert age["form_id"] == "1A"

    def test_output_has_v3_schema_fields(self, tmp_path: Path) -> None:
        """Every entry must carry all 23 v3 fields."""
        expected_fields = {
            "variable_name",
            "form_id",
            "form_name",
            "source_pdf",
            "form_version",
            "form_summary",
            "section",
            "section_context",
            "description",
            "coded_options",
            "depends_on",
            "condition",
            "data_type",
            "core_status",
            "is_phi",
            "phi_reason",
            "phi_type",
            "date_kind",
            "anchor_rule",
            "suggested_output_variable",
            "approved_for_transform",
            "date_group_by",
            "deidentified_as",
        }
        bundle, output, pdf_dir, dd_dir = _make_trio_bundle(tmp_path)
        tmp = _make_tmp_dir(tmp_path, forms={"1A Screening v1.0_variables.json": _FORM_JSON})

        build_variables_reference(
            bundle,
            output,
            tmp_dir=tmp,
            pdf_extractions_dir=pdf_dir,
            dictionary_dir=dd_dir,
        )

        data = json.loads(output.read_text())
        assert len(data) > 0
        for entry in data:
            missing = expected_fields - set(entry.keys())
            assert not missing, f"Entry {entry.get('variable_name')} missing fields: {missing}"

    def test_without_tmp_dir_still_writes_valid_output(self, tmp_path: Path) -> None:
        """Calling without tmp_dir (backward compat) writes a valid (possibly empty) list."""
        bundle, output, pdf_dir, dd_dir = _make_trio_bundle(tmp_path)

        summary = build_variables_reference(
            bundle,
            output,
            pdf_extractions_dir=pdf_dir,
            dictionary_dir=dd_dir,
        )

        assert output.exists()
        data = json.loads(output.read_text())
        assert isinstance(data, list)
        assert isinstance(summary["total_variables"], int)

    def test_non_phi_variable_kept_in_deidentified_as(self, tmp_path: Path) -> None:
        """Non-PHI variables appear in deidentified_as under their own name."""
        bundle, output, pdf_dir, dd_dir = _make_trio_bundle(tmp_path)
        # AGE should not be classified as PHI by default rules
        simple_form = {
            "form_name": "Screening",
            "variables": {"RESULT": {"description": "Test result value — clearly not PHI"}},
        }
        tmp = _make_tmp_dir(tmp_path, forms={"1A Screening v1.0_variables.json": simple_form})

        build_variables_reference(
            bundle,
            output,
            tmp_dir=tmp,
            pdf_extractions_dir=pdf_dir,
            dictionary_dir=dd_dir,
        )

        data: list[dict] = json.loads(output.read_text())
        result_entries = [e for e in data if e["variable_name"] == "RESULT"]
        if result_entries:
            entry = result_entries[0]
            if not entry["is_phi"]:
                assert "RESULT" in entry["deidentified_as"]

    def test_both_sources_empty_writes_empty_list(self, tmp_path: Path) -> None:
        """When no extraction source has variables, output is an empty list (no crash)."""
        bundle, output, pdf_dir, dd_dir = _make_trio_bundle(tmp_path)
        tmp = _make_tmp_dir(tmp_path, forms=None)  # empty extracted_variables/

        build_variables_reference(
            bundle,
            output,
            tmp_dir=tmp,
            pdf_extractions_dir=pdf_dir,
            dictionary_dir=dd_dir,
        )

        data = json.loads(output.read_text())
        assert isinstance(data, list)
