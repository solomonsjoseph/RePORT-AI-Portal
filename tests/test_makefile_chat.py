"""Static checks for chat Makefile runtime contracts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]


def test_chat_targets_use_locked_uv_run() -> None:
    """Starting chat should not update uv.lock as a side effect."""

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "UV_RUN_LOCKED ?= $(UV) run --locked" in makefile
    assert "@$(UV_RUN_LOCKED) $(CLI_GROUPS) python -c" in makefile
    assert "@$(UV_RUN_LOCKED) $(CHAT_GROUPS) python -c" in makefile
    assert "@$(UV_RUN_LOCKED) $(CLI_GROUPS) python main.py --chat" in makefile
    assert "@$(UV_RUN_LOCKED) $(CHAT_GROUPS) python main.py --web" in makefile


def test_quickstart_and_study_targets_match_plugin_flow() -> None:
    """Quickstart enters the Load Study UI; `make study` is the publish entry.

    The publish path is now the orchestrator: `make study STUDY=<name>` exports
    STUDY_NAME and runs the 10-phase orchestrator. The old `main.py`-flag targets
    (pipeline/build-bundle/dictionary/extract-datasets/bundle) are gone.
    """

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "quickstart: sync chat" in makefile
    assert "Sync → web UI → Load Study plugin" in makefile
    # The study build delegates to the orchestrator with STUDY_NAME exported.
    assert "study:" in makefile
    assert "STUDY_NAME=$(STUDY) $(UV) run --all-groups python $(ORCHESTRATOR)" in makefile
    # The removed legacy targets must not reappear.
    assert "main.py --build-bundle" not in makefile
    assert "main.py --pipeline" not in makefile
