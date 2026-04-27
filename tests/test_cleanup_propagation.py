"""Tests for scripts/extraction/cleanup_propagation.py.

Covers:
- compute_propagation_set: column-scope drops minus surviving dataset-schema
  vars (case-folded, provenance-fields excluded).
- prune_dictionary: walks STAGING_DICTIONARY_DIR/**/*.jsonl, drops rows whose
  Databank Fieldname matches the drop set, atomic rewrite. Dictionary leg
  carries no PHI — no audit artifact is emitted.
- prune_pdfs: walks STAGING_PDFS_DIR/*_variables.json, drops matching
  variables + section references, atomic rewrite. PDF leg carries no PHI —
  no audit artifact is emitted.
- run_propagation: end-to-end orchestrator from config.STAGING_* paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.extraction.cleanup_propagation import (
    compute_propagation_set,
    prune_dictionary,
    prune_pdfs,
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
        import config

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
        import config

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
        import config

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
        import config

        _write_dataset_audit(config.AUDIT_DATASET_REPORT_PATH, [])
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()

    def test_casefold_match(self, monkeypatch_config: Path) -> None:
        import config

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
        import config

        # Audit file does not exist
        ds_dir = config.STAGING_DATASETS_DIR
        ds_dir.mkdir(parents=True, exist_ok=True)

        result = compute_propagation_set(config.AUDIT_DATASET_REPORT_PATH, ds_dir)
        assert result == set()


# ── prune_dictionary ────────────────────────────────────────────────────────


_DICT_VAR_KEY = "Question Short Name (Databank Fieldname)"


class TestPruneDictionary:
    def test_drops_matching_rows(self, monkeypatch_config: Path) -> None:
        import config

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
        import config

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
        import config

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
        import config

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
        import config

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


# ── prune_pdfs ─────────────────────────────────────────────────────────────


class TestPrunePdfs:
    def _seed(self, pdf_dir: Path, name: str, payload: dict) -> Path:
        pdf_dir.mkdir(parents=True, exist_ok=True)
        path = pdf_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_drops_top_level_variables_and_section_entries(self, monkeypatch_config: Path) -> None:
        import config

        pdf_dir = config.STAGING_PDFS_DIR
        path = self._seed(
            pdf_dir,
            "form1_variables.json",
            {
                "form_name": "form1",
                "variables": {"SUBJID": {}, "AGE_DROPPED": {}, "NAME": {}},
                "sections": {
                    "demographics": {
                        "context": "...",
                        "variables": ["SUBJID", "AGE_DROPPED", "NAME"],
                    }
                },
            },
        )

        removed = prune_pdfs({"age_dropped"}, pdf_dir)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "AGE_DROPPED" not in data["variables"]
        assert set(data["variables"].keys()) == {"SUBJID", "NAME"}
        assert "AGE_DROPPED" not in data["sections"]["demographics"]["variables"]
        assert data["sections"]["demographics"]["variables"] == ["SUBJID", "NAME"]
        # One top-level variable + one section reference dropped
        assert removed == 2

    def test_case_insensitive_match(self, monkeypatch_config: Path) -> None:
        import config

        pdf_dir = config.STAGING_PDFS_DIR
        path = self._seed(
            pdf_dir,
            "form1_variables.json",
            {
                "variables": {"AGE_DROPPED": {}, "NAME": {}},
                "sections": {"s1": {"context": "x", "variables": ["age_dropped", "NAME"]}},
            },
        )

        removed = prune_pdfs({"age_dropped"}, pdf_dir)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "AGE_DROPPED" not in data["variables"]
        assert data["sections"]["s1"]["variables"] == ["NAME"]
        assert removed == 2

    def test_multiple_sections(self, monkeypatch_config: Path) -> None:
        import config

        pdf_dir = config.STAGING_PDFS_DIR
        path = self._seed(
            pdf_dir,
            "multi_variables.json",
            {
                "variables": {"X": {}, "Y": {}},
                "sections": {
                    "a": {"context": "...", "variables": ["X", "Y"]},
                    "b": {"context": "...", "variables": ["X"]},
                    "c": {"context": "...", "variables": ["Y"]},
                },
            },
        )

        removed = prune_pdfs({"x"}, pdf_dir)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["variables"] == {"Y": {}}
        assert data["sections"]["a"]["variables"] == ["Y"]
        assert data["sections"]["b"]["variables"] == []
        assert data["sections"]["c"]["variables"] == ["Y"]
        # 1 top-level X + 2 section refs (a, b)
        assert removed == 3

    def test_missing_sections_key(self, monkeypatch_config: Path) -> None:
        import config

        pdf_dir = config.STAGING_PDFS_DIR
        path = self._seed(
            pdf_dir,
            "form1_variables.json",
            {"variables": {"X": {}, "Y": {}}},  # no "sections"
        )

        removed = prune_pdfs({"x"}, pdf_dir)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["variables"] == {"Y": {}}
        assert removed == 1

    def test_empty_drop_set_no_op(self, monkeypatch_config: Path) -> None:
        import config

        pdf_dir = config.STAGING_PDFS_DIR
        original_payload = {
            "variables": {"X": {}, "Y": {}},
            "sections": {"a": {"context": "...", "variables": ["X", "Y"]}},
        }
        path = self._seed(pdf_dir, "x_variables.json", original_payload)
        before = path.read_text(encoding="utf-8")

        removed = prune_pdfs(set(), pdf_dir)

        assert path.read_text(encoding="utf-8") == before
        assert removed == 0


# ── run_propagation ────────────────────────────────────────────────────────


class TestRunPropagation:
    def test_end_to_end_from_staging(self, monkeypatch_config: Path) -> None:
        import config

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

        # 4. Staging PDFs — contains AGE_DROPPED variable + section reference.
        pdf_dir = config.STAGING_PDFS_DIR
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_json = pdf_dir / "form1_variables.json"
        pdf_json.write_text(
            json.dumps(
                {
                    "form_name": "form1",
                    "variables": {"SUBJID": {}, "AGE_DROPPED": {}},
                    "sections": {
                        "demo": {
                            "context": "...",
                            "variables": ["SUBJID", "AGE_DROPPED"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        # 5. Run end-to-end.
        run_propagation()

        # 6. Assert dictionary pruned.
        dict_rows = [
            json.loads(line) for line in dict_jsonl.read_text(encoding="utf-8").splitlines()
        ]
        assert [r[_DICT_VAR_KEY] for r in dict_rows] == ["SUBJID"]

        # 7. Assert PDF pruned.
        pdf_data = json.loads(pdf_json.read_text(encoding="utf-8"))
        assert "AGE_DROPPED" not in pdf_data["variables"]
        assert pdf_data["sections"]["demo"]["variables"] == ["SUBJID"]

        # 8. Dict/PDF legs emit no audit artifact — only the dataset audit
        #    that we seeded in step 1 should exist under STUDY_AUDIT_DIR.
        audit_files = sorted(
            p.name for p in config.STUDY_AUDIT_DIR.glob("*.json")
        )
        assert audit_files == ["dataset_cleanup_report.json"]
