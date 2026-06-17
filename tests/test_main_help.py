"""CLI help text regression checks."""

from __future__ import annotations

import subprocess
import sys


def test_pipeline_help_names_host_publish_path() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AI Assistant launcher" in result.stdout
    assert "make study STUDY=<name>" in result.stdout
    assert "--chat" in result.stdout
    assert "--web" in result.stdout
    # The publish path moved to the orchestrator; it is no longer a launcher flag.
    assert "--pipeline" not in result.stdout
