"""Restore-drill tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.utils.restore_drill import run_restore_drill


def test_restore_drill_uses_temp_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trio = tmp_path / "output" / "Indo-VAP" / "trio_bundle"
    snap = tmp_path / "data" / "snapshots" / "Indo-VAP"
    key = tmp_path / "phi.key"
    for root in (trio, snap):
        (root / "datasets").mkdir(parents=True)
        (root / "datasets" / "1_demo.jsonl").write_text('{"row": 1}\n', encoding="utf-8")
        (root / "variables.json").write_text('{"ok": true}', encoding="utf-8")
    key.write_text("not-a-real-key", encoding="utf-8")

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", trio)
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", snap)
    monkeypatch.setattr(config, "PHI_KEY_PATH", key)

    run_restore_drill()

    assert (trio / "variables.json").read_text(encoding="utf-8") == '{"ok": true}'
