"""Tests for the OR-combined PHI guard gate (Wave 3 C3, decision D2)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.security.phi_guard_gate import run_phi_guard_gate


def test_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text(
        json.dumps({"SUBJID": "RID_SUBJ_abcdefghijkl", "hgb": 12.5}) + "\n",
        encoding="utf-8",
    )
    res = run_phi_guard_gate(tmp_path)
    assert res.ok
    assert res.triggered_by == ()
    assert res.detail == ""


def test_email_leak_fails_both_scanners(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text(
        json.dumps({"note": "reach me at x@y.com"}) + "\n", encoding="utf-8"
    )
    res = run_phi_guard_gate(tmp_path)
    assert not res.ok
    # Both scanners catch a clear email — OR-combination reports both.
    assert "presidio" in res.triggered_by
    assert "legacy" in res.triggered_by
    assert "matched content omitted" in res.detail


def test_or_combination_fails_if_either_finds(tmp_path: Path) -> None:
    """The combined gate fails if EITHER sub-scanner flags PHI.

    A PAN is in the shared catalog, so both flag it; the key property is that
    ``ok`` is the logical AND of the two scanners' ``ok``.
    """
    (tmp_path / "a.jsonl").write_text(json.dumps({"id": "ABCDE1234F"}) + "\n", encoding="utf-8")
    res = run_phi_guard_gate(tmp_path)
    assert res.ok == (res.presidio.ok and res.legacy.ok)
    assert not res.ok


def test_missing_dir_passes(tmp_path: Path) -> None:
    assert run_phi_guard_gate(tmp_path / "absent").ok
