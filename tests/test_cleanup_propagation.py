"""Tests for scripts/extraction/cleanup_propagation.py.

Covers:
- compute_propagation_set: column-scope drops minus surviving dataset-schema
  vars (case-folded, provenance-fields excluded).
- prune_dictionary: walks STAGING_DICTIONARY_DIR/**/*.jsonl, drops rows whose
  Databank Fieldname matches the drop set, atomic rewrite. Dictionary leg
  carries no PHI — no audit artifact is emitted.
- run_propagation: end-to-end orchestrator from config.STAGING_* paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from scripts.extraction.cleanup_propagation import (
    compute_propagation_set,
    prune_dictionary,
    run_propagation,
)
from tests.conftest import _write_jsonl

# ── Helpers ────────────────────────────────────────────────────────────────


def _write_dataset_audit(
    path: Path,
    removed: list[dict[str, object]],
    *,
    study: str = "TestStudy",
) -> None:
    """Seed an AUDIT_DATASET_REPORT_PATH payload in the unified schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "study": study,
                "generated_utc": "2026-04-21T00:00:00Z",
                "leg": "dataset",
                "removed": removed,
            }
        ),
        encoding="utf-8",
    )


# ── compute_propagation_set ────────────────────────────────────────────────


class TestComputePropagationSet:
    def test_returns_dropped_vars_not_in_surviving_schemas(self, monkeypatch_config: Path) -> None:
        # Seed audit: two dataset-column drop events
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "AGE",
                    "file": "f.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": "AGE_MAIN",
                },
                {
                    "scope": "dataset-column",
                    "name": "SUBJID2",
                    "file": "g.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": "SUBJID",
                },
            ],
        )

        # Seed staging datasets: AGE still survives in dataset B → must NOT propagate
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds_dir / "a.jsonl", [{"SUBJID": "S1", "AGE": 30}])
        _write_jsonl(ds_dir / "b.jsonl", [{"SUBJID": "S2", "NAME": "x"}])

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == {"subjid2"}

    def test_ignores_non_column_scopes(self, monkeypatch_config: Path) -> None:
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-junk-file",
                    "name": "JunkFile",
                    "file": "junk.jsonl",
                    "sheet": None,
                    "reason": "junk",
                    "kept": None,
                },
                {
                    "scope": "dataset-duplicate-file",
                    "name": "DupStem",
                    "file": "dup.jsonl",
                    "sheet": None,
                    "reason": "subset",
                    "kept": "keep.jsonl",
                },
            ],
        )
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds_dir / "a.jsonl", [{"SUBJID": "S1"}])

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()

    def test_excludes_provenance_fields_from_surviving_set(self, monkeypatch_config: Path) -> None:
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "_provenance",
                    "file": "f.jsonl",
                    "sheet": None,
                    "reason": "provenance mishap",
                    "kept": None,
                }
            ],
        )
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(
            ds_dir / "a.jsonl",
            [{"SUBJID": "S1", "_provenance": {"sheet": "foo"}, "_metadata": {}}],
        )

        # Provenance keys don't count as "surviving vars" → still propagates
        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == {"_provenance"}

    def test_empty_audit_returns_empty_set(self, monkeypatch_config: Path) -> None:
        _write_dataset_audit(config.AUDIT_DATASET_REPORT_PATH, [])
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()

    def test_casefold_match(self, monkeypatch_config: Path) -> None:
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "SubjId2",
                    "file": "f.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": None,
                }
            ],
        )
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)
        # Different case survives — should MATCH and exclude from drop set
        _write_jsonl(ds_dir / "a.jsonl", [{"SUBJID2": "x"}])

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()

    def test_missing_audit_returns_empty_set(self, monkeypatch_config: Path) -> None:
        # Audit file does not exist
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()


# ── prune_dictionary ────────────────────────────────────────────────────────


_DICT_VAR_KEY = "Question Short Name (Databank Fieldname)"


class TestPruneDictionary:
    def test_drops_matching_rows(self, monkeypatch_config: Path) -> None:
        dict_dir = config.STAGING_DICTIONARY_DIR
        sub = dict_dir / "form1"
        sub.mkdir(parents=True, exist_ok=True)
        jsonl = sub / "form1_table.jsonl"
        rows = [
            {_DICT_VAR_KEY: "SUBJID", "__sheet__": "form1", "Form": "F1"},
            {_DICT_VAR_KEY: "AGE_DROPPED", "__sheet__": "form1", "Form": "F1"},
            {_DICT_VAR_KEY: "NAME", "__sheet__": "form1", "Form": "F1"},
        ]
        _write_jsonl(jsonl, rows)

        removed = prune_dictionary({"age_dropped"}, dict_dir)

        # File rewritten with 2 rows
        remaining = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        assert len(remaining) == 2
        names = {r[_DICT_VAR_KEY] for r in remaining}
        assert names == {"SUBJID", "NAME"}
        assert removed == 1

    def test_recursive_walk_visits_subdirectories(self, monkeypatch_config: Path) -> None:
        dict_dir = config.STAGING_DICTIONARY_DIR
        (dict_dir / "tbl_a").mkdir(parents=True, exist_ok=True)
        (dict_dir / "tbl_b").mkdir(parents=True, exist_ok=True)

        _write_jsonl(
            dict_dir / "tbl_a" / "tbl_a_table.jsonl",
            [
                {_DICT_VAR_KEY: "VAR_X", "__sheet__": "tbl_a"},
                {_DICT_VAR_KEY: "KEEPA", "__sheet__": "tbl_a"},
            ],
        )
        _write_jsonl(
            dict_dir / "tbl_b" / "tbl_b_table.jsonl",
            [
                {_DICT_VAR_KEY: "VAR_X", "__sheet__": "tbl_b"},
                {_DICT_VAR_KEY: "KEEPB", "__sheet__": "tbl_b"},
            ],
        )

        removed = prune_dictionary({"var_x"}, dict_dir)

        rows_a = [
            json.loads(line)
            for line in (dict_dir / "tbl_a" / "tbl_a_table.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        rows_b = [
            json.loads(line)
            for line in (dict_dir / "tbl_b" / "tbl_b_table.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [r[_DICT_VAR_KEY] for r in rows_a] == ["KEEPA"]
        assert [r[_DICT_VAR_KEY] for r in rows_b] == ["KEEPB"]
        assert removed == 2

    def test_empty_drop_set_no_op(self, monkeypatch_config: Path) -> None:
        dict_dir = config.STAGING_DICTIONARY_DIR
        dict_dir.mkdir(parents=True, exist_ok=True)
        jsonl = dict_dir / "t.jsonl"
        rows = [{_DICT_VAR_KEY: "X", "__sheet__": "t"}]
        _write_jsonl(jsonl, rows)
        original = jsonl.read_text(encoding="utf-8")

        removed = prune_dictionary(set(), dict_dir)

        assert jsonl.read_text(encoding="utf-8") == original
        assert removed == 0

    def test_missing_variable_name_column_row_is_kept(self, monkeypatch_config: Path) -> None:
        dict_dir = config.STAGING_DICTIONARY_DIR
        dict_dir.mkdir(parents=True, exist_ok=True)
        jsonl = dict_dir / "t.jsonl"
        rows = [
            {"SomeOtherKey": "whatever", "__sheet__": "t"},  # no var-name key
            {_DICT_VAR_KEY: "TOBEDROPPED", "__sheet__": "t"},
        ]
        _write_jsonl(jsonl, rows)

        removed = prune_dictionary({"tobedropped"}, dict_dir)

        remaining = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        # First row preserved; second row dropped
        assert len(remaining) == 1
        assert remaining[0].get("SomeOtherKey") == "whatever"
        assert removed == 1

    def test_casefold_match_on_dict_row(self, monkeypatch_config: Path) -> None:
        dict_dir = config.STAGING_DICTIONARY_DIR
        dict_dir.mkdir(parents=True, exist_ok=True)
        jsonl = dict_dir / "t.jsonl"
        _write_jsonl(
            jsonl,
            [
                {_DICT_VAR_KEY: "subjid2", "__sheet__": "t"},
                {_DICT_VAR_KEY: "KEEP", "__sheet__": "t"},
            ],
        )

        # Drop-set is already case-folded
        removed = prune_dictionary({"subjid2"}, dict_dir)
        remaining = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        assert [r[_DICT_VAR_KEY] for r in remaining] == ["KEEP"]
        assert removed == 1


# ── run_propagation ────────────────────────────────────────────────────────


class TestRunPropagation:
    def test_end_to_end_from_staging(self, monkeypatch_config: Path) -> None:
        # 1. Dataset audit with one propagable drop (AGE_DROPPED).
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "AGE_DROPPED",
                    "file": "f.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": None,
                }
            ],
        )

        # 2. Staging datasets — AGE_DROPPED does NOT survive.
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(ds_dir / "a.jsonl", [{"SUBJID": "S1", "NAME": "x"}])

        # 3. Staging dictionary — contains one AGE_DROPPED row.
        dict_dir = config.STAGING_DICTIONARY_DIR
        (dict_dir / "form1").mkdir(parents=True, exist_ok=True)
        dict_jsonl = dict_dir / "form1" / "form1_table.jsonl"
        _write_jsonl(
            dict_jsonl,
            [
                {_DICT_VAR_KEY: "SUBJID", "__sheet__": "form1"},
                {_DICT_VAR_KEY: "AGE_DROPPED", "__sheet__": "form1"},
            ],
        )

        # 4. Run end-to-end.
        run_propagation()

        # 5. Assert dictionary pruned.
        dict_rows = [
            json.loads(line) for line in dict_jsonl.read_text(encoding="utf-8").splitlines()
        ]
        assert [r[_DICT_VAR_KEY] for r in dict_rows] == ["SUBJID"]

        # 6. Dictionary leg emits no audit artifact — only the dataset audit
        #    that we seeded in step 1 should exist under STUDY_AUDIT_DIR.
        audit_files = sorted(p.name for p in config.STUDY_AUDIT_DIR.glob("*.json"))
        assert audit_files == ["dataset_cleanup_report.json"]


# ── GAP-7: fail-closed on malformed JSONL ──────────────────────────────────


class TestComputePropagationSetMalformedJsonl:
    """GAP-7: compute_propagation_set must RAISE (not silently skip) when a staging
    JSONL file contains a malformed line.

    Silently continuing would risk over-pruning: a variable that only appears
    on the unparseable row would be excluded from 'surviving' and then pruned
    from the dictionary — dangling-reference integrity loss.
    """

    def test_raises_on_malformed_jsonl_line(self, monkeypatch_config: Path) -> None:
        """One valid line + one malformed line → RuntimeError, never silent continue."""
        import pytest

        # Seed audit with a column-scope drop so the propagation set is non-empty
        # (the code only scans dataset JSONLs when dropped is non-empty).
        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "DROPPED_COL",
                    "file": "x.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": None,
                }
            ],
        )

        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        # Write a JSONL file with one valid JSON object line and one malformed line.
        bad_jsonl = ds_dir / "mixed_validity.jsonl"
        bad_jsonl.write_text(
            '{"SUBJID": "S1", "SOME_COL": 1}\nNOT VALID JSON {{{{{\n',
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="malformed JSONL"):
            compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)

    def test_error_message_names_the_file(self, monkeypatch_config: Path) -> None:
        """The RuntimeError message must name the offending file so operators can act."""
        import pytest

        _write_dataset_audit(
            config.AUDIT_DATASET_REPORT_PATH,
            [
                {
                    "scope": "dataset-column",
                    "name": "SOME_VAR",
                    "file": "f.jsonl",
                    "sheet": None,
                    "reason": "dup",
                    "kept": None,
                }
            ],
        )

        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        bad_jsonl = ds_dir / "broken_dataset.jsonl"
        bad_jsonl.write_text(
            '{"SUBJID": "S1"}\n{{not json at all}}\n',
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError) as exc_info:
            compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)

        assert "broken_dataset.jsonl" in str(exc_info.value), (
            "RuntimeError message must name the offending file"
        )
