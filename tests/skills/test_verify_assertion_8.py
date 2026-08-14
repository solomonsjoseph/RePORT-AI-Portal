"""Tests for the widened Assertion 8 — PHI absence across all three published
LLM-readable legs (dataset, dictionary, SoT).

Step 9: ``_verify_assertion_8_phi_absence`` used to scan only
``dataset_schema/files/``. The dictionary (``dictionary_mapping/jsonl/``) and
SoT (``SoT/``) legs are also inside the agent read zone
(``scripts/ai_assistant/file_access.py``) but were never scanned. This file
exercises the widened assertion end-to-end against real ``scan_tree_for_phi``
(no mocking) with synthetic ``tmp_path`` fixtures — never real study data.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.skills.extract_to_llm_source import _verify_assertion_8_phi_absence


def _make_clean_dataset_leg(dataset_files_dir: Path) -> None:
    dataset_files_dir.mkdir(parents=True, exist_ok=True)
    (dataset_files_dir / "clean.jsonl").write_text(
        json.dumps({"SUBJID": "RID_SUBJ_abcdefghijkl", "TST_TU": "12"}) + "\n",
        encoding="utf-8",
    )


def _make_clean_dictionary_leg(dictionary_dir: Path) -> None:
    dictionary_dir.mkdir(parents=True, exist_ok=True)
    (dictionary_dir / "codelist.jsonl").write_text(
        json.dumps({"Question": "TST result", "Type": "code", "Units": None}) + "\n",
        encoding="utf-8",
    )


def _make_clean_sot_leg(sot_dir: Path) -> None:
    policy_dir = sot_dir / "10_TST" / "pdf"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "10_TST_policy.yaml").write_text(
        "study: Example Study\n"
        "variables:\n"
        "  TST_TU:\n"
        "    section: form_body\n"
        "    pdf_question: 'TU result:'\n"
        "    type: code\n",
        encoding="utf-8",
    )


class TestAssertion8AllCleanPasses:
    def test_pass_when_all_three_legs_clean(self, tmp_path: Path) -> None:
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        _make_clean_dictionary_leg(dictionary_dir)
        _make_clean_sot_leg(sot_dir)

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "pass"
        assert detail == ""

    def test_pass_when_dictionary_and_sot_legs_absent(self, tmp_path: Path) -> None:
        """A study that has not published a dictionary/SoT leg yet must not
        fail assertion 8 — ``scan_tree_for_phi`` treats a missing directory
        as clean, matching the existing dataset-leg convention."""
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        # dictionary_dir and sot_dir intentionally not created

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "pass"
        assert detail == ""


class TestAssertion8DictionaryLegFails:
    def test_fail_on_phi_shaped_value_in_dictionary_leg(self, tmp_path: Path) -> None:
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        dictionary_dir.mkdir(parents=True, exist_ok=True)
        (dictionary_dir / "bad_table.jsonl").write_text(
            json.dumps({"Notes": "contact patient.jane@example.com for questions"}) + "\n",
            encoding="utf-8",
        )
        _make_clean_sot_leg(sot_dir)

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "fail"
        assert detail
        # Value-free contract: the matched email must never appear in detail.
        assert "example.com" not in detail
        assert "jane" not in detail


class TestAssertion8SotLegFails:
    def test_fail_on_phi_shaped_value_in_sot_leg(self, tmp_path: Path) -> None:
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        _make_clean_dictionary_leg(dictionary_dir)

        policy_dir = sot_dir / "10_TST" / "pdf"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "10_TST_policy.yaml").write_text(
            "study: Example Study\n"
            "variables:\n"
            "  TST_TU:\n"
            "    section: form_body\n"
            "    pdf_question: 'reach investigator at doctor.smith@example.com'\n"
            "    type: code\n",
            encoding="utf-8",
        )

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "fail"
        assert detail
        assert "example.com" not in detail
        assert "smith" not in detail

    def test_structural_fid_declarations_do_not_false_positive(self, tmp_path: Path) -> None:
        """Step 9b regression guard: the `variables:` mapping key and the
        `"name": "FID..."` schema field are structural column/variable-name
        declarations, not subject data, and must not block publish."""
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        _make_clean_dictionary_leg(dictionary_dir)

        form_dir = sot_dir / "10_TST"
        (form_dir / "dataset").mkdir(parents=True, exist_ok=True)
        (form_dir / "dataset" / "10_TST_schema.json").write_text(
            "{\n"
            '  "columns": [\n'
            "    {\n"
            '      "name": "FID",\n'
            '      "phi_action": "pseudonymize",\n'
            '      "source_order": 1\n'
            "    }\n"
            "  ],\n"
            '  "form": "10_TST"\n'
            "}\n",
            encoding="utf-8",
        )
        (form_dir / "joined").mkdir(parents=True, exist_ok=True)
        (form_dir / "joined" / "10_TST_joined_query_view.yaml").write_text(
            "study: Example Study\n"
            "form: 10_TST\n"
            "variables:\n"
            "  FID:\n"
            "    pdf:\n"
            "      section: header\n"
            "      question: 'Family ID:'\n"
            "      phi: pseudonymize\n"
            "    dataset:\n"
            "      phi_action: pseudonymize\n"
            "runtime_fields: {}\n",
            encoding="utf-8",
        )
        (form_dir / "pdf").mkdir(parents=True, exist_ok=True)
        (form_dir / "pdf" / "10_TST_policy.yaml").write_text(
            "study: Example Study\n"
            "variables:\n"
            "  FID:\n"
            "    section: header\n"
            "    pdf_question: 'Family ID:'\n"
            "    type: identifier\n"
            "    phi: pseudonymize\n"
            "    widget: identifier entry field aligned to printed identifier prompt\n",
            encoding="utf-8",
        )

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "pass"
        assert detail == ""

    def test_fid_inside_discrepancies_still_blocks(self, tmp_path: Path) -> None:
        """A `discrepancies[].dataset_column_binding[]` entry naming FID2 is
        a list item, not the `variables:` mapping key — a different line
        shape that must stay BLOCKING (subject-adjacent CRF content per the
        plan), never allowlisted alongside the structural declarations."""
        dataset_files_dir = tmp_path / "llm_source" / "dataset_schema" / "files"
        dictionary_dir = tmp_path / "llm_source" / "dictionary_mapping" / "jsonl"
        sot_dir = tmp_path / "llm_source" / "SoT"

        _make_clean_dataset_leg(dataset_files_dir)
        _make_clean_dictionary_leg(dictionary_dir)

        policy_dir = sot_dir / "9_EEval" / "pdf"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "9_EEval_policy.yaml").write_text(
            "study: Example Study\n"
            "variables:\n"
            "  EE_BOAT:\n"
            "    section: unmatched_dataset\n"
            "    pdf_question: null\n"
            "discrepancies:\n"
            "- kind: dataset_header_without_visible_pdf_widget\n"
            "  dataset_column_binding:\n"
            "  - FID2\n"
            "  - FID3\n",
            encoding="utf-8",
        )

        status, detail = _verify_assertion_8_phi_absence(
            dataset_files_dir, dictionary_dir, sot_dir
        )

        assert status == "fail"
        assert detail
