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
        (llm_source_dir / "data_dictionary.json").write_text("{}")

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
