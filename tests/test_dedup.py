"""Tests for scripts/extraction/dedup.py — duplicate detection and removal."""

from __future__ import annotations

from typing import Any

import pandas as pd

from scripts.extraction.dedup import (
    clean_cross_form_duplicates,
    clean_duplicate_columns,
    remove_within_file_duplicates,
    variable_richness_score,
)


class TestCleanDuplicateColumns:
    def test_removes_identical_suffix_column(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2, 3], "SUBJID2": [1, 2, 3], "AGE": [20, 30, 40]})
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert "SUBJID" in result.columns
        assert "SUBJID2" not in result.columns

    def test_keeps_differing_suffix_column(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2, 3], "SUBJID2": [4, 5, 6], "AGE": [20, 30, 40]})
        result, events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert "SUBJID" in result.columns
        assert "SUBJID2" in result.columns
        assert events == []

    def test_removes_null_suffix_column(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2, 3], "SUBJID2": [None, None, None]})
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert "SUBJID2" not in result.columns

    def test_no_duplicates_unchanged(self) -> None:
        df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        result, events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert list(result.columns) == ["A", "B"]
        assert events == []

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame()
        result, events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert result.empty
        assert events == []

    def test_multiple_suffix_patterns(self) -> None:
        df = pd.DataFrame(
            {
                "NAME": ["a", "b"],
                "NAME_2": ["a", "b"],
                "NAME3": ["a", "b"],
            }
        )
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert "NAME" in result.columns
        # Duplicates of NAME should be removed
        remaining = [c for c in result.columns if c.startswith("NAME")]
        assert len(remaining) == 1

    def test_underscore_suffix_with_base_missing(self) -> None:
        df = pd.DataFrame({"RESULT_2": [1, 2], "OTHER": [3, 4]})
        result, events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        # No base "RESULT" → keep as-is
        assert "RESULT_2" in result.columns
        assert events == []

    def test_preserves_row_count(self) -> None:
        df = pd.DataFrame({"A": range(100), "A2": range(100)})
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert len(result) == 100

    def test_column_order_stability(self) -> None:
        df = pd.DataFrame({"Z": [1], "A": [2], "M": [3]})
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert list(result.columns) == ["Z", "A", "M"]

    def test_case_sensitivity(self) -> None:
        df = pd.DataFrame({"subjid": [1], "SUBJID": [1]})
        result, _events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        # Different case = different columns, both kept
        assert len(result.columns) == 2

    # ── Drop-event reporting (Task 1 of cleanup-propagation plan) ──

    def test_returns_tuple_of_dataframe_and_list(self) -> None:
        df = pd.DataFrame({"A": [1], "B": [2]})
        result = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        assert isinstance(result, tuple)
        assert len(result) == 2
        cleaned_df, events = result
        assert isinstance(cleaned_df, pd.DataFrame)
        assert isinstance(events, list)

    def test_duplicate_event_has_correct_reason_and_kept(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2, 3], "SUBJID2": [1, 2, 3]})
        _cleaned, events = clean_duplicate_columns(
            df, source_file="01_Demographics.jsonl", sheet="Sheet1"
        )
        assert len(events) == 1
        event = events[0]
        assert event == {
            "scope": "dataset-column",
            "name": "SUBJID2",
            "file": "01_Demographics.jsonl",
            "sheet": "Sheet1",
            "reason": "100% identical to 'SUBJID'",
            "kept": "SUBJID",
        }

    def test_null_event_has_correct_reason_and_kept_none(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2, 3], "SUBJID2": [None, None, None]})
        _cleaned, events = clean_duplicate_columns(
            df, source_file="01_Demographics.jsonl", sheet=None
        )
        assert len(events) == 1
        event = events[0]
        assert event == {
            "scope": "dataset-column",
            "name": "SUBJID2",
            "file": "01_Demographics.jsonl",
            "sheet": None,
            "reason": "entirely null",
            "kept": None,
        }

    def test_multi_column_produces_one_event_per_drop(self) -> None:
        df = pd.DataFrame(
            {
                "SUBJID": [1, 2, 3],
                "SUBJID2": [1, 2, 3],  # duplicate of SUBJID
                "NAME": ["a", "b", "c"],
                "NAME_2": [None, None, None],  # null, base NAME exists
                "AGE": [20, 30, 40],  # untouched
            }
        )
        cleaned, events = clean_duplicate_columns(df, source_file="mixed.jsonl", sheet="S1")
        # The cleaned frame keeps base columns + AGE
        assert set(cleaned.columns) == {"SUBJID", "NAME", "AGE"}
        # One event per dropped column, in encounter order
        assert len(events) == 2
        names_in_events = [e["name"] for e in events]
        assert names_in_events == ["SUBJID2", "NAME_2"]

        subjid_event = next(e for e in events if e["name"] == "SUBJID2")
        assert subjid_event["reason"] == "100% identical to 'SUBJID'"
        assert subjid_event["kept"] == "SUBJID"
        assert subjid_event["file"] == "mixed.jsonl"
        assert subjid_event["sheet"] == "S1"
        assert subjid_event["scope"] == "dataset-column"

        name_event = next(e for e in events if e["name"] == "NAME_2")
        assert name_event["reason"] == "entirely null"
        assert name_event["kept"] is None
        assert name_event["file"] == "mixed.jsonl"
        assert name_event["sheet"] == "S1"
        assert name_event["scope"] == "dataset-column"

    def test_every_event_has_required_keys(self) -> None:
        df = pd.DataFrame({"SUBJID": [1, 2], "SUBJID2": [1, 2]})
        _cleaned, events = clean_duplicate_columns(df, source_file="demo.jsonl", sheet=None)
        required = {"scope", "name", "file", "sheet", "reason", "kept"}
        for event in events:
            assert required <= set(event.keys())


class TestVariableRichnessScore:
    def test_empty_dict_returns_zero(self) -> None:
        score, _, _ = variable_richness_score({})
        assert score == 0

    def test_rich_variable_higher_score(self) -> None:
        poor: dict[str, Any] = {"variable_name": "X"}
        rich: dict[str, Any] = {
            "variable_name": "X",
            "description": "A detailed description",
            "coded_options": {"1": "Yes", "2": "No"},
            "data_type": "categorical",
        }
        score_poor = variable_richness_score(poor)[0]
        score_rich = variable_richness_score(rich)[0]
        assert score_rich > score_poor


class TestRemoveWithinFileDuplicates:
    def test_no_duplicates_unchanged(self) -> None:
        data: dict[str, Any] = {
            "variables": {
                "VAR_A": {"description": "A"},
                "VAR_B": {"description": "B"},
            }
        }
        result = remove_within_file_duplicates(data)
        assert result["duplicates_removed"] == 0

    def test_removes_case_insensitive_duplicate(self) -> None:
        data: dict[str, Any] = {
            "variables": {
                "SUBJID": {"description": "Subject ID", "data_type": "string"},
                "subjid": {"description": ""},
            }
        }
        result = remove_within_file_duplicates(data)
        assert result["duplicates_removed"] == 1

    def test_dry_run_preserves_all(self) -> None:
        data: dict[str, Any] = {
            "variables": {
                "X": {"description": "first"},
                "x": {"description": "second"},
            }
        }
        result = remove_within_file_duplicates(data, dry_run=True)
        assert result["duplicates_removed"] == 1
        assert "cleaned_data" not in result


class TestCleanCrossFormDuplicates:
    def test_no_duplicates_returns_empty(self) -> None:
        forms: dict[str, dict[str, Any]] = {
            "form_a": {"variables": {"A": {"description": "a"}}},
            "form_b": {"variables": {"B": {"description": "b"}}},
        }
        result = clean_cross_form_duplicates(forms)
        assert result == {}

    def test_removes_cross_form_duplicate(self) -> None:
        forms: dict[str, dict[str, Any]] = {
            "form_a": {"variables": {"SHARED": {"description": "rich", "data_type": "string"}}},
            "form_b": {
                "variables": {
                    "SHARED": {"description": ""},
                    "OTHER": {"description": "x"},
                }
            },
        }
        result = clean_cross_form_duplicates(forms)
        assert "form_b" in result
        assert "SHARED" not in result["form_b"]["variables"]
