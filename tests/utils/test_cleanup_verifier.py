"""Tests for the 3-phase dataset-cleanup verifier (Wave 3 C4.2)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit.ledger import CLEANUP_LEDGER_FILENAME, DATASET_LEDGER_DIRNAME
from scripts.utils.cleanup_verifier import verify_cleanup


def _write_ledger(audit_dir: Path, stem: str, events: list[dict]) -> None:
    folder = audit_dir / DATASET_LEDGER_DIRNAME / stem
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CLEANUP_LEDGER_FILENAME).write_text(
        json.dumps({"run_id": "r", "study": "S", "leg": "dataset", "events": events}),
        encoding="utf-8",
    )


def _col_drop(var: str, src_file: str) -> dict:
    return {
        "form": Path(src_file).stem,
        "variable_id": var,
        "action": "dataset_column_drop",
        "where": {"dataset_file": src_file, "pdf_source": None},
        "count": None,
    }


def _junk(stem: str) -> dict:
    return {
        "form": stem,
        "variable_id": stem,
        "action": "dataset_junk_file",
        "where": {"dataset_file": f"{stem}.xlsx", "pdf_source": None},
        "count": None,
    }


def _dup(removed_stem: str) -> dict:
    return {
        "form": removed_stem,
        "variable_id": removed_stem,
        "action": "dataset_duplicate_file",
        "where": {"dataset_file": f"{removed_stem}.xlsx", "pdf_source": None},
        "count": None,
    }


def _publish(pub: Path, stem: str, columns: list[str] | None, *, empty: bool = False) -> None:
    pub.mkdir(parents=True, exist_ok=True)
    path = pub / f"{stem}.jsonl"
    if empty:
        path.write_text("", encoding="utf-8")
        return
    # First-line keys only matter; values are placeholder non-PHI.
    row = dict.fromkeys(columns or ["SUBJID"], 1)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_clean_run_has_no_findings(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    pub = tmp_path / "pub"
    # Form A had a column dropped; surviving column set excludes it.
    _write_ledger(audit, "formA", [_col_drop("DUPCOL", "formA.xlsx")])
    _publish(pub, "formA", ["SUBJID", "VALUE"])
    # Form B was junk and is correctly absent from the published tree.
    _write_ledger(audit, "junkstem", [_junk("junkstem")])

    rep = verify_cleanup(audit, pub, junk_patterns=frozenset(), duplicate_pairs=[])
    assert rep.ok, rep.findings
    assert rep.checked_ledgers == 2
    assert rep.checked_datasets == 1


def test_must_gone_dropped_column_still_present(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    pub = tmp_path / "pub"
    _write_ledger(audit, "formA", [_col_drop("DUPCOL", "formA.xlsx")])
    _publish(pub, "formA", ["SUBJID", "DUPCOL"])  # dropped col leaked back

    rep = verify_cleanup(audit, pub, junk_patterns=frozenset(), duplicate_pairs=[])
    assert not rep.ok
    kinds = {f.kind for f in rep.findings_by_phase["must_gone"]}
    assert "dropped_column_present" in kinds


def test_must_gone_removed_file_still_present(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    pub = tmp_path / "pub"
    _write_ledger(audit, "dupstem", [_dup("dupstem")])
    _publish(pub, "dupstem", ["SUBJID"])  # removed duplicate still published

    rep = verify_cleanup(audit, pub, junk_patterns=frozenset(), duplicate_pairs=[])
    assert not rep.ok
    kinds = {f.kind for f in rep.findings_by_phase["must_gone"]}
    assert any(k.startswith("removed_file_present") for k in kinds)


def test_must_remain_surviving_dataset_missing(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    pub = tmp_path / "pub"
    # Column-drop only (not a file removal) but no published file → data vanished.
    _write_ledger(audit, "formA", [_col_drop("DUPCOL", "formA.xlsx")])

    rep = verify_cleanup(audit, pub, junk_patterns=frozenset(), duplicate_pairs=[])
    assert not rep.ok
    kinds = {f.kind for f in rep.findings_by_phase["must_remain"]}
    assert "surviving_dataset_missing" in kinds


def test_anomaly_junk_empty_and_duplicate_pair(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    pub = tmp_path / "pub"
    _publish(pub, "junky", ["SUBJID"])
    _publish(pub, "emptyform", None, empty=True)
    _publish(pub, "pairA", ["SUBJID"])
    _publish(pub, "pairB", ["SUBJID"])

    rep = verify_cleanup(
        audit,
        pub,
        junk_patterns=frozenset({"junky"}),
        duplicate_pairs=[("pairA", "pairB")],
    )
    assert not rep.ok
    kinds = {f.kind for f in rep.findings_by_phase["anomaly"]}
    assert "junk_file_published" in kinds
    assert "empty_published_dataset" in kinds
    assert "duplicate_pair_both_published" in kinds


def test_handles_absent_published_dir(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write_ledger(audit, "junkstem", [_junk("junkstem")])
    rep = verify_cleanup(
        audit, tmp_path / "nonexistent", junk_patterns=frozenset(), duplicate_pairs=[]
    )
    # Junk correctly absent (dir missing) → must-gone satisfied; no datasets.
    assert rep.checked_datasets == 0
    assert rep.ok
