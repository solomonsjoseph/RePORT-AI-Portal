"""Tests for the pre-delete manifest writer + clean-legacy CLI."""

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

    monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(output_marker)))


# ---------------------------------------------------------------------------
# manifest writer + delete_legacy_dirs
# ---------------------------------------------------------------------------


def test_manifest_captures_all_files_in_legacy_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest must contain SHA-256 for every file in the legacy dirs."""
    output_root = tmp_path / "output" / "Indo-VAP"
    audit = output_root / "audit"
    audit.mkdir(parents=True)
    _patch_output_marker(monkeypatch, tmp_path / "output")

    trio = output_root / "trio_bundle" / "datasets"
    trio.mkdir(parents=True)
    f1 = trio / "form_a.jsonl"
    f1.write_text('{"A": 1}\n', encoding="utf-8")

    staging = output_root / "staging"
    staging.mkdir()
    f2 = staging / "llm_source_staging.json"
    f2.write_text("{}", encoding="utf-8")

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


def test_manifest_skips_missing_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    (output_root / "trio_bundle" / "datasets" / "f.jsonl").write_text("{}")
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

    _patch_output_marker(monkeypatch, tmp_path / "output")

    output_root = tmp_path / "output" / "Indo-VAP"
    (output_root / "trio_bundle").mkdir(parents=True)

    manifest_path = tmp_path / "not_an_output_dir" / "x.json"
    manifest_path.parent.mkdir(parents=True)

    with pytest.raises(ZoneViolationError):
        write_pre_delete_manifest(output_root=output_root, manifest_path=manifest_path)


# ---------------------------------------------------------------------------
# main() CLI orchestration
# ---------------------------------------------------------------------------


def test_cli_orchestrates_in_correct_order(tmp_path: Path, monkeypatch) -> None:
    """main() must: (1) write manifest, (2) prune packs, (3) delete dirs - in order."""
    from scripts.utils import pre_delete_cleanup

    call_order: list[str] = []

    def fake_write_manifest(**kwargs):
        call_order.append("write_manifest")
        assert "audit" in str(kwargs["manifest_path"])
        return {}

    def fake_prune(**kwargs):
        call_order.append("prune")
        return 42

    def fake_delete(**kwargs):
        call_order.append("delete")

    monkeypatch.setattr(pre_delete_cleanup, "write_pre_delete_manifest", fake_write_manifest)
    monkeypatch.setattr(pre_delete_cleanup, "prune_per_variable_packs", fake_prune)
    monkeypatch.setattr(pre_delete_cleanup, "delete_legacy_dirs", fake_delete)

    rc = pre_delete_cleanup.main([])

    assert rc == 0
    assert call_order == ["write_manifest", "prune", "delete"]


def test_cli_module_exposes_main() -> None:
    from scripts.utils import pre_delete_cleanup

    assert callable(pre_delete_cleanup.main)


def test_cli_dry_run_does_not_call_destructive_functions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """--dry-run must not call prune or delete; it MAY still call write_manifest."""
    import config
    from scripts.utils import pre_delete_cleanup

    called: list[str] = []

    def fake_write(**kwargs):
        called.append("write_manifest")
        return {}

    def fake_prune(**kwargs):
        called.append("prune")
        return 0

    def fake_delete(**kwargs):
        called.append("delete")

    monkeypatch.setattr(pre_delete_cleanup, "write_pre_delete_manifest", fake_write)
    monkeypatch.setattr(pre_delete_cleanup, "prune_per_variable_packs", fake_prune)
    monkeypatch.setattr(pre_delete_cleanup, "delete_legacy_dirs", fake_delete)

    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(config, "SOT_DIR", tmp_path / "sot")
    monkeypatch.setattr(config, "LLM_SOURCE_EVIDENCE_PACKS_DIR", tmp_path / "packs")

    rc = pre_delete_cleanup.main(["--dry-run"])
    assert rc == 0
    assert "prune" not in called
    assert "delete" not in called
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_cli_no_dry_run_calls_all(tmp_path: Path, monkeypatch) -> None:
    """No --dry-run keeps existing behavior (all 3 calls in order)."""
    import config
    from scripts.utils import pre_delete_cleanup

    called: list[str] = []

    monkeypatch.setattr(
        pre_delete_cleanup,
        "write_pre_delete_manifest",
        lambda **_kw: called.append("write_manifest") or {},
    )
    monkeypatch.setattr(
        pre_delete_cleanup, "prune_per_variable_packs", lambda **_kw: called.append("prune") or 0
    )
    monkeypatch.setattr(
        pre_delete_cleanup, "delete_legacy_dirs", lambda **_kw: called.append("delete")
    )

    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(config, "SOT_DIR", tmp_path / "sot")
    monkeypatch.setattr(config, "LLM_SOURCE_EVIDENCE_PACKS_DIR", tmp_path / "packs")

    rc = pre_delete_cleanup.main([])
    assert rc == 0
    assert called == ["write_manifest", "prune", "delete"]
