"""Tests for the Step 12a reserved-key collision guard in
scripts.extraction.dataset_pipeline._write_provenance_jsonl.

An incoming raw source column literally named one of the pipeline's
reserved provenance/PHI-scrub keys (``source_file``, ``_provenance``,
``_metadata``, ``_phi_scrubbed``) must be renamed with a ``src__`` prefix
before the reserved key is injected, so:

* the raw column's value is preserved (not silently overwritten), and
* a raw column named ``_phi_scrubbed`` cannot spoof the PHI scrubber's
  row-level idempotency marker (checked later, by
  scripts.security.phi_scrub, as ``row.get("_phi_scrubbed") == "v3"``).

Each collision must also produce exactly one ``reserved_key_renamed``
audit event, carrying only the column name and a count — never the value.
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pandas as pd

from scripts.extraction.dataset_pipeline import _write_provenance_jsonl, extract_single_dataset


class TestWriteProvenanceJsonlReservedKeyCollision:
    def test_incoming_phi_scrubbed_column_is_renamed(self, tmp_path: Path) -> None:
        # A raw source column literally named "_phi_scrubbed" holding "v3"
        # (the scrubber's own idempotency marker value) must not survive
        # under that name — it must be renamed before dataset_pipeline
        # injects anything, so the scrubber's later idempotency check can
        # never be spoofed by source data.
        df = pd.DataFrame([{"SUBJID": "S1", "_phi_scrubbed": "v3", "VALUE": 1}])
        out_path = tmp_path / "out.jsonl"

        count, rename_events = _write_provenance_jsonl(
            df=df,
            output_path=out_path,
            source_file="src.xlsx",
            sheet_name="Sheet1",
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="abc123",
        )

        assert count == 1
        rows = [json.loads(line) for line in out_path.read_text().splitlines()]
        assert len(rows) == 1
        row = rows[0]

        # "src__" + "_phi_scrubbed" == "src___phi_scrubbed" (three underscores):
        # two from the "src__" prefix, one already leading "_phi_scrubbed".
        assert "src___phi_scrubbed" in row
        assert row["src___phi_scrubbed"] == "v3"

        # dataset_pipeline.py never injects "_phi_scrubbed" itself (that
        # marker is set later by scripts.security.phi_scrub) — so the
        # reserved key must be entirely absent here, not equal to "v3".
        assert "_phi_scrubbed" not in row

        # The extractor's own injected keys are present and correct,
        # unaffected by the collision.
        assert row["source_file"] == "src.xlsx"
        assert row["_provenance"]["raw_sha256"] == "abc123"
        assert "pipeline_version" in row["_provenance"]

        # Value-free audit event: name + count only, never the "v3" value.
        assert rename_events == [
            {
                "scope": "reserved_key_renamed",
                "name": "_phi_scrubbed",
                "file": "src.xlsx",
                "sheet": "Sheet1",
                "renamed_to": "src___phi_scrubbed",
                "count": 1,
            }
        ]

    def test_incoming_source_file_column_is_renamed(self, tmp_path: Path) -> None:
        # A raw source column literally named "source_file" must not
        # silently overwrite (or be overwritten by) the extractor's own
        # provenance value.
        df = pd.DataFrame([{"SUBJID": "S1", "source_file": "raw_original.csv", "VALUE": 2}])
        out_path = tmp_path / "out.jsonl"

        count, rename_events = _write_provenance_jsonl(
            df=df,
            output_path=out_path,
            source_file="src.xlsx",
            sheet_name="Sheet1",
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="abc123",
        )

        assert count == 1
        rows = [json.loads(line) for line in out_path.read_text().splitlines()]
        row = rows[0]

        # "src__" + "source_file" == "src__source_file".
        assert row["src__source_file"] == "raw_original.csv"

        # The reserved key holds the extractor's own provenance value, not
        # the raw collided value.
        assert row["source_file"] == "src.xlsx"
        assert row["_provenance"]["raw_sha256"] == "abc123"
        assert "pipeline_version" in row["_provenance"]

        assert rename_events == [
            {
                "scope": "reserved_key_renamed",
                "name": "source_file",
                "file": "src.xlsx",
                "sheet": "Sheet1",
                "renamed_to": "src__source_file",
                "count": 1,
            }
        ]

    def test_no_collision_yields_no_rename_events(self, tmp_path: Path) -> None:
        df = pd.DataFrame([{"SUBJID": "S1", "VALUE": 1}])
        out_path = tmp_path / "out.jsonl"

        count, rename_events = _write_provenance_jsonl(
            df=df,
            output_path=out_path,
            source_file="src.xlsx",
            sheet_name="Sheet1",
            study_name="TEST",
            extraction_ts="2026-04-23T00:00:00Z",
            raw_sha256="abc123",
        )

        assert count == 1
        assert rename_events == []


class TestExtractSingleDatasetSurfacesRenameEvents:
    def test_reserved_key_collision_reaches_dropped_events(self, tmp_path: Path) -> None:
        # End-to-end wiring: extract_single_dataset's 4th tuple element must
        # include the reserved_key_renamed event alongside any
        # dataset-column drop events.
        src = tmp_path / "collide.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "S1"
        ws.append(["SUBJID", "_phi_scrubbed", "VALUE"])
        ws.append(["S1", "v3", 1])
        wb.save(src)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        ok, count, err, events = extract_single_dataset(
            file_path=src,
            output_dir=out_dir,
            study_name="TEST",
            extraction_ts="2026-04-21T00:00:00+00:00",
        )

        assert ok is True
        assert err is None
        assert count == 1
        renamed = [e for e in events if e["scope"] == "reserved_key_renamed"]
        assert len(renamed) == 1
        assert renamed[0]["name"] == "_phi_scrubbed"
        assert renamed[0]["renamed_to"] == "src___phi_scrubbed"
        assert renamed[0]["file"] == "collide.xlsx"
        assert renamed[0]["count"] == 1

        out_file = next(out_dir.glob("*.jsonl"))
        rows = [json.loads(line) for line in out_file.read_text().splitlines()]
        assert rows[0]["src___phi_scrubbed"] == "v3"
        assert "_phi_scrubbed" not in rows[0]
