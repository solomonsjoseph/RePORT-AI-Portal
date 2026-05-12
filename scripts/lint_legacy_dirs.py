r"""Linter: fail build if legacy output directory name strings appear in scripts/.

Phase 5b Task 7. Blocks regressions of removed legacy directory concepts.

Hard-block patterns (full match anywhere in line, with token boundaries):
  - ``human_review``   — only as a bare token (not inside compound
    identifiers like ``needs_human_review`` or ``PDF_EVIDENCE_NEEDS_HUMAN_REVIEW``).
    Phase 5a moved the live review-queue dir to
    ``config.TMP_DIR / <study> / "human_review"``; references via TMP_DIR
    are allowed via the line-allow rule below.

Context-scoped patterns (only when quoted as a path component):
  - ``"trio_bundle"`` adjacent to path separators or quoted as a standalone
    path component (e.g. ``output / "trio_bundle"``). The parent
    ``trio_bundle/`` dir survives as the PHI-scrubbed clean zone; the
    Phase 5b move was about its *contents* (datasets, dictionary) relocating
    to ``llm_source/``. References via ``config.TRIO_BUNDLE_DIR`` are allowed
    by the line-allow rule. User-facing UI strings (e.g.
    ``"Run a fresh load to produce \`\`trio_bundle/\`\`"``) do not match the
    path-component patterns and therefore do not flag.
  - ``"staging"`` adjacent to path separators or quoted as a standalone path
    component. The bare identifier ``staging`` and constants like
    ``STAGING_UMASK`` / ``_STAGING_DIR_MODE`` survive as TMP-zone concepts
    and must NOT trigger. TMP_DIR-rooted staging (the Phase 5a intent) is
    allowed via the TMP_DIR line-allow rule.
  - ``"agent"`` only when used as an output-path component
    (e.g. ``output / "agent"``); module names like ``agent_tools.py`` and
    AI-agent references stay clean.

Per-line allow rules (do NOT flag the line):
  - The line is a pure comment (first non-whitespace char is ``#``).
  - The line is inside a triple-quoted docstring block.
  - The line references ``TMP_DIR`` (e.g. ``config.TMP_DIR / study / "staging"``).
  - The line references ``TRIO_BUNDLE_DIR`` (live PHI-scrubbed clean zone).

Skipped files (the file itself legitimately names the legacy strings):
  - ``lint_legacy_dirs.py``       (this linter — its regex literals self-flag)
  - ``lint_doc_freshness.py``     (sibling doc-freshness linter — catalogs
    legacy strings as detection patterns)
  - ``pre_delete_cleanup.py``     (the legacy-deletion tool)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Hard-block tokens — match only when the token is NOT embedded in a larger
# identifier (so ``needs_human_review`` / ``PDF_EVIDENCE_NEEDS_HUMAN_REVIEW``
# do not trigger). Uses lookarounds for character-class boundaries that
# include the underscore as a word char.
_FULL_MATCH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?<![A-Za-z0-9_])human_review(?![A-Za-z0-9_])"),
]

# Path-component patterns. Each requires the token to appear inside a quoted
# string adjacent to a path separator, an opening/closing path expression,
# or directly after the word ``output``.
_PATH_COMPONENT_PATTERNS: list[re.Pattern[str]] = [
    # "trio_bundle" surrounded by path separators or path delimiters
    re.compile(r'[/\s(]["\']trio_bundle["\'][/\s),]'),
    re.compile(r'["\']trio_bundle["\']\s*/'),
    re.compile(r'/\s*["\']trio_bundle["\']'),
    re.compile(r'output[^"\'\n]*["\']trio_bundle["\']'),
    # Bare quoted "trio_bundle/..." string literal (legacy path prefix)
    re.compile(r'["\']trio_bundle/'),
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

# Files that legitimately mention the legacy names and must be skipped.
_SKIP_FILES: frozenset[str] = frozenset(
    {
        "lint_legacy_dirs.py",
        "lint_doc_freshness.py",
        "pre_delete_cleanup.py",
    }
)

# Per-line allow tokens. If any appears on the same line as a would-be
# violation, the line is treated as a legitimate live reference.
_LINE_ALLOW_TOKENS: tuple[str, ...] = (
    "TMP_DIR",
    "TRIO_BUNDLE_DIR",
)

_TRIPLE_QUOTE_RE = re.compile(r'"""|\'\'\'')


def _is_line_allowed(line: str) -> bool:
    """Return True if the line is an allowed live reference (skip violation)."""
    return any(tok in line for tok in _LINE_ALLOW_TOKENS)


def check_file(path: Path) -> list[str]:
    """Return list of violation strings (one per offending line).

    Files in ``_SKIP_FILES`` (this linter, the sibling doc-freshness linter,
    the legacy-deletion tool, and its CLI wrapper) are exempt. Comment-only
    lines and lines inside triple-quoted docstrings are skipped. Lines
    containing live-reference allow tokens (``TMP_DIR``, ``TRIO_BUNDLE_DIR``)
    are skipped to permit Phase 5a TMP-zone staging/human_review and live
    PHI-scrubbed trio_bundle references.
    """
    if path.name in _SKIP_FILES:
        return []
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    in_docstring = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Track triple-quoted docstring state. An odd count toggles state;
        # the line is treated as docstring whenever the state was True at
        # the start OR the line contains any triple quotes (so single-line
        # docstrings and the opening/closing lines are both suppressed).
        tq_count = len(_TRIPLE_QUOTE_RE.findall(line))
        line_in_docstring = in_docstring or tq_count > 0
        if tq_count and tq_count % 2 == 1:
            in_docstring = not in_docstring
        if line_in_docstring:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _is_line_allowed(line):
            continue
        for pat in _FULL_MATCH_PATTERNS:
            if pat.search(line):
                violations.append(f"{path}:{lineno}: legacy directory name found: {line.strip()}")
                break
        else:
            for pat in _PATH_COMPONENT_PATTERNS:
                if pat.search(line):
                    violations.append(f"{path}:{lineno}: legacy path component: {line.strip()}")
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
