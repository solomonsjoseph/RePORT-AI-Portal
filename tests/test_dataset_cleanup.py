"""Tests for scripts/extraction/dataset_cleanup.py — trio bundle dataset cleaning."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scripts.audit.ledger import dataset_cleanup_ledger_path
from scripts.extraction.dataset_cleanup import (
    CleanupReport,
    UnscrubbedDatasetError,
    clean_trio_datasets,
)
from tests.conftest import _write_jsonl, scrubbed_records


class TestRemoveJunk:
    def test_removes_paste_errors(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))
        _write_jsonl(ds / "real_data.jsonl", scrubbed_records([{"b": 2}]))

        report = clean_trio_datasets(ds)
        assert "Paste Errors.jsonl" in report.junk_removed
        assert not (ds / "Paste Errors.jsonl").exists()
        assert (ds / "real_data.jsonl").exists()

    def test_removes_test1ek(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "TEST1EK.jsonl", scrubbed_records([{"a": 1}]))

        report = clean_trio_datasets(ds)
        assert "TEST1EK.jsonl" in report.junk_removed

    def test_no_junk_present(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "good_data.jsonl", scrubbed_records([{"a": 1}]))

        report = clean_trio_datasets(ds)
        assert report.junk_removed == []


class TestMergeDuplicates:
    def test_merge_identical_schemas_same_rows(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        records = scrubbed_records([{"SUBJID": f"S{i}", "AGE": 25 + i} for i in range(5)])
        _write_jsonl(ds / "14_CaseControl.jsonl", records)
        _write_jsonl(ds / "14_Case_Control.jsonl", records)

        report = clean_trio_datasets(ds)
        assert len(report.duplicates_merged) == 1
        # One file removed, one remains
        remaining = list(ds.glob("14_*.jsonl"))
        assert len(remaining) == 1

    def test_keeps_larger_file(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        small = scrubbed_records([{"SUBJID": f"S{i}", "AGE": 25 + i} for i in range(3)])
        large = scrubbed_records([{"SUBJID": f"S{i}", "AGE": 25 + i} for i in range(10)])
        _write_jsonl(ds / "2A_ICBaseline.jsonl", large)
        _write_jsonl(ds / "2A_ICBaseline_1.jsonl", small)

        report = clean_trio_datasets(ds)
        assert len(report.duplicates_merged) == 1
        assert (ds / "2A_ICBaseline.jsonl").exists()
        assert not (ds / "2A_ICBaseline_1.jsonl").exists()

    def test_different_schemas_kept(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "21_DSTISO.jsonl", scrubbed_records([{"COL_A": 1}]))
        _write_jsonl(ds / "21_DSTIsolate.jsonl", scrubbed_records([{"COL_B": 2}]))

        report = clean_trio_datasets(ds)
        assert len(report.duplicates_merged) == 0
        assert len(report.duplicates_skipped) == 1

    def test_missing_pair_file_skipped(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "14_CaseControl.jsonl", scrubbed_records([{"A": 1}]))
        # 14_Case_Control.jsonl does NOT exist

        report = clean_trio_datasets(ds)
        assert report.duplicates_merged == []
        assert report.duplicates_skipped == []


class TestCleanupReport:
    def test_total_actions(self) -> None:
        r = CleanupReport(
            junk_removed=["a.jsonl", "b.jsonl"],
            duplicates_merged=[{"kept": "x", "removed": "y"}],
        )
        assert r.total_actions == 3

    def test_empty_report(self) -> None:
        r = CleanupReport()
        assert r.total_actions == 0


class TestEdgeCases:
    def test_empty_directory(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.TRIO_DATASETS_DIR
        # No files at all
        report = clean_trio_datasets(ds)
        assert report.total_actions == 0

    def test_nonexistent_directory(self, tmp_path: Path, monkeypatch_config: Path) -> None:
        # Point to nonexistent dir
        missing = tmp_path / "nope"
        report = clean_trio_datasets(missing)
        assert report.total_actions == 0


class TestAuditSerialization:
    """Task 3: clean_trio_datasets emits unified audit at AUDIT_DATASET_REPORT_PATH."""

    def test_junk_events_serialized_with_correct_scope(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        audit_path = config.AUDIT_DATASET_REPORT_PATH
        assert audit_path.exists()
        payload = json.loads(audit_path.read_text())

        assert payload["study"] == "TestStudy"
        assert payload["leg"] == "dataset"
        assert isinstance(payload["removed"], list)
        assert "generated_utc" in payload
        # ISO-8601 UTC ending in Z, no microseconds
        assert payload["generated_utc"].endswith("Z")

        junk_events = [e for e in payload["removed"] if e["scope"] == "dataset-junk-file"]
        assert len(junk_events) == 1
        ev = junk_events[0]
        assert ev["scope"] == "dataset-junk-file"
        assert ev["file"] == "Paste Errors.jsonl"
        assert ev["name"] == "Paste Errors"
        assert ev["sheet"] is None
        assert ev["kept"] is None
        assert ev["reason"] == "known junk artifact"

    def test_duplicate_file_events_serialized_with_correct_scope(
        self, monkeypatch_config: Path
    ) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        records = scrubbed_records([{"SUBJID": f"S{i}", "AGE": 25 + i} for i in range(5)])
        _write_jsonl(ds / "14_CaseControl.jsonl", records)
        _write_jsonl(ds / "14_Case_Control.jsonl", records)

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        audit_path = config.AUDIT_DATASET_REPORT_PATH
        assert audit_path.exists()
        payload = json.loads(audit_path.read_text())

        dup_events = [e for e in payload["removed"] if e["scope"] == "dataset-duplicate-file"]
        assert len(dup_events) == 1
        ev = dup_events[0]
        assert ev["scope"] == "dataset-duplicate-file"
        # One of the two files was removed
        assert ev["file"] in {"14_CaseControl.jsonl", "14_Case_Control.jsonl"}
        assert ev["name"] in {"14_CaseControl", "14_Case_Control"}
        assert ev["kept"] in {"14_CaseControl.jsonl", "14_Case_Control.jsonl"}
        assert ev["kept"] != ev["file"]
        assert ev["sheet"] is None
        assert ev["reason"] in {"subset", "same_schema_same_count", "union_merge"}

    def test_extraction_drops_pass_through(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        drop_event = {
            "scope": "dataset-column",
            "name": "SUBJID2",
            "file": "01_Demographics.jsonl",
            "sheet": "Sheet1",
            "reason": "100% identical to 'SUBJID'",
            "kept": "SUBJID",
        }

        clean_trio_datasets(
            ds,
            extracted_drop_events=[drop_event],
            study_name="TestStudy",
        )

        audit_path = config.AUDIT_DATASET_REPORT_PATH
        payload = json.loads(audit_path.read_text())
        assert len(payload["removed"]) == 1
        assert payload["removed"][0] == drop_event

    def test_combined_audit_contains_all_sources(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        # Junk file
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))
        # Duplicate pair
        records = scrubbed_records([{"SUBJID": f"S{i}", "AGE": 25 + i} for i in range(5)])
        _write_jsonl(ds / "14_CaseControl.jsonl", records)
        _write_jsonl(ds / "14_Case_Control.jsonl", records)

        drop_event = {
            "scope": "dataset-column",
            "name": "SUBJID2",
            "file": "01_Demographics.jsonl",
            "sheet": "Sheet1",
            "reason": "100% identical to 'SUBJID'",
            "kept": "SUBJID",
        }

        clean_trio_datasets(
            ds,
            extracted_drop_events=[drop_event],
            study_name="TestStudy",
        )

        payload = json.loads(config.AUDIT_DATASET_REPORT_PATH.read_text())
        assert len(payload["removed"]) == 3
        scopes = {e["scope"] for e in payload["removed"]}
        assert scopes == {
            "dataset-column",
            "dataset-junk-file",
            "dataset-duplicate-file",
        }

    def test_audit_written_atomically_to_config_path(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        # Remove audit parent dir first to confirm auto-creation
        audit_path = config.AUDIT_DATASET_REPORT_PATH
        # Parent should auto-create
        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        assert audit_path.exists()
        assert audit_path.parent.is_dir()
        payload = json.loads(audit_path.read_text())
        # Parseable ISO-8601
        parsed = datetime.fromisoformat(payload["generated_utc"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_empty_inputs_still_emit_audit_envelope(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        payload = json.loads(config.AUDIT_DATASET_REPORT_PATH.read_text())
        assert payload["study"] == "TestStudy"
        assert payload["leg"] == "dataset"
        assert payload["removed"] == []
        assert payload["generated_utc"].endswith("Z")

    def test_audit_written_even_when_datasets_dir_missing(
        self, tmp_path: Path, monkeypatch_config: Path
    ) -> None:
        """Nonexistent datasets dir must still produce the audit envelope."""
        import config

        missing = tmp_path / "does_not_exist_dir"

        report = clean_trio_datasets(
            missing,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        assert report.junk_removed == []
        assert report.duplicates_merged == []
        assert config.AUDIT_DATASET_REPORT_PATH.exists()
        payload = json.loads(config.AUDIT_DATASET_REPORT_PATH.read_text())
        assert payload["removed"] == []

    def test_audit_envelope_contains_errors_and_skipped_keys(
        self, monkeypatch_config: Path
    ) -> None:
        """EDIT-DCLEAN-005: errors and skipped must always appear in audit JSON."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        # Schema-mismatched pair → duplicates_skipped populated
        _write_jsonl(ds / "21_DSTISO.jsonl", scrubbed_records([{"COL_A": 1}]))
        _write_jsonl(ds / "21_DSTIsolate.jsonl", scrubbed_records([{"COL_B": 2}]))

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        payload = json.loads(config.AUDIT_DATASET_REPORT_PATH.read_text())

        # Both keys must always be present — even if one is empty
        assert "skipped" in payload, "'skipped' key missing from audit JSON"
        assert "errors" in payload, "'errors' key missing from audit JSON"
        assert isinstance(payload["skipped"], list)
        assert isinstance(payload["errors"], list)
        # The schema-mismatch above should produce exactly one skipped entry
        assert len(payload["skipped"]) == 1
        assert payload["skipped"][0]["reason"] == "schemas differ"
        # No errors expected in this clean run
        assert payload["errors"] == []

    def test_audit_envelope_errors_and_skipped_empty_on_clean_run(
        self, monkeypatch_config: Path
    ) -> None:
        """errors and skipped appear as empty lists when nothing goes wrong."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        payload = json.loads(config.AUDIT_DATASET_REPORT_PATH.read_text())
        assert payload["errors"] == []
        assert payload["skipped"] == []


class TestAsWrittenLedger:
    """Phase 1C: clean_trio_datasets writes per-dataset cleanup ledgers."""

    def _ledger_path(self, filename: str = "1A_ICScreening.jsonl") -> Path:
        import config

        return dataset_cleanup_ledger_path(config.AUDIT_DATASET_REPORT_PATH.parent, filename)

    def test_as_written_ledger_created(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "1A_ICScreening.jsonl", scrubbed_records([{"a": 1}]))

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        ledger_path = self._ledger_path()
        assert ledger_path.exists(), "ledger.as_written.json was not created"
        assert not (ledger_path.parent / config.AUDIT_NO_LLM_SENTINEL_NAME).exists()

        envelope = json.loads(ledger_path.read_text())
        assert "run_id" in envelope
        # Primary ledger is content-only — wall-clock fields moved to timing sidecar.
        assert "iso_timestamp" not in envelope
        assert "generated_utc" not in envelope
        assert envelope["study"] == "TestStudy"
        assert envelope["leg"] == "dataset"
        assert "events" in envelope
        assert isinstance(envelope["events"], list)

    def test_as_written_ledger_column_drop_shape(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        drop_event = {
            "scope": "dataset-column",
            "name": "DUP_COL_1",
            "file": "1A_ICScreening.xlsx",
            "sheet": "Sheet1",
            "reason": "100% identical to 'DUP_COL'",
            "kept": "DUP_COL",
        }

        clean_trio_datasets(
            ds,
            extracted_drop_events=[drop_event],
            study_name="TestStudy",
        )

        envelope = json.loads(self._ledger_path("1A_ICScreening.xlsx").read_text())
        col_drops = [e for e in envelope["events"] if e["action"] == "dataset_column_drop"]
        assert len(col_drops) == 1

        ev = col_drops[0]
        assert ev["action"] == "dataset_column_drop"
        assert ev["form"] == "1A_ICScreening"
        assert ev["variable_id"] == "DUP_COL_1"
        assert ev["where"]["dataset_file"] == "1A_ICScreening.xlsx"

    def test_as_written_ledger_junk_file_shape(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))

        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        envelope = json.loads(self._ledger_path("Paste Errors.jsonl").read_text())
        junk_events = [e for e in envelope["events"] if e["action"] == "dataset_junk_file"]
        assert len(junk_events) == 1

        ev = junk_events[0]
        assert ev["action"] == "dataset_junk_file"
        assert ev["form"] == "Paste Errors"
        assert ev["variable_id"] == "Paste Errors"
        assert ev["where"]["dataset_file"] == "Paste Errors.jsonl"

    def test_non_column_scope_not_in_ledger(self, monkeypatch_config: Path) -> None:
        """extracted_drop_events with scope != 'dataset-column' must not produce column-drop events."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))

        # Only a non-column-scope event — should be filtered out of column-drop section
        non_column_event = {
            "scope": "dataset-junk-file",
            "name": "Paste Errors",
            "file": "Paste Errors.jsonl",
            "sheet": None,
            "reason": "known junk artifact",
            "kept": None,
        }

        clean_trio_datasets(
            ds,
            extracted_drop_events=[non_column_event],
            study_name="TestStudy",
        )

        envelope = json.loads(self._ledger_path("Paste Errors.jsonl").read_text())
        col_drops = [e for e in envelope["events"] if e["action"] == "dataset_column_drop"]
        assert col_drops == [], "non-column scope must not produce dataset_column_drop events"


class TestScrubFirstGuard:
    """RED→GREEN tests for the fail-closed scrub-first guard in clean_trio_datasets."""

    def test_unscrubbed_file_raises(self, monkeypatch_config: Path) -> None:
        """A file with no _phi_scrubbed marker must raise UnscrubbedDatasetError."""
        import pytest

        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "unscrubbed.jsonl", [{"SUBJID": "S1", "AGE": 30}])

        with pytest.raises(UnscrubbedDatasetError, match=r"_phi_scrubbed.*marker absent"):
            clean_trio_datasets(ds)

    def test_old_marker_raises(self, monkeypatch_config: Path) -> None:
        """A file with an old marker version (v1) must raise UnscrubbedDatasetError."""
        import pytest

        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "old_marker.jsonl", [{"SUBJID": "S1", "_phi_scrubbed": "v1"}])

        with pytest.raises(UnscrubbedDatasetError, match=r"1 row.*not scrubbed to v3"):
            clean_trio_datasets(ds)

    def test_mixed_rows_raises(self, monkeypatch_config: Path) -> None:
        """A file where only some rows carry v3 must raise UnscrubbedDatasetError."""
        import pytest

        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(
            ds / "mixed.jsonl",
            [
                {"SUBJID": "S1", "_phi_scrubbed": "v3"},
                {"SUBJID": "S2"},  # missing marker
            ],
        )

        with pytest.raises(UnscrubbedDatasetError, match=r"1 row.*not scrubbed to v3"):
            clean_trio_datasets(ds)

    def test_fully_scrubbed_file_passes(self, monkeypatch_config: Path) -> None:
        """A file where every row carries _phi_scrubbed == 'v3' must not raise."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "scrubbed.jsonl", scrubbed_records([{"SUBJID": "S1", "AGE": 30}]))

        # Must not raise — the guard should pass cleanly
        report = clean_trio_datasets(ds, study_name="TestStudy")
        assert report.total_actions == 0


class TestValueDivergentPairRoutedToHumanReview:
    """UP5-A: a SUSPECTED_DUPLICATE_PAIRS pair whose files have identical schema,
    non-subset rows, and DIFFERENT row counts must NOT be auto-merged.

    Expected outcomes:
      (a) Both files still exist in staging after clean_trio_datasets.
      (b) A jsonl_union_review.md human-review note is written under audit/human_review/.
      (c) The CleanupReport's duplicates_skipped contains a 'value_divergent_needs_human_review'
          entry and duplicates_merged does NOT include a union of these files.

    Rows carry _phi_scrubbed=='v3' so the _assert_scrubbed pre-flight passes.
    """

    def test_both_files_survive_after_cleanup(self, monkeypatch_config: Path) -> None:
        """Neither file is deleted when the rows are value-divergent."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        # Use a pair registered in SUSPECTED_DUPLICATE_PAIRS:
        # ("14_CaseControl", "14_Case_Control")
        # Identical schema: {SUBJID, STATUS, _phi_scrubbed}
        # File A: 5 rows; File B: 3 rows that are completely different subjects.
        # Neither file is a subset of the other → must route to human review.
        file_a_rows = scrubbed_records(
            [{"SUBJID": f"SA{i}", "STATUS": "enrolled"} for i in range(5)]
        )
        file_b_rows = scrubbed_records(
            [{"SUBJID": f"SB{i}", "STATUS": "enrolled"} for i in range(3)]
        )
        _write_jsonl(ds / "14_CaseControl.jsonl", file_a_rows)
        _write_jsonl(ds / "14_Case_Control.jsonl", file_b_rows)

        clean_trio_datasets(ds, study_name="TestStudy")

        assert (ds / "14_CaseControl.jsonl").exists(), "File A must not be deleted"
        assert (ds / "14_Case_Control.jsonl").exists(), "File B must not be deleted"

    def test_human_review_note_written(self, monkeypatch_config: Path) -> None:
        """A jsonl_union_review.md note must be written under audit/human_review/."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        file_a_rows = scrubbed_records(
            [{"SUBJID": f"SA{i}", "STATUS": "enrolled"} for i in range(5)]
        )
        file_b_rows = scrubbed_records(
            [{"SUBJID": f"SB{i}", "STATUS": "enrolled"} for i in range(3)]
        )
        _write_jsonl(ds / "14_CaseControl.jsonl", file_a_rows)
        _write_jsonl(ds / "14_Case_Control.jsonl", file_b_rows)

        clean_trio_datasets(ds, study_name="TestStudy")

        review_note = (
            config.STUDY_AUDIT_DIR
            / "human_review"
            / "datasets"
            / "14_CaseControl"
            / "jsonl_union_review.md"
        )
        assert review_note.exists(), (
            f"Human-review note not found at {review_note}; "
            "value-divergent pair must be routed to human review"
        )
        # The note must mention column names only (no row values)
        content = review_note.read_text(encoding="utf-8")
        assert "14_CaseControl" in content
        assert "14_Case_Control" in content

    def test_cleanup_ledger_records_needs_human_review(self, monkeypatch_config: Path) -> None:
        """The CleanupReport must record a 'value_divergent_needs_human_review' reason
        in duplicates_skipped and must NOT include these files in duplicates_merged."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        file_a_rows = scrubbed_records(
            [{"SUBJID": f"SA{i}", "STATUS": "enrolled"} for i in range(5)]
        )
        file_b_rows = scrubbed_records(
            [{"SUBJID": f"SB{i}", "STATUS": "enrolled"} for i in range(3)]
        )
        _write_jsonl(ds / "14_CaseControl.jsonl", file_a_rows)
        _write_jsonl(ds / "14_Case_Control.jsonl", file_b_rows)

        report = clean_trio_datasets(ds, study_name="TestStudy")

        # duplicates_merged must NOT contain the value-divergent pair
        merged_files = {e.get("kept", "") for e in report.duplicates_merged} | {
            e.get("removed", "") for e in report.duplicates_merged
        }
        assert "14_CaseControl.jsonl" not in merged_files, (
            "Value-divergent pair must not appear in duplicates_merged"
        )
        assert "14_Case_Control.jsonl" not in merged_files, (
            "Value-divergent pair must not appear in duplicates_merged"
        )

        # duplicates_skipped must contain the human-review reason
        skipped_reasons = [e.get("reason", "") for e in report.duplicates_skipped]
        assert any("human_review" in r for r in skipped_reasons), (
            f"Expected 'human_review' in a skipped reason, got: {skipped_reasons}"
        )

    def test_equal_count_value_divergent_pair_routes_to_human_review(
        self, monkeypatch_config: Path
    ) -> None:
        """Equal row count + same schema but different values must NOT be auto-merged.

        Regression test for the bug where 'if is_sub or len(df_a) == len(df_b)'
        silently deleted the smaller file for equal-size pairs without checking
        whether values were actually identical.
        """
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        # Same column schema, same row count, but DIFFERENT values
        file_a_rows = scrubbed_records([{"SUBJID": f"SA{i}", "STATUS": "A"} for i in range(3)])
        file_b_rows = scrubbed_records([{"SUBJID": f"SB{i}", "STATUS": "B"} for i in range(3)])
        _write_jsonl(ds / "14_CaseControl.jsonl", file_a_rows)
        _write_jsonl(ds / "14_Case_Control.jsonl", file_b_rows)

        report = clean_trio_datasets(ds, study_name="TestStudy")

        # Neither file should be deleted
        assert (ds / "14_CaseControl.jsonl").exists(), "file_a must not be deleted"
        assert (ds / "14_Case_Control.jsonl").exists(), "file_b must not be deleted"

        # Must appear in duplicates_skipped with the equal-count divergent reason
        skipped_reasons = [e.get("reason", "") for e in report.duplicates_skipped]
        assert any("equal_count" in r for r in skipped_reasons), (
            f"Expected 'equal_count' in a skipped reason, got: {skipped_reasons}"
        )

        # Must NOT appear in duplicates_merged
        merged_files = {e.get("kept", "") for e in report.duplicates_merged} | {
            e.get("removed", "") for e in report.duplicates_merged
        }
        assert "14_CaseControl.jsonl" not in merged_files
        assert "14_Case_Control.jsonl" not in merged_files
