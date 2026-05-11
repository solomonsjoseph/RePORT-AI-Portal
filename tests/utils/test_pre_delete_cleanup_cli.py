"""Tests for the Phase 5b clean-legacy CLI."""
from __future__ import annotations

from pathlib import Path


def test_cli_orchestrates_in_correct_order(tmp_path: Path, monkeypatch) -> None:
    """main() must: (1) write manifest, (2) prune packs, (3) delete dirs - in order."""
    from scripts.utils import pre_delete_cleanup_cli

    call_order: list[str] = []

    def fake_write_manifest(**kwargs):
        call_order.append("write_manifest")
        # Verify manifest_path lands in expected dir
        assert "audit" in str(kwargs["manifest_path"])
        return {}

    def fake_prune(**kwargs):
        call_order.append("prune")
        return 42

    def fake_delete(**kwargs):
        call_order.append("delete")

    monkeypatch.setattr(pre_delete_cleanup_cli, "write_pre_delete_manifest", fake_write_manifest)
    monkeypatch.setattr(pre_delete_cleanup_cli, "prune_per_variable_packs", fake_prune)
    monkeypatch.setattr(pre_delete_cleanup_cli, "delete_legacy_dirs", fake_delete)

    rc = pre_delete_cleanup_cli.main([])

    assert rc == 0
    assert call_order == ["write_manifest", "prune", "delete"]


def test_cli_module_exposes_main() -> None:
    from scripts.utils import pre_delete_cleanup_cli
    assert callable(pre_delete_cleanup_cli.main)


def test_cli_dry_run_does_not_call_destructive_functions(tmp_path: Path, monkeypatch, capsys) -> None:
    """--dry-run must not call prune or delete; it MAY still call write_manifest."""
    import config
    from scripts.utils import pre_delete_cleanup_cli

    called: list[str] = []

    def fake_write(**kwargs):
        called.append("write_manifest")
        return {}

    def fake_prune(**kwargs):
        called.append("prune")
        return 0

    def fake_delete(**kwargs):
        called.append("delete")

    monkeypatch.setattr(pre_delete_cleanup_cli, "write_pre_delete_manifest", fake_write)
    monkeypatch.setattr(pre_delete_cleanup_cli, "prune_per_variable_packs", fake_prune)
    monkeypatch.setattr(pre_delete_cleanup_cli, "delete_legacy_dirs", fake_delete)

    # Point config paths somewhere that exists so audit_dir.is_dir() passes
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(config, "SOT_DIR", tmp_path / "sot")
    monkeypatch.setattr(config, "LLM_SOURCE_EVIDENCE_PACKS_DIR", tmp_path / "packs")

    rc = pre_delete_cleanup_cli.main(["--dry-run"])
    assert rc == 0
    assert "prune" not in called
    assert "delete" not in called
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_cli_no_dry_run_calls_all(tmp_path: Path, monkeypatch) -> None:
    """No --dry-run keeps existing behavior (all 3 calls in order)."""
    import config
    from scripts.utils import pre_delete_cleanup_cli

    called: list[str] = []

    monkeypatch.setattr(pre_delete_cleanup_cli, "write_pre_delete_manifest",
                        lambda **kw: called.append("write_manifest") or {})
    monkeypatch.setattr(pre_delete_cleanup_cli, "prune_per_variable_packs",
                        lambda **kw: called.append("prune") or 0)
    monkeypatch.setattr(pre_delete_cleanup_cli, "delete_legacy_dirs",
                        lambda **kw: called.append("delete"))

    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(config, "SOT_DIR", tmp_path / "sot")
    monkeypatch.setattr(config, "LLM_SOURCE_EVIDENCE_PACKS_DIR", tmp_path / "packs")

    rc = pre_delete_cleanup_cli.main([])  # no flags
    assert rc == 0
    assert called == ["write_manifest", "prune", "delete"]
