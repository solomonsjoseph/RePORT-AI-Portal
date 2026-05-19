"""Determinism contract tests: primary pipeline outputs must be byte-identical
across consecutive runs on identical raw input.

The invariant is:
- output/{STUDY}/llm_source/ tree is byte-identical (per-file read_bytes equality)
- primary lineage_manifest.json is byte-identical
- Per-run sidecars (extraction_timing.json, lineage_timing.json) DIFFER between
  runs because they capture the timestamps that were removed from primary content.

If REPORTAL_RUN_ID is set, ledger + lineage + extraction_timing all reference
the same run_id.

NOTE: We test only the unit-level sub-steps (provenance building + lineage
emission) rather than the full pipeline, because the latter requires a live
study fixture that is not committed to the repo.  This keeps CI fast while
still exercising the exact code paths that contain the non-determinism.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import pytest

from scripts.extraction import dataset_pipeline
from scripts.utils import lineage


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_provenance_jsonl_twice(tmp_path: Path) -> tuple[bytes, bytes]:
    """Write the same DataFrame twice via _write_provenance_jsonl; return both byte payloads."""
    df = pd.DataFrame([{"SUBJID": "S1", "VALUE": 1}])
    out1 = tmp_path / "run1" / "out.jsonl"
    out2 = tmp_path / "run2" / "out.jsonl"
    out1.parent.mkdir(parents=True)
    out2.parent.mkdir(parents=True)

    # Two calls a moment apart to ensure wall-clock timestamps would differ.
    dataset_pipeline._write_provenance_jsonl(
        df=df,
        output_path=out1,
        source_file="src.xlsx",
        sheet_name="Sheet1",
        study_name="TEST",
        extraction_ts="2026-04-23T00:00:00Z",
        raw_sha256="abc",
    )
    time.sleep(0.01)  # ensure wall-clock differs
    dataset_pipeline._write_provenance_jsonl(
        df=df,
        output_path=out2,
        source_file="src.xlsx",
        sheet_name="Sheet1",
        study_name="TEST",
        extraction_ts="2026-04-23T00:00:01Z",  # different extraction_ts
        raw_sha256="abc",
    )
    return out1.read_bytes(), out2.read_bytes()


# ---------------------------------------------------------------------------
# Provenance determinism
# ---------------------------------------------------------------------------


class TestProvenanceDeterminism:
    """extraction_utc must not appear in per-row _provenance in primary content."""

    def test_provenance_no_extraction_utc(self) -> None:
        """_build_provenance must NOT include extraction_utc in the returned dict."""
        prov = dataset_pipeline._build_provenance(
            source_file="f.xlsx",
            sheet_name="Sheet1",
            row_index=0,
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="deadbeef",
        )
        assert "extraction_utc" not in prov, (
            "_build_provenance still injects extraction_utc into per-row provenance; "
            "this prevents byte-identical primary outputs across runs"
        )

    def test_jsonl_bytes_identical_for_different_extraction_ts(self, tmp_path: Path) -> None:
        """JSONL content must be identical even when extraction_ts differs between runs."""
        bytes1, bytes2 = _write_provenance_jsonl_twice(tmp_path)
        assert bytes1 == bytes2, (
            "JSONL output differs across runs with different extraction_ts values; "
            "extraction_utc is still baked into per-row content"
        )


# ---------------------------------------------------------------------------
# Lineage manifest determinism
# ---------------------------------------------------------------------------


class TestLineageManifestDeterminism:
    """generated_utc and per-file mtime_utc must not appear in the primary manifest."""

    def test_primary_manifest_no_generated_utc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """emit_lineage_manifest must NOT include generated_utc in the primary manifest."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"x,y\n1,2\n")

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        (llm_src / "out.jsonl").write_bytes(b'{"x":1}\n')

        audit = tmp_path / "audit"
        audit.mkdir()
        manifest_path = audit / "lineage_manifest.json"

        payload = lineage.emit_lineage_manifest(
            study_name="DET_TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=manifest_path,
        )

        assert "generated_utc" not in payload, (
            "emit_lineage_manifest still sets generated_utc in primary manifest"
        )
        on_disk = json.loads(manifest_path.read_text())
        assert "generated_utc" not in on_disk

    def test_primary_manifest_no_mtime_utc_in_file_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File records in inputs/outputs must NOT carry mtime_utc."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"x,y\n1,2\n")

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()

        audit = tmp_path / "audit"
        audit.mkdir()
        manifest_path = audit / "lineage_manifest.json"

        payload = lineage.emit_lineage_manifest(
            study_name="DET_TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=manifest_path,
        )

        for file_record in payload["inputs"]["datasets"]:
            assert "mtime_utc" not in file_record, (
                f"File record for {file_record.get('path')} still contains mtime_utc"
            )

    def test_manifest_bytes_identical_across_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two consecutive calls with identical inputs must produce byte-identical manifests."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"x,y\n1,2\n")

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        (llm_src / "out.jsonl").write_bytes(b'{"x":1}\n')

        audit = tmp_path / "audit"
        audit.mkdir()

        # Use the same manifest path for both runs — this mirrors the real
        # pipeline where each run overwrites the same lineage_manifest.json.
        # We read the bytes immediately after each write so we capture what
        # each run produced before the file is overwritten.
        manifest_path = audit / "lineage_manifest.json"

        common_kwargs = dict(
            study_name="DET_TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=manifest_path,
        )

        lineage.emit_lineage_manifest(**common_kwargs)
        bytes_run1 = manifest_path.read_bytes()

        time.sleep(0.05)  # ensure wall-clock differs

        lineage.emit_lineage_manifest(**common_kwargs)
        bytes_run2 = manifest_path.read_bytes()

        assert bytes_run1 == bytes_run2, (
            "Primary lineage manifest is not byte-identical across consecutive runs; "
            "timestamps are still baked into primary content"
        )


# ---------------------------------------------------------------------------
# Sidecar existence tests (post-implementation)
# ---------------------------------------------------------------------------


class TestSidecarPresence:
    """After implementation: sidecars must exist and contain timing fields."""

    def test_extraction_timing_sidecar_written(self, tmp_path: Path) -> None:
        """write_extraction_timing_sidecar creates the expected file with required fields."""
        from scripts.utils.run_context import write_extraction_timing_sidecar

        run_id = "testrun123"
        sidecar = tmp_path / "runs" / run_id / "extraction_timing.json"
        write_extraction_timing_sidecar(
            output_dir=tmp_path,
            run_id=run_id,
            study="TEST",
            extraction_utc="2026-04-23T00:00:00Z",
            pipeline_version="2.0.0",
        )
        assert sidecar.is_file(), "extraction_timing.json sidecar was not written"
        data = json.loads(sidecar.read_text())
        assert data["run_id"] == run_id
        assert data["study"] == "TEST"
        assert data["extraction_utc"] == "2026-04-23T00:00:00Z"
        assert data["pipeline_version"] == "2.0.0"

    def test_lineage_timing_sidecar_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """emit_lineage_manifest writes lineage_timing.json when run_id + runs_dir provided."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"x,y\n1,2\n")

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()

        audit = tmp_path / "audit"
        audit.mkdir()
        manifest_path = audit / "lineage_manifest.json"
        run_id = "testrun456"

        lineage.emit_lineage_manifest(
            study_name="DET_TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=manifest_path,
            run_id=run_id,
            runs_dir=tmp_path / "runs",
        )

        sidecar = tmp_path / "runs" / run_id / "lineage_timing.json"
        assert sidecar.is_file(), "lineage_timing.json sidecar was not written"
        data = json.loads(sidecar.read_text())
        assert data["run_id"] == run_id
        assert data["study"] == "DET_TEST"
        assert "generated_utc" in data

    def test_lineage_timing_sidecar_contains_mtime_map(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lineage_timing.json must contain an mtime_utc map keyed by file path."""
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))

        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_bytes(b"x,y\n1,2\n")

        llm_src = tmp_path / "llm_source"
        llm_src.mkdir()
        (llm_src / "out.jsonl").write_bytes(b'{"x":1}\n')

        audit = tmp_path / "audit"
        audit.mkdir()
        run_id = "testrun789"

        lineage.emit_lineage_manifest(
            study_name="DET_TEST",
            raw_datasets_dir=raw,
            raw_dictionary_dir=None,
            raw_pdfs_dir=None,
            llm_source_dir=llm_src,
            audit_dir=audit,
            pipeline_version="2.0.0",
            compliance_posture="safe_harbor",
            manifest_path=audit / "lineage_manifest.json",
            run_id=run_id,
            runs_dir=tmp_path / "runs",
        )

        sidecar = tmp_path / "runs" / run_id / "lineage_timing.json"
        data = json.loads(sidecar.read_text())
        assert "mtime_utc" in data, "lineage_timing.json must contain mtime_utc map"
        assert isinstance(data["mtime_utc"], dict), "mtime_utc must be a dict keyed by file path"
        # At least one file should be in the map
        assert len(data["mtime_utc"]) > 0, "mtime_utc map must have at least one entry"


# ---------------------------------------------------------------------------
# resolve_run_id
# ---------------------------------------------------------------------------


class TestResolveRunId:
    def test_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.utils.run_context import resolve_run_id

        monkeypatch.setenv("REPORTAL_RUN_ID", "fixed-run-abc")
        assert resolve_run_id() == "fixed-run-abc"

    def test_generates_uuid_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.utils.run_context import resolve_run_id

        monkeypatch.delenv("REPORTAL_RUN_ID", raising=False)
        rid = resolve_run_id()
        assert rid.startswith("run_")
        assert len(rid) > 4

    def test_two_calls_without_env_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.utils.run_context import resolve_run_id

        monkeypatch.delenv("REPORTAL_RUN_ID", raising=False)
        assert resolve_run_id() != resolve_run_id()

    def test_ledger_uses_resolve_run_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """LedgerWriter with no explicit run_id must honour REPORTAL_RUN_ID env var."""
        from scripts.audit.ledger import LedgerWriter

        # Set up sentinel so phase4 guard doesn't fail
        monkeypatch.setenv("REPORTAL_RUN_ID", "env-run-xyz")
        monkeypatch.delenv("REPORTAL_PROCESS_ROLE", raising=False)
        out = tmp_path / "audit" / "ledger.json"
        lw = LedgerWriter(output_path=out)
        # Flush requires phase4 guard — skip writing; just check the internal run_id
        assert lw._run_id == "env-run-xyz", (
            "LedgerWriter did not pick up REPORTAL_RUN_ID from environment"
        )
