"""Doc-freshness linter for the RePORT AI Portal.

What. Compares live, source-of-truth values (tool count from
``ALL_TOOLS``, repo version from ``__version__``, action-class count from
``phi_scrub.yaml``) against the prose in README, AGENTS, sphinx user/dev
guides, and the IRB dossier. Also rejects forbidden phrases that
indicate retired architecture (vector DB / RAG / Presidio-as-active /
"only zone the LLM agent reads" / stale tool counts / stale Make
targets).

Why. Three rounds of freshness sweeps converged the docs to current
state, but inline counts and architecture words drift the moment code
changes. Doing this in CI means a future PR that adds a 13th tool — or
removes one — fails the docs-quality-check stage with a precise pointer
to the line(s) that need updating, instead of silently producing stale
docs that the next reviewer has to discover from scratch.

How. Two passes:

1. **Live-value comparison** — import ``ALL_TOOLS`` and
   ``__version__``, parse ``phi_scrub.yaml`` for action classes,
   parse ``conformance_matrix.md`` for the criterion split. For each
   live value, look for forbidden patterns ("11 structured-data
   tools", "12 callables", etc.) that contradict it. Report
   contradictions.
2. **Forbidden-phrase scan** — a curated list of patterns that should
   NEVER appear in any tracked doc (vector index claims, "only zone
   the LLM agent reads", retired Make targets, etc.).

The linter exits non-zero on any finding, which fails CI. Each finding
prints ``path:line: REASON`` so the dev sees exactly where to look.
Disclaimers ("no chunking, no embedding") are passed through allowlist
patterns that match the canonical phrasing.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Repository root resolved relative to this script.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Files / directories the linter scans for prose drift.
TRACKED_FILES: tuple[str, ...] = ("README.md",)
TRACKED_DIRS: tuple[str, ...] = (
    "docs/sphinx",
    "docs/irb_dossier",
)
TRACKED_GLOBS: tuple[str, ...] = (
    "*.rst",
    "*.md",
)
# Directory names anywhere in a path that mark generated / vendored content.
# Matched as path *parts* so e.g. ``docs/sphinx/_build/html/index.html`` is
# excluded, but a hypothetical ``docs/sphinx/_buildguide.rst`` (no such file
# today) is not.
EXCLUDED_PATH_PARTS: frozenset[str] = frozenset(
    {
        "_build",
        "_static",
        "_templates",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


@dataclass(frozen=True)
class Finding:
    """One drift instance: file, line, and the reason it's stale."""

    path: Path
    line_no: int
    line: str
    reason: str

    def render(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return f"{rel}:{self.line_no}: {self.reason}\n    > {self.line.rstrip()}"


def _iter_tracked_files() -> Iterable[Path]:
    """Yield every tracked doc file as an absolute Path.

    Skips paths that contain any segment in :data:`EXCLUDED_PATH_PARTS`,
    so locally-generated build output (e.g., ``docs/sphinx/_build/``,
    which is gitignored but commonly present after ``make docs``) does
    not produce noisy false positives or slow the linter down.
    """
    for name in TRACKED_FILES:
        candidate = REPO_ROOT / name
        if candidate.is_file():
            yield candidate
    for directory in TRACKED_DIRS:
        base = REPO_ROOT / directory
        if not base.is_dir():
            continue
        for glob in TRACKED_GLOBS:
            for path in base.rglob(glob):
                if EXCLUDED_PATH_PARTS.intersection(path.parts):
                    continue
                yield path


def _live_tool_count() -> int:
    """Read the canonical ``ALL_TOOLS`` length without importing the package.

    Avoids the import-time side effects of ``scripts.ai_assistant.agent_tools``
    (langchain, ollama, etc. — heavy and not always installed). Instead,
    parse the literal list from the source file.
    """
    src = (REPO_ROOT / "scripts" / "ai_assistant" / "agent_tools.py").read_text()
    match = re.search(r"ALL_TOOLS\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not match:
        raise RuntimeError("ALL_TOOLS literal not found in agent_tools.py")
    body = match.group(1)
    # Count non-empty, non-comment lines that look like a bare identifier.
    entries = [
        line.strip().rstrip(",")
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return len(entries)


def _live_version() -> str:
    """Read ``__version__`` literal from the canonical source file."""
    src = (REPO_ROOT / "__version__.py").read_text()
    match = re.search(r'__version__\s*:\s*str\s*=\s*"([^"]+)"', src)
    if not match:
        raise RuntimeError("__version__ literal not found in __version__.py")
    return match.group(1)


def _live_action_class_count() -> int:
    """Count distinct action classes in ``scripts/security/phi_scrub.yaml``.

    The catalog ships eight: keep / birthdate / drop / cap / generalize /
    suppress_small_cell / date_jitter / id_pseudonymize. Each appears as
    a top-level YAML key (``<name>_fields:`` or ``<name>_field:``).
    """
    yaml_path = REPO_ROOT / "scripts" / "security" / "phi_scrub.yaml"
    if not yaml_path.is_file():
        return 8  # fall back to documented constant
    expected = {
        "keep_fields",
        "birthdate_field",
        "drop_fields",
        "cap_fields",
        "generalize_fields",
        "suppress_small_cell_fields",
        "date_fields",
        "id_fields",
    }
    seen: set[str] = set()
    for line in yaml_path.read_text().splitlines():
        head = line.split(":", 1)[0].strip()
        if head in expected:
            seen.add(head)
    return len(seen) or 8


# ---------------------------------------------------------------------------
# Forbidden phrases — each entry is (regex, reason). Matches are reported
# unless the line ALSO matches one of the allowlist patterns paired with the
# entry. Patterns are case-insensitive unless noted.
# ---------------------------------------------------------------------------

FORBIDDEN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Stale tool counts. Match phrasing like "12 tools", "12 structured-data
    # tools", "12 callables", "fixed list of 12 callables", etc. Plural is the
    # normal doc form, so the trailing noun MUST allow ``s?``.
    (
        r"\b(\d+)\s+(?:structured(?:[-\s]?data)?|callable|@tool)s?\s+(?:tools?|callables?)\b",
        "stale tool count — current ALL_TOOLS length is {tool_count}",
        (),
    ),
    (
        r"\bfixed\s+list\s+of\s+(\d+)\s+callables?\b",
        "stale tool count — current ALL_TOOLS length is {tool_count}",
        (),
    ),
    # Bare "N tools" / "N callables" without a leading qualifier. Catches
    # phrasings like "12 tools" alone in prose.
    (
        r"\b(\d+)\s+(?:tools?|callables?)\b(?!\s+(?:run|exec|invocation))",
        "stale tool count — current ALL_TOOLS length is {tool_count}",
        (
            # Allowlist — when the cited count matches live ALL_TOOLS, it's
            # picked up by the count-equality short-circuit below.
        ),
    ),
    # Vector-DB / RAG / chunking as architectural CLAIMS (not disclaimers)
    (
        r"vector\s+(db|store|index|database)",
        "vector DB residue — pipeline does not build a vector index",
        (
            r"no\s+(chunking|embedding|vector)",
            r"without\s+(chunking|embedding|vector)",
            r"vector\s+db\s+integration\s*\(if",  # future-work note
            r"how\s+the\s+agent\s+reads\s+the\s+bundle.*no\s+vector",
        ),
    ),
    (
        r"\bembedding\s+(index|store|model|search)",
        "embeddings residue — pipeline has no embedding step",
        (r"no\s+embedding", r"without\s+embedding"),
    ),
    (
        r"\bsemantic\s+(search|retrieval)",
        "semantic search residue — agent uses {tool_count} structured tools, no semantic retrieval",
        (r"no\s+semantic\s+(search|retrieval)",),
    ),
    # "Only zone" / "agent never exposed" residue
    (
        r"the\s+only\s+zone\s+the\s+(llm\s+)?agent",
        '"only zone" residue — current LLM read zone is trio_bundle + agent',
        (),
    ),
    (
        r"only\s+zone\s+the\s+downstream",
        '"only zone" residue — current LLM read zone is trio_bundle + agent',
        (),
    ),
    # 35-criterion / 31-criterion bare (without follow-up qualifier)
    (
        r"35-?criterion",
        "35-criterion needs the qualifier '(31 original + 4 added via patches 2026-04-23a/b)'",
        (
            r"31\s*original",
            r"31\s*\+\s*4",
            r"four\s+follow-?ups",
            r"plus\s+four\s+follow-?ups",
            r"4\s+added",
            r"35\s*/\s*35\s*criteria\s+architecturally",
        ),
    ),
    # Stale Makefile target names
    (
        r"\bmake\s+extract-pdfs\b",
        "stale Make target — use `make pdf-extract`",
        (),
    ),
    # Pre-scrubbed wording (operators don't pre-scrub; pipeline does at Step 1.6)
    (
        r"datasets?\s+must\s+be\s+pre-?scrubbed",
        '"pre-scrubbed" residue — pipeline scrubs at Step 1.6 on AMBER staging',
        (),
    ),
    (
        r"reads?\s+pre-?scrubbed\s+study",
        '"pre-scrubbed" residue — Step 1.6 in-pipeline scrub is canonical',
        (),
    ),
    (
        r"\.xls(?!x)",
        "legacy .xls residue — supported tabular inputs are .xlsx and .csv only",
        (),
    ),
    (
        r"xlsx,\s*xls,\s*csv",
        "legacy .xls residue — supported tabular inputs are .xlsx and .csv only",
        (),
    ),
    (
        r"\bvllm\b",
        "stale provider claim — supported provider IDs are openai, anthropic, google-genai, ollama, nvidia-ai-endpoints",
        (),
    ),
    (
        r"test_dataset_extraction\.py",
        "stale test filename — dataset extraction coverage lives in tests/test_dataset_pipeline.py",
        (),
    ),
    (
        r"test_date_transform\.py",
        "stale test filename — SANT/date coverage lives in tests/test_phi_scrub.py",
        (),
    ),
    (
        r"ai_assistant/\s+#.*planned",
        "stale test-tree claim — AI Assistant tests are active top-level tests, not a planned tests/ai_assistant folder",
        (),
    ),
    (
        r"extraction/\s+#.*planned",
        "stale test-tree claim — extraction tests are active top-level tests, not a planned tests/extraction folder",
        (),
    ),
    (
        r"\b(?:80|90|100)%\s+(?:code\s+)?coverage",
        "coverage threshold claim is not enforced by current CI; document runnable gates instead",
        (),
    ),
    # Stale streamlit version pin
    (
        r"streamlit\s+1\.5\d",
        "stale Streamlit pin — pyproject.toml requires >=1.38, <2.0",
        (),
    ),
    # Stale Llama default
    (
        r"\bllama-?[23]\b\s+(default|model|provider)",
        "stale default model — qwen3 replaced llama since commit df52ec4",
        (),
    ),
    # "PDF-snippet sanitiser" gap claims (closed in patch-2026-04-23a)
    (
        r"no\s+pdf-?snippet\s+(instruction\s+)?sanitiser\s+(today|currently)",
        "patch-2026-04-23a closed this gap (sanitise_untrusted_snippet)",
        (),
    ),
    (
        r"sanitiser\s+is\s+the\s+planned\s+hardening",
        "patch-2026-04-23a already shipped this hardening",
        (),
    ),
    # Old ONLY-zone residue in IRB diagrams (handled separately above; keep
    # an additional pattern for the ASCII variant)
    (
        r"<--\s*the\s+only\s+zone",
        '"the ONLY zone" residue — current LLM read zone is trio_bundle + agent',
        (),
    ),
    # ``__version__`` references must agree with the canonical literal in
    # ``__version__.py``. Match patterns like ``__version__ = "0.x.y"``,
    # ``"version": "0.x.y"``, or "current version 0.x.y" in prose. The
    # version-equality short-circuit in :func:`_check_file` skips matches
    # whose digits equal the live version.
    (
        # Match both ``__version__ = "X.Y.Z"`` and the annotated
        # ``__version__: str = "X.Y.Z"`` forms used in docs/code samples.
        r"__version__[^\"\n]*\"\d+\.\d+\.\d+\"",
        "stale __version__ literal — canonical is {version}",
        (),
    ),
    (
        r"current\s+version\s*:?\s*\d+\.\d+\.\d+",
        "stale 'current version' claim — canonical is {version}",
        (),
    ),
    # Action-class catalog count drift ("8-action catalog", "7-action
    # classes", etc.). The action-count short-circuit in
    # :func:`_check_file` skips correct-number matches.
    (
        r"\b\d+[-\s]+action\s+(?:catalog|catalogue|classes?|set)\b",
        "stale action-class count — canonical is {action_count} classes (see scripts/security/phi_scrub.yaml)",
        (),
    ),
)


def _check_file(
    path: Path,
    *,
    tool_count: int,
    version: str,
    action_count: int,
) -> list[Finding]:
    """Return every drift Finding produced by ``path``.

    Disclaimer allowlists are evaluated on a 5-line window (the matching
    line plus the two before and two after) so wrapped prose like::

        ... directly. No chunking, embedding, or
        vector index is needed.

    is recognised as a disclaimer even when "vector" lands on a separate
    line from "no".
    """
    findings: list[Finding] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return findings

    lines = text.splitlines()
    lower_lines = [line.lower() for line in lines]

    for index, raw in enumerate(lines):
        line_no = index + 1
        lower = lower_lines[index]
        # 5-line window: 2 before, current, 2 after.
        window_lo = max(0, index - 2)
        window_hi = min(len(lower_lines), index + 3)
        window_text = " ".join(lower_lines[window_lo:window_hi])

        for pattern, reason_tmpl, allowlist in FORBIDDEN:
            if not re.search(pattern, lower):
                continue
            if any(re.search(allow, window_text) for allow in allowlist):
                continue

            # Special-case the tool-count regexes: the patterns match both
            # correct (e.g., "12 tools") and stale (e.g., "10 tools") cases.
            # Skip when the captured number equals the live count. Match
            # plural and singular noun heads so "tools" / "callables" /
            # "@tool" / "structured-data tools" all resolve the count.
            count_match = re.search(
                r"\b(\d+)\s+(?:structured(?:[-\s]?data)?\s+)?(?:tools?|callables?|@?tool)",
                lower,
            )
            if count_match:
                cited = int(count_match.group(1))
                if cited == tool_count:
                    continue
            # Action-class count drift ("8-action catalog", "eight action
            # classes" — only the digit form is auto-checked here; the
            # spelled-out form is left alone because it doesn't drift in
            # practice).
            action_match = re.search(
                r"\b(\d+)[-\s]+action\s+(?:catalog|catalogue|classes?|set)",
                lower,
            )
            if action_match:
                cited = int(action_match.group(1))
                if cited == action_count:
                    continue
            # Version drift — only run the equality short-circuit when the
            # matched FORBIDDEN entry is itself version-related (otherwise an
            # unrelated FORBIDDEN match on a line that happens to mention the
            # live version would be silently dropped).
            if "version" in pattern.lower():
                version_match = re.search(r"\b(\d+\.\d+\.\d+)\b", lower)
                if version_match and version_match.group(1) == version:
                    continue

            reason = reason_tmpl.format(
                tool_count=tool_count,
                version=version,
                action_count=action_count,
            )
            findings.append(Finding(path=path, line_no=line_no, line=raw, reason=reason))
    return findings


def main() -> int:
    """Run every check and return a process exit code (0 = clean, 1 = drift)."""
    tool_count = _live_tool_count()
    version = _live_version()
    action_count = _live_action_class_count()

    print(
        f"[doc-freshness] live values: ALL_TOOLS={tool_count}, "
        f"__version__={version}, action_classes={action_count}",
        file=sys.stderr,
    )

    findings: list[Finding] = []
    for path in sorted(_iter_tracked_files()):
        findings.extend(
            _check_file(
                path,
                tool_count=tool_count,
                version=version,
                action_count=action_count,
            )
        )

    if not findings:
        print("[doc-freshness] OK — no stale-doc drift detected.", file=sys.stderr)
        return 0

    print(f"[doc-freshness] FAIL — {len(findings)} drift(s):", file=sys.stderr)
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    print(
        "\nRefresh the offending lines, then re-run "
        "`uv run --frozen python scripts/lint_doc_freshness.py`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
