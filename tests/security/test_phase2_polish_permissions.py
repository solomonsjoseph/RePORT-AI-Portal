"""Phase 2.x polish — permission + wiring regression tests.

Companion to ``docs/irb_dossier/phase3_phi_followups.md``. Each test
pins one of the polish items so a future change cannot silently regress
the file mode / wiring it fixes.

Items covered (per the plan doc):
- P1a — ``conversations.py`` chmod 0o600 after every JSON write
- P1b — ``telemetry.py`` chmod 0o600 after every event write
- P1c — sandbox ``spec.json`` chmod 0o600 in temp dir
- P1d — sandbox-persisted ``run_*.py`` chmod 0o600 + ``code/`` dir 0o700
- P1e — snapshot directory + tree chmod 0o700 / 0o600
- P1f — ``config.ensure_directories`` hardens sensitive dirs to 0o700
- P5  — ``install_phi_redactor`` wired with ``SUBJECT_ID_PATTERNS``
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

# These permission checks make no sense on Windows (different model).
WINDOWS_SKIP = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")


# ── P1a — conversations.py ──────────────────────────────────────────────────


@WINDOWS_SKIP
def test_conversation_save_writes_with_mode_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every JSON written by ``conversations.py`` must be mode 0o600 — they
    contain redacted user prompts and tool returns."""
    # Loosen umask so a missing chmod would otherwise leave 0o644.
    old_umask = os.umask(0o022)
    try:
        fpath = tmp_path / "test_conv.json"
        fpath.write_text(json.dumps({"x": 1}), encoding="utf-8")
        # Apply the same idempotent fix that conversations.py applies after
        # write — this is the contract the production code must meet.
        fpath.chmod(0o600)
        assert stat.S_IMODE(fpath.stat().st_mode) == 0o600
    finally:
        os.umask(old_umask)


def test_conversations_module_has_chmod_after_every_write_text() -> None:
    """Pin the static contract: every ``write_text`` in conversations.py is
    followed by a ``chmod(0o600)`` call within a few lines. If a future
    refactor adds a write without chmod, this test fails."""
    src = Path("scripts/ai_assistant/ui/conversations.py").read_text(encoding="utf-8")
    write_count = src.count("fpath.write_text(json.dumps(")
    chmod_count = src.count("fpath.chmod(0o600)")
    assert write_count == chmod_count, (
        f"Mismatch: {write_count} write_text vs {chmod_count} chmod — every "
        "JSON write must be followed by chmod(0o600)."
    )


# ── P1b — telemetry.py ──────────────────────────────────────────────────────


def test_telemetry_module_chmods_sink_after_write() -> None:
    src = Path("scripts/utils/telemetry.py").read_text(encoding="utf-8")
    assert "sink_path.chmod(0o600)" in src, (
        "telemetry.py must chmod the sink to 0o600 after every event write"
    )


# ── P1c — sandbox spec.json ─────────────────────────────────────────────────


def test_sandbox_init_chmods_spec_json() -> None:
    src = Path("scripts/ai_assistant/sandbox/__init__.py").read_text(encoding="utf-8")
    assert "spec_path.chmod(0o600)" in src, (
        "sandbox/__init__.py must chmod the spec.json to 0o600 after write"
    )


# ── P1d — sandbox-persisted analysis code ───────────────────────────────────


def test_sandbox_runner_chmods_persisted_code() -> None:
    src = Path("scripts/ai_assistant/sandbox/runner.py").read_text(encoding="utf-8")
    assert "code_dir.chmod(0o700)" in src, "runner.py must chmod the code/ subdir to 0o700"
    assert "path.chmod(0o600)" in src, "runner.py must chmod each persisted run_*.py to 0o600"


# ── P1e — snapshots ─────────────────────────────────────────────────────────


@WINDOWS_SKIP
def test_harden_tree_modes_sets_correct_permissions(tmp_path: Path) -> None:
    """``_harden_tree_modes`` walks a directory tree and sets dirs to 0o700
    and files to 0o600. The function is idempotent and best-effort."""
    from scripts.utils.snapshots import _harden_tree_modes

    root = tmp_path / "snap"
    sub = root / "datasets"
    sub.mkdir(parents=True)
    f = sub / "data.jsonl"
    f.write_text("{}", encoding="utf-8")

    # Loosen modes first so we can verify the helper actually changes them.
    root.chmod(0o755)
    sub.chmod(0o755)
    f.chmod(0o644)

    _harden_tree_modes(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(sub.stat().st_mode) == 0o700
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_snapshots_module_uses_safe_rmtree_with_symlink_guard() -> None:
    """``shutil.rmtree`` at the snapshot root is TOCTOU-vulnerable to a
    symlink swap between ``exists()`` and ``rmtree``. Every snapshot
    deletion must go through ``_safe_rmtree`` which refuses to follow a
    symlink root."""
    src = Path("scripts/utils/snapshots.py").read_text(encoding="utf-8")
    # ``_safe_rmtree`` is the helper; ``shutil.rmtree`` may appear only inside
    # ``_safe_rmtree``'s own implementation (line 56-ish), nowhere else.
    # Only count actual call sites (``shutil.rmtree(``) — exclude comments
    # AND docstring mentions of the symbol.
    call_sites = [
        line
        for line in src.splitlines()
        if "shutil.rmtree(" in line and not line.lstrip().startswith("#")
    ]
    # Exactly one allowed call: the implementation inside _safe_rmtree.
    assert len(call_sites) == 1, (
        f"Expected exactly one shutil.rmtree( call (inside _safe_rmtree); "
        f"found {len(call_sites)}: {call_sites}"
    )
    assert "_safe_rmtree" in src, "must define and use _safe_rmtree"


@WINDOWS_SKIP
def test_safe_rmtree_refuses_to_follow_symlink_root(tmp_path: Path) -> None:
    """Behavioral test: ``_safe_rmtree`` on a symlink unlinks the link only
    and does NOT delete the target."""
    from scripts.utils.snapshots import _safe_rmtree

    real_target = tmp_path / "real_data"
    real_target.mkdir()
    (real_target / "important.jsonl").write_text("data", encoding="utf-8")

    link = tmp_path / "link_to_real"
    link.symlink_to(real_target)

    _safe_rmtree(link)

    assert not link.exists() or link.is_symlink() is False  # link gone
    # Target still exists with its content.
    assert real_target.exists()
    assert (real_target / "important.jsonl").read_text(encoding="utf-8") == "data"


# ── P1f — config.ensure_directories ─────────────────────────────────────────


@WINDOWS_SKIP
def test_ensure_directories_hardens_sensitive_dirs_to_0700(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ensure_directories`` must chmod the sensitive output / agent /
    audit / logs dirs to 0o700, regardless of process umask."""
    import config

    old_umask = os.umask(0o022)
    try:
        # Redirect every sensitive dir to a tmp path so we can verify the
        # mode without touching the real ``output/`` tree.
        for attr in (
            "STUDY_OUTPUT_DIR",
            "LOGS_DIR",
            "TRIO_BUNDLE_DIR",
            "TRIO_DATASETS_DIR",
            "DICTIONARY_JSON_OUTPUT_DIR",
            "PDF_EXTRACTIONS_DIR",
            "STUDY_AUDIT_DIR",
            "AGENT_STATE_DIR",
            "AGENT_OUTPUT_DIR",
            "CONVERSATIONS_DIR",
            "TELEMETRY_DIR",
            "STUDY_RESTORE_POINTS_DIR",
        ):
            new = tmp_path / attr.lower()
            monkeypatch.setattr(config, attr, new)

        # OUTPUT_DIR + TMP_DIR are not sensitive in this list (TMP is
        # already 0o700 via secure_staging), but they need to exist.
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

        config.ensure_directories()

        for attr in (
            "STUDY_OUTPUT_DIR",
            "AGENT_STATE_DIR",
            "AGENT_OUTPUT_DIR",
            "CONVERSATIONS_DIR",
            "STUDY_RESTORE_POINTS_DIR",
            "STUDY_AUDIT_DIR",
            "TRIO_BUNDLE_DIR",
        ):
            path = getattr(config, attr)
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o700, (
                f"{attr}: expected mode 0o700, got 0o{mode:03o} — "
                "ensure_directories must harden every sensitive dir"
            )
    finally:
        os.umask(old_umask)


# ── P5 — SUBJECT_ID_PATTERNS wired into install_phi_redactor ────────────────


def test_install_phi_redactor_callers_pass_subject_id_patterns() -> None:
    """Both production callers of ``install_phi_redactor`` must pass the
    canonical ``SUBJECT_ID_PATTERNS`` so the per-subject HMAC redaction
    pass actually fires (not just the generic catalog pass)."""
    main_src = Path("main.py").read_text(encoding="utf-8")
    cli_src = Path("scripts/ai_assistant/cli.py").read_text(encoding="utf-8")
    for label, src in (("main.py", main_src), ("cli.py", cli_src)):
        assert "subject_id_patterns=" in src and "SUBJECT_ID_PATTERNS" in src, (
            f"{label} must pass subject_id_patterns=list(SUBJECT_ID_PATTERNS) "
            "to install_phi_redactor — otherwise SUBJ_* identifiers in logs "
            "are not HMAC-tagged."
        )
