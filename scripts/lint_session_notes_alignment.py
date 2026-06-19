#!/usr/bin/env python3
"""Fail-closed grep audit for session-notes alignment drift (Notes 1–22).

Checks tracked source/docs for patterns that contradict
``now-we-are-going-playful-dove.md`` (joined-view-only agent SoT, P2 raw dedup,
legacy excel-handler not on active path). Used by the completeness loop.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (pattern, human label, allowed paths substring — if any path contains this, skip)
RULES: list[tuple[re.Pattern[str], str, tuple[str, ...]]] = [
    (
        re.compile(r"scripts/source_truth/generate_lean_outputs\.py(?!.*importable)"),
        "Use python -m scripts.source_truth.generate_lean_outputs, not a missing file path",
        ("scripts/__init__.py", "LEGACY_ARCHITECTURE", "lint_session_notes"),
    ),
    (
        re.compile(r"excel-duplicate-handler.*once per study", re.I),
        "excel-duplicate-handler is legacy; orchestrator uses dataset-deduplication P2",
        ("LEGACY", "legacy", "session_notes_alignment", "lint_session_notes"),
    ),
    (
        re.compile(r"role:\s*preflight(?!.*legacy)"),
        "Use role: legacy_preflight for excel-duplicate-handler",
        ("lint_session_notes",),
    ),
    (
        re.compile(r"find_policy_yaml\s*\("),
        "find_policy_yaml removed; agent uses joined views only (Note 3)",
        ("lint_session_notes",),
    ),
]

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "output",
    "tmp",
    "data",
    "__pycache__",
    ".pytest_cache",
}

SCAN_SUFFIXES = {".py", ".rst", ".md", ".yaml", ".yml"}


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("docs/plans/"):
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        out.append(path)
    return out


def main() -> int:
    failures: list[str] = []
    for path in _iter_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, label, allow_substrings in RULES:
            if not pattern.search(text):
                continue
            if any(sub in rel or sub in text for sub in allow_substrings):
                continue
            failures.append(f"{rel}: {label}")

    if failures:
        print("session-notes alignment lint FAILED:", file=sys.stderr)
        for line in sorted(set(failures)):
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("session-notes alignment lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
