"""Tests for scripts/extraction/dataset_cleanup.py — the dataset audit envelope.

Since Note 18 this module is audit-only: file-level junk/duplicate handling moved
to raw-file dedup before extraction (the dataset-deduplication skill at
orchestrator phase 2 + the manifest ``reject:`` gate), so ``clean_trio_datasets``
no longer reads row values or removes/merges staging files. It serializes the
upstream extraction column-drop events into the unified audit report + per-dataset
``as_written`` cleanup ledgers. These tests cover that surviving behavior.
"""

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


class TestAuditOnlyBehavior:
    """Note 18: cleanup no longer removes junk/duplicate files from staging."""

    def test_junk_named_file_is_not_removed(self, monkeypatch_config: Path) -> None:
        """A file named like legacy junk stays — junk handling moved upstream."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Paste Errors.jsonl", scrubbed_records([{"a": 1}]))

        report = clean_trio_datasets(ds, study_name="TestStudy")
        assert (ds / "Paste Errors.jsonl").exists(), "audit-only cleanup must not delete files"
        assert report.junk_removed == []
        assert report.duplicates_merged == []

    def test_duplicate_named_pair_is_not_merged(self, monkeypatch_config: Path) -> None:
        """A legacy suspected-duplicate pair is left intact — dedup moved upstream."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        records = scrubbed_records([{"SUBJID": f"S{i}"} for i in range(5)])
        _write_jsonl(ds / "14_CaseControl.jsonl", records)
        _write_jsonl(ds / "14_Case_Control.jsonl", records)

        report = clean_trio_datasets(ds, study_name="TestStudy")
        assert (ds / "14_CaseControl.jsonl").exists()
        assert (ds / "14_Case_Control.jsonl").exists()
        assert report.duplicates_merged == []

    def test_unscrubbed_error_retained_for_back_compat(self) -> None:
        """UnscrubbedDatasetError is importable (back-compat) though no longer raised."""
        assert issubclass(UnscrubbedDatasetError, Exception)


class TestEdgeCases:
    def test_empty_directory(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.TRIO_DATASETS_DIR
        report = clean_trio_datasets(ds)
        assert report.total_actions == 0

    def test_nonexistent_directory(self, tmp_path: Path, monkeypatch_config: Path) -> None:
        missing = tmp_path / "nope"
        report = clean_trio_datasets(missing)
        assert report.total_actions == 0


class TestAuditSerialization:
    """clean_trio_datasets emits the unified audit at AUDIT_DATASET_REPORT_PATH."""

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

    def test_audit_written_atomically_to_config_path(self, monkeypatch_config: Path) -> None:
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)

        audit_path = config.AUDIT_DATASET_REPORT_PATH
        clean_trio_datasets(
            ds,
            extracted_drop_events=[],
            study_name="TestStudy",
        )

        assert audit_path.exists()
        assert audit_path.parent.is_dir()
        payload = json.loads(audit_path.read_text())
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

    def test_audit_envelope_errors_and_skipped_empty_on_clean_run(
        self, monkeypatch_config: Path
    ) -> None:
        """errors and skipped appear as empty lists on an audit-only run."""
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
    """clean_trio_datasets writes per-dataset cleanup ledgers from extraction drops."""

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

    def test_non_column_scope_not_in_ledger(self, monkeypatch_config: Path) -> None:
        """extracted_drop_events with scope != 'dataset-column' produce no column drops."""
        import config

        ds = config.STAGING_DATASETS_DIR
        ds.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds / "Some_Form.jsonl", scrubbed_records([{"a": 1}]))

        non_column_event = {
            "scope": "dataset-junk-file",
            "name": "Some_Form",
            "file": "Some_Form.jsonl",
            "sheet": None,
            "reason": "n/a",
            "kept": None,
        }

        clean_trio_datasets(
            ds,
            extracted_drop_events=[non_column_event],
            study_name="TestStudy",
        )

        envelope = json.loads(self._ledger_path("Some_Form.jsonl").read_text())
        col_drops = [e for e in envelope["events"] if e["action"] == "dataset_column_drop"]
        assert col_drops == [], "non-column scope must not produce dataset_column_drop events"
