"""Reject provider-shaped API keys committed as static text."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
PROVIDER_KEY_PATTERNS = {
    "anthropic": re.compile(r"sk-ant-[A-Za-z]+\d*-[A-Za-z0-9_\-]{20,}"),
    "openai": re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{40,}"),
    "nvidia": re.compile(r"nvapi-[A-Za-z0-9_\-]{30,}"),
    "google": re.compile(r"AIza[A-Za-z0-9_\-]{35}"),
}


def _git_path() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.fail("git executable is required for tracked-file secret scanning")
    return git


def _tracked_text_files() -> list[Path]:
    result = subprocess.run(  # noqa: S603 - static git ls-files invocation.
        [_git_path(), "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        ROOT / path for path in result.stdout.splitlines() if Path(path).suffix in TEXT_SUFFIXES
    ]


def test_tracked_files_do_not_contain_static_provider_api_key_literals() -> None:
    findings: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for provider, pattern in PROVIDER_KEY_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_no}:{provider}")

    assert findings == []
