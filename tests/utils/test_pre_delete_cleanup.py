"""Tests for the pre-delete manifest writer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _patch_output_marker(monkeypatch: pytest.MonkeyPatch, output_marker: Path) -> None:
    """Repoint secure_env._OUTPUT_MARKER at *output_marker* so zone guards
    accept tmp_path-based manifest paths."""
    import scripts.security.secure_env as secure_env

    monkeypatch.setattr(
        secure_env, "_OUTPUT_MARKER", os.path.realpath(str(output_marker))
    )


def test_manifest_captures_all_files_in_legacy_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest must contain SHA-256 for every file in the legacy dirs."""
    output_root = tmp_path / "output" / "Indo-VAP"
    audit = output_root / "audit"
    audit.mkdir(parents=True)
    _patch_output_marker(monkeypatch, tmp_path / "output")

    # Simulate legacy dirs with files
    trio = output_root / "trio_bundle" / "datasets"
    trio.mkdir(parents=True)
    f1 = trio / "form_a.jsonl"
    f1.write_text('{"A": 1}\n', encoding="utf-8")

    staging = output_root / "staging"
    staging.mkdir()
    f2 = staging / "llm_source_staging.json"
    f2.write_text('{}', encoding="utf-8")

    manifest_path = audit / "lineage_manifest_pre_delete.json"

    from scripts.utils.pre_delete_cleanup import write_pre_delete_manifest

    write_pre_delete_manifest(output_root=output_root, manifest_path=manifest_path)

    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["study"] == "Indo-VAP"
    assert "generated_utc" in data

    flat = {entry["path"]: entry["sha256"] for entry in data["deleted_files"]}
    key1 = str(f1.relative_to(output_root))
    key2 = str(f2.relative_to(output_root))
    assert key1 in flat
    assert key2 in flat
    assert flat[key1] == _sha256(f1)
    assert flat[key2] == _sha256(f2)


def test_manifest_skips_missing_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing legacy dirs are skipped silently — no error."""
    output_root = tmp_path / "output" / "Indo-VAP"
    audit = output_root / "audit"
    audit.mkdir(parents=True)
    _patch_output_marker(monkeypatch, tmp_path / "output")

    manifest_path = audit / "lineage_manifest_pre_delete.json"

    from scripts.utils.pre_delete_cleanup import write_pre_delete_manifest

    write_pre_delete_manifest(output_root=output_root, manifest_path=manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["deleted_files"] == []


def test_post_delete_structure(tmp_path: Path) -> None:
    """After delete_legacy_dirs, output_root contains only llm_source/ and audit/."""
    output_root = tmp_path / "output" / "Indo-VAP"
    (output_root / "audit").mkdir(parents=True)
    (output_root / "llm_source").mkdir()
    (output_root / "trio_bundle" / "datasets").mkdir(parents=True)
    (output_root / "trio_bundle" / "datasets" / "f.jsonl").write_text('{}')
    (output_root / "staging").mkdir()
    (output_root / "human_review").mkdir()

    from scripts.utils.pre_delete_cleanup import delete_legacy_dirs

    delete_legacy_dirs(output_root=output_root)

    remaining = {p.name for p in output_root.iterdir() if p.is_dir()}
    assert remaining == {"audit", "llm_source"}, (
        f"unexpected dirs after deletion: {remaining - {'audit', 'llm_source'}}"
    )


def test_manifest_rejects_path_outside_output_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zone guard must hard-fail when manifest_path is outside output/."""
    from scripts.security.secure_env import ZoneViolationError
    from scripts.utils.pre_delete_cleanup import write_pre_delete_manifest

    # Point _OUTPUT_MARKER at tmp_path/output, then attempt to write the
    # manifest into a sibling dir that is NOT under that marker.
    _patch_output_marker(monkeypatch, tmp_path / "output")

    output_root = tmp_path / "output" / "Indo-VAP"
    (output_root / "trio_bundle").mkdir(parents=True)

    manifest_path = tmp_path / "not_an_output_dir" / "x.json"
    manifest_path.parent.mkdir(parents=True)

    with pytest.raises(ZoneViolationError):
        write_pre_delete_manifest(
            output_root=output_root, manifest_path=manifest_path
        )
