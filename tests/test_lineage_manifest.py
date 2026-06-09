"""Tests for scripts/utils/lineage.py (Stage 2f lineage manifest emission)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.security.secure_env import ZoneViolationError
from scripts.utils import lineage


class TestHashPath:
    def test_deterministic_sha256(self, tmp_path: Path) -> None:
        target = tmp_path / "file.bin"
        content = b"lineage-test-content"
        target.write_bytes(content)
        assert lineage.hash_path(target) == hashlib.sha256(content).hexdigest()


class TestEmitLineageManifest:
    def test_full_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Override the output-zone marker so tmp_path qualifies.
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw_datasets = tmp_path / "raw_ds"
        raw_datasets.mkdir()
        (raw_datasets / "a.csv").write_bytes(b"raw-a")
        (raw_datasets / "b.csv").write_bytes(b"raw-b")

        raw_dict = tmp_path / "raw_dict"
        raw_dict.mkdir()
        (raw_dict / "dict.xlsx").write_bytes(b"raw-dict")

        llm_src = tmp_path / "llm_source"
        (llm_src / "datasets").mkdir(parents=True)
        (llm_src / "datasets" / "scrubbed.jsonl").write_bytes(b"clean-rows")
        (llm_src / "variables.json").write_bytes(b"{}")

        audit = tmp_path / "audit"
        audit.mkdir()
        (audit / "phi_scrub_report.json").write_text(
            json.dumps(
                {
                    "study": "TEST",
                    "generated_utc": "2026-04-23T12:00:00Z",
                    "compliance_posture": "safe_harbor",
                    "scrubbed": [{"scope": "phi-scrub-drop", "field": "STAFF_NAME", "count": 3}],
                }
            )
        )

        manifest_path = audit / "lineage_manifest.json"
        payload = lineage.emit_lineage_manifest(
            study_name="TEST",
            raw_datasets_dir=raw_datasets,
            raw_dictionary_dir=raw_dict,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=manifest_path,
        )

        assert manifest_path.is_file()
        on_disk = json.loads(manifest_path.read_text())
        assert on_disk["study"] == "TEST"
        assert on_disk["pipeline_version"] == "2.0.0"
        assert on_disk["compliance_posture"] == "safe_harbor"
        # Inputs enumerated
        ds_paths = {r["path"] for r in on_disk["inputs"]["datasets"]}
        assert ds_paths == {"a.csv", "b.csv"}
        # Hash carried
        a_meta = next(r for r in on_disk["inputs"]["datasets"] if r["path"] == "a.csv")
        assert a_meta["sha256"] == hashlib.sha256(b"raw-a").hexdigest()
        # Outputs enumerated
        llm_paths = {r["path"] for r in on_disk["outputs"]["llm_source"]}
        assert "datasets/scrubbed.jsonl" in llm_paths
        assert "variables.json" in llm_paths
        # Step metadata
        assert "phi_scrub" in on_disk["steps"]
        assert on_disk["steps"]["phi_scrub"]["event_count"] == 1
        # Return payload matches file
        assert payload == on_disk

    def test_rejects_manifest_path_outside_output_zone(self, tmp_path: Path) -> None:
        bad = tmp_path / "lineage_manifest.json"
        with pytest.raises(ZoneViolationError):
            lineage.emit_lineage_manifest(
                study_name="TEST",
                raw_datasets_dir=tmp_path,
                raw_dictionary_dir=None,
                raw_pdfs_dir=None,
                llm_source_dir=tmp_path,
                audit_dir=tmp_path,
                pipeline_version="x",
                compliance_posture="disabled",
                manifest_path=bad,
            )

    def test_emit_lineage_manifest_uses_llm_source_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """emit_lineage_manifest accepts llm_source_dir, records outputs.llm_source."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "file.csv").write_text("a,b\n1,2\n")

        audit = tmp_path / "audit"
        audit.mkdir()

        llm_source_dir = tmp_path / "llm_source"
        llm_source_dir.mkdir()

        manifest_path = audit / "lineage_manifest.json"

        result = lineage.emit_lineage_manifest(
            study_name="TestStudy",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_source_dir,
            audit_dir=audit,
            pipeline_version="0.0.1",
            compliance_posture="STRICT",
            manifest_path=manifest_path,
        )

        assert "llm_source" in result["outputs"]
        assert "trio_bundle" not in result["outputs"]
        assert manifest_path.is_file()

    def test_handles_missing_raw_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "nonexistent"  # not created
        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        audit = tmp_path / "audit"
        audit.mkdir()
        mpath = audit / "lineage.json"
        payload = lineage.emit_lineage_manifest(
            study_name="X",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="x",
            compliance_posture="disabled",
            manifest_path=mpath,
        )
        # Missing raw dir yields empty list (not an error).
        assert payload["inputs"]["datasets"] == []

    def test_audit_datasets_in_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """audit_datasets key covers per-dataset ledgers under audit/datasets/."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"raw-a")

        audit = tmp_path / "audit"
        audit.mkdir()
        # Create a per-dataset ledger under audit/datasets/<stem>/
        ds_dir = audit / "datasets" / "1A_ICScreening"
        ds_dir.mkdir(parents=True)
        ledger_file = ds_dir / "phi_handling_ledger.as_written.json"
        ledger_file.write_text('{"run_id":"r1","events":[]}')
        cleanup_file = ds_dir / "dataset_cleanup_ledger.as_written.json"
        cleanup_file.write_text('{"run_id":"r1","events":[]}')
        # Create a timing sidecar — must be EXCLUDED from the content hash.
        timing_file = ds_dir / "phi_handling_ledger_timing.json"
        timing_file.write_text('{"generated_utc":"2026-01-01T00:00:00Z"}')

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()

        mpath = audit / "lineage_manifest.json"
        payload = lineage.emit_lineage_manifest(
            study_name="TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="0.0.1",
            compliance_posture="safe_harbor",
            manifest_path=mpath,
        )

        assert "audit_datasets" in payload["outputs"], "audit_datasets must be a top-level output key"
        ad_paths = {r["path"] for r in payload["outputs"]["audit_datasets"]}
        # Ledger files must be present.
        assert any("phi_handling_ledger.as_written.json" in p for p in ad_paths), \
            "PHI ledger must appear in audit_datasets"
        assert any("dataset_cleanup_ledger.as_written.json" in p for p in ad_paths), \
            "cleanup ledger must appear in audit_datasets"
        # Timing sidecar must be excluded.
        assert not any("_timing.json" in p for p in ad_paths), \
            "_timing.json sidecars must be excluded from content hash"

    def test_run_id_in_primary_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_id must appear in the primary manifest when supplied (B-7)."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        audit = tmp_path / "audit"
        audit.mkdir()
        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        mpath = audit / "lineage_manifest.json"

        payload = lineage.emit_lineage_manifest(
            study_name="TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="1.0.0",
            compliance_posture="safe_harbor",
            manifest_path=mpath,
            run_id="run_abc123",
        )

        assert payload["run_id"] == "run_abc123"
        on_disk = json.loads(mpath.read_text())
        assert on_disk["run_id"] == "run_abc123"

    def test_run_id_absent_when_not_supplied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_id must be absent from the primary manifest when not supplied."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        audit = tmp_path / "audit"
        audit.mkdir()
        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        mpath = audit / "lineage_manifest.json"

        payload = lineage.emit_lineage_manifest(
            study_name="TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="1.0.0",
            compliance_posture="safe_harbor",
            manifest_path=mpath,
        )

        assert "run_id" not in payload
