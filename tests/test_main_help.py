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

    assert "Clinical data host publish path" in result.stdout
    assert "Run host publish path: Dict" in result.stdout
    assert "Clinical data processing pipeline" not in result.stdout
    assert "Full pipeline: Extract" not in result.stdout
