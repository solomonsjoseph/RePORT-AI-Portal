"""Tests for the SoT-vs-scrubbed reconciliation engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.reconciliation import (
    ReconciliationResult,
    load_scrubbed_columns,
    load_sot_columns,
    reconcile,
)


# ---------------------------------------------------------------------------
# reconcile() — the core decision rule
# ---------------------------------------------------------------------------


def test_reconcile_all_match_with_phi_explanation() -> None:
    result = reconcile(
        form="form_x",
        sot_cols=frozenset({"a", "b", "c"}),
        scrubbed_cols=frozenset({"a", "b"}),
        phi_drop=frozenset({"c"}),
        cleanup_drop=frozenset(),
    )
    assert result == ReconciliationResult(
        form="form_x",
        ok=True,
        missing_unexplained=frozenset(),
        extra_in_scrubbed=frozenset(),
        explained_by_phi=frozenset({"c"}),
        explained_by_cleanup=frozenset(),
    )


def test_reconcile_unexplained_drop_fails() -> None:
    result = reconcile(
        form="form_x",
        sot_cols=frozenset({"a", "b", "c"}),
        scrubbed_cols=frozenset({"a"}),
        phi_drop=frozenset({"b"}),
        cleanup_drop=frozenset(),
    )
    assert result.ok is False
    assert result.missing_unexplained == frozenset({"c"})
    assert result.extra_in_scrubbed == frozenset()
    assert result.explained_by_phi == frozenset({"b"})
    assert result.explained_by_cleanup == frozenset()


def test_reconcile_extra_column_fails() -> None:
    result = reconcile(
        form="form_x",
        sot_cols=frozenset({"a", "b"}),
        scrubbed_cols=frozenset({"a", "b", "x"}),
        phi_drop=frozenset(),
        cleanup_drop=frozenset(),
    )
    assert result.ok is False
    assert result.missing_unexplained == frozenset()
    assert result.extra_in_scrubbed == frozenset({"x"})


def test_reconcile_overlapping_explanations_count_once() -> None:
    """A column explained by both ledgers is still ok — either explanation
    suffices. Reconciliation never double-counts."""
    result = reconcile(
        form="form_x",
        sot_cols=frozenset({"a", "b"}),
        scrubbed_cols=frozenset({"a"}),
        phi_drop=frozenset({"b"}),
        cleanup_drop=frozenset({"b"}),
    )
    assert result.ok is True
    assert result.explained_by_phi == frozenset({"b"})
    assert result.explained_by_cleanup == frozenset({"b"})
    assert result.missing_unexplained == frozenset()


def test_reconcile_empty_inputs() -> None:
    result = reconcile(
        form="empty",
        sot_cols=frozenset(),
        scrubbed_cols=frozenset(),
        phi_drop=frozenset(),
        cleanup_drop=frozenset(),
    )
    assert result.ok is True
    assert result.missing_unexplained == frozenset()
    assert result.extra_in_scrubbed == frozenset()


def test_reconciliation_result_is_frozen() -> None:
    """Frozen dataclass — mutating a result must raise."""
    r = ReconciliationResult(
        form="x",
        ok=True,
        missing_unexplained=frozenset(),
        extra_in_scrubbed=frozenset(),
        explained_by_phi=frozenset(),
        explained_by_cleanup=frozenset(),
    )
    with pytest.raises(AttributeError):
        r.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_sot_columns
# ---------------------------------------------------------------------------


def test_load_sot_columns_extracts_variable_keys() -> None:
    artifact = {
        "schema_version": 2,
        "study": "Mini",
        "form": "form_x",
        "variables": {
            "FID": {"record_type": "variable"},
            "PATIENT_NAME": {"record_type": "variable"},
            "SOME_OPTION_SET": {"record_type": "option_set"},
            "AGE": {"record_type": "variable"},
        },
    }
    cols = load_sot_columns(artifact)
    assert cols == frozenset({"FID", "PATIENT_NAME", "AGE"})
    assert isinstance(cols, frozenset)


def test_load_sot_columns_treats_missing_record_type_as_variable() -> None:
    """Permissive: if a record has no ``record_type`` we still keep it,
    matching the heuristic that ``variables:`` is the column dict."""
    artifact = {
        "form": "form_x",
        "variables": {
            "A": {"record_type": "variable"},
            "B": {},  # no record_type — treat as variable
        },
    }
    assert load_sot_columns(artifact) == frozenset({"A", "B"})


def test_load_sot_columns_empty_variables() -> None:
    assert load_sot_columns({"variables": {}}) == frozenset()
    assert load_sot_columns({}) == frozenset()


# ---------------------------------------------------------------------------
# load_scrubbed_columns
# ---------------------------------------------------------------------------


def test_load_scrubbed_columns_returns_first_row_keys(tmp_path: Path) -> None:
    rows = [
        {"FID": "1", "AGE": 30, "_phi_scrubbed": "v1"},
        {"FID": "2", "AGE": 40, "_phi_scrubbed": "v1"},
    ]
    (tmp_path / "form_x.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    cols = load_scrubbed_columns("form_x", tmp_path)
    # _phi_scrubbed and provenance fields are stripped.
    assert cols == frozenset({"FID", "AGE"})


def test_load_scrubbed_columns_strips_provenance_fields(tmp_path: Path) -> None:
    row = {
        "FID": "1",
        "AGE": 30,
        "_phi_scrubbed": "v1",
        "source_file": "form_x.xlsx",
        "_provenance": {"sha": "..."},
        "_metadata": {"...": "..."},
    }
    (tmp_path / "form_x.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    cols = load_scrubbed_columns("form_x", tmp_path)
    assert cols == frozenset({"FID", "AGE"})


def test_load_scrubbed_columns_skips_blank_lines(tmp_path: Path) -> None:
    body = "\n\n" + json.dumps({"A": 1, "B": 2}) + "\n"
    (tmp_path / "form_x.jsonl").write_text(body, encoding="utf-8")
    assert load_scrubbed_columns("form_x", tmp_path) == frozenset({"A", "B"})


def test_load_scrubbed_columns_missing_file_returns_none(tmp_path: Path) -> None:
    """Missing file → None so the caller can skip reconciliation gracefully."""
    assert load_scrubbed_columns("nope", tmp_path) is None


def test_load_scrubbed_columns_empty_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "form_x.jsonl").write_text("", encoding="utf-8")
    # File exists but is empty — return empty frozenset (not None) so
    # the form participates in reconciliation as a degenerate case.
    assert load_scrubbed_columns("form_x", tmp_path) == frozenset()


def test_load_scrubbed_columns_logs_malformed_lines_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue 2 — malformed JSONL lines must be logged at WARNING (not silently
    dropped), and the scanner must continue to the next line so a single
    bad row does not derail reconciliation."""
    body = "{not json,," + "\n" + json.dumps({"A": 1, "B": 2}) + "\n"
    jsonl_path = tmp_path / "form_x.jsonl"
    jsonl_path.write_text(body, encoding="utf-8")

    with caplog.at_level("WARNING", logger="scripts.source_truth.reconciliation"):
        cols = load_scrubbed_columns("form_x", tmp_path)

    # Fallthrough still works: returns the column set from the second row.
    assert cols == frozenset({"A", "B"})
    # And the malformed line is surfaced as a warning that names the file
    # and the line number.
    matching = [
        r
        for r in caplog.records
        if "malformed json" in r.getMessage().lower()
        and str(jsonl_path) in r.getMessage()
        and "line 1" in r.getMessage()
    ]
    assert matching, f"expected malformed-line warning, got {[r.getMessage() for r in caplog.records]}"


def test_load_scrubbed_columns_reads_from_datasets_dir_directly(tmp_path: Path) -> None:
    """load_scrubbed_columns(form, datasets_dir) reads form.jsonl directly in datasets_dir.

    Phase 5b moves the canonical scrubbed JSONL location from
    ``staging_root/datasets/<form>.jsonl`` (with a ``datasets/`` subdir)
    to a flat ``datasets_dir/<form>.jsonl`` layout. The reader must
    therefore NOT prepend a ``datasets/`` subdir — passing a path that
    contains a ``datasets/`` child must return ``None`` for the form
    (the file isn't where we look anymore).
    """
    datasets_dir = tmp_path / "files"
    datasets_dir.mkdir()
    (datasets_dir / "smear.jsonl").write_text(
        json.dumps({"SPUTUM_DATE": "x", "RESULT": "y", "_phi_scrubbed": True}) + "\n",
        encoding="utf-8",
    )

    cols = load_scrubbed_columns("smear", datasets_dir)
    assert cols == frozenset({"SPUTUM_DATE", "RESULT"})

    # Old API where the caller relied on the function appending a
    # ``datasets/`` subdir must no longer work. We pass a parent dir that
    # has a ``datasets/`` child holding the JSONL — but the function
    # reads ``<parent>/<form>.jsonl`` directly, so it must return None.
    staging_root = tmp_path / "staging"
    (staging_root / "datasets").mkdir(parents=True)
    (staging_root / "datasets" / "smear.jsonl").write_text(
        json.dumps({"COL_A": "x"}) + "\n", encoding="utf-8"
    )
    result = load_scrubbed_columns("smear", staging_root)
    assert result is None
