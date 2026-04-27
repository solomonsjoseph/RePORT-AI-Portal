"""Tests for extraction provenance enrichment (Stage 2c).

Covers:
* hash_raw_file — deterministic SHA-256 of a streamed file
* _build_provenance — presence of raw_sha256, pipeline_version, extraction_engine
* _write_provenance_jsonl — end-to-end rows carry full provenance
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.extraction import dataset_pipeline


class TestHashRawFile:
    def test_matches_hashlib_stream(self, tmp_path: Path) -> None:
        content = b"alpha\nbeta\n" * 1000
        target = tmp_path / "sample.csv"
        target.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert dataset_pipeline.hash_raw_file(target) == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.csv"
        target.write_bytes(b"")
        assert dataset_pipeline.hash_raw_file(target) == hashlib.sha256(b"").hexdigest()

    def test_large_chunked(self, tmp_path: Path) -> None:
        # Force multiple chunks through the 64 KiB default.
        content = b"x" * ((1 << 16) * 3 + 1234)
        target = tmp_path / "big.bin"
        target.write_bytes(content)
        assert dataset_pipeline.hash_raw_file(target) == hashlib.sha256(content).hexdigest()


class TestBuildProvenance:
    def test_contains_all_fields(self) -> None:
        prov = dataset_pipeline._build_provenance(
            source_file="f.xlsx",
            sheet_name="Sheet1",
            row_index=7,
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="deadbeef",
        )
        assert prov["source_file"] == "f.xlsx"
        assert prov["sheet_name"] == "Sheet1"
        assert prov["row_index"] == 7
        assert prov["study_name"] == "TEST"
        assert prov["extraction_utc"] == "2026-04-23T00:00:00Z"
        assert prov["raw_sha256"] == "deadbeef"
        assert "pipeline_version" in prov
        assert "extraction_engine" in prov
        assert prov["extraction_engine"].startswith("pandas=")

    def test_raw_sha256_omitted_when_none(self) -> None:
        prov = dataset_pipeline._build_provenance(
            source_file="f.xlsx",
            sheet_name="Sheet1",
            row_index=0,
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256=None,
        )
        assert "raw_sha256" not in prov


class TestWriteProvenanceJsonl:
    def test_rows_carry_sha_and_version(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            [
                {"SUBJID": "S1", "VALUE": 1},
                {"SUBJID": "S2", "VALUE": 2},
            ]
        )
        out_path = tmp_path / "out.jsonl"
        count = dataset_pipeline._write_provenance_jsonl(
            df=df,
            output_path=out_path,
            source_file="src.xlsx",
            sheet_name="Sheet1",
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="abc123",
        )
        assert count == 2
        lines = [json.loads(line) for line in out_path.read_text().splitlines()]
        for row in lines:
            assert row["_provenance"]["raw_sha256"] == "abc123"
            assert "pipeline_version" in row["_provenance"]
            assert row["_provenance"]["extraction_engine"].startswith("pandas=")
