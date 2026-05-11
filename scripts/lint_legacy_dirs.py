"""Linter: fail build if legacy output directory name strings appear in scripts/.

Phase 5b Task 7. Blocks regressions of removed legacy directory concepts.

Hard-block patterns (full match anywhere in line):
  - ``trio_bundle``    (legacy bundle dir)
  - ``human_review``   (legacy review-queue dir)

Context-scoped patterns (only when quoted as a path component):
  - ``"staging"`` / ``'staging'`` adjacent to path separators or quoted as a
    standalone path component. The bare identifier ``staging`` and constants
    like ``STAGING_UMASK`` / ``_STAGING_DIR_MODE`` survive as TMP-zone
    concepts and must NOT trigger.
  - ``"agent"`` / ``'agent'`` only when used as an output-path component
    (e.g. ``output / "agent"``); module names like ``agent_tools.py`` and
    AI-agent references stay clean.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns that are unambiguously legacy — match anywhere in a line.
_FULL_MATCH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"trio_bundle"),
    re.compile(r"human_review"),
]

# Path-component patterns. Each requires the token to appear inside a quoted
# string adjacent to a path separator, an opening/closing path expression,
# or directly after the word ``output``.
_PATH_COMPONENT_PATTERNS: list[re.Pattern[str]] = [
    # "staging" surrounded by path separators or path delimiters
    re.compile(r'[/\s(]["\']staging["\'][/\s),]'),
    re.compile(r'["\']staging["\']\s*/'),
    re.compile(r'/\s*["\']staging["\']'),
    re.compile(r'output[^"\'\n]*["\']staging["\']'),
    # "agent" used as an output-path component
    re.compile(r'[/\s(]["\']agent["\'][/\s),]'),
    re.compile(r'["\']agent["\']\s*/'),
    re.compile(r'/\s*["\']agent["\']'),
    re.compile(r'output[^"\'\n]*["\']agent["\']'),
]


def check_file(path: Path) -> list[str]:
    """Return list of violation strings (one per offending line).

    The linter file itself is exempt — its regex literals would otherwise
    self-flag.
    """
    if path.name == "lint_legacy_dirs.py":
        return []
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pat in _FULL_MATCH_PATTERNS:
            if pat.search(line):
                violations.append(
                    f"{path}:{lineno}: legacy directory name found: {line.strip()}"
                )
                break
        else:
            for pat in _PATH_COMPONENT_PATTERNS:
                if pat.search(line):
                    violations.append(
                        f"{path}:{lineno}: legacy path component: {line.strip()}"
                    )
                    break
    return violations


def lint_scripts_dir(scripts_dir: Path) -> int:
    """Lint every .py file under ``scripts_dir``. Return 0 if clean, 1 otherwise."""
    all_violations: list[str] = []
    for py_file in sorted(scripts_dir.rglob("*.py")):
        all_violations.extend(check_file(py_file))
    for v in all_violations:
        print(v, file=sys.stderr)
    return 1 if all_violations else 0


def main() -> int:
    return lint_scripts_dir(Path("scripts"))


if __name__ == "__main__":
    sys.exit(main())
