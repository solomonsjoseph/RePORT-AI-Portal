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


def test_quickstart_and_dictionary_targets_match_plugin_flow() -> None:
    """Quickstart should enter the Load Study UI, while dictionary stays narrow."""

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "quickstart: sync chat" in makefile
    assert "Sync → web UI → Load Study plugin" in makefile
    assert "@$(PYTHON) main.py --build-bundle --skip-datasets" in makefile
