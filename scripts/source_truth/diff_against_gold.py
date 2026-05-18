"""SoT Phase-1 diff-against-gold CLI.

Cross-LLM portability note
--------------------------
This module is intentionally a thin CLI wrapper, not a ``.claude/skills/`` file.
The canonical shape is a callable CLI so that any agentic tool — ChatGPT, Gemini,
Cursor, Aider, or a plain shell script — can reach this capability without
Claude-specific infrastructure.

Usage
-----
    python -m scripts.source_truth.diff_against_gold \\
        --study Indo-VAP --form 6_HIV --candidate /tmp/candidate.lean.yaml

    uv run --all-groups python scripts/source_truth/diff_against_gold.py \\
        --study Indo-VAP --form 6_HIV --candidate /tmp/candidate.lean.yaml

Exit codes
----------
    0  — no novel diffs (diff is empty or all diffs are cosmetic/within_rule)
    1  — one or more novel diffs detected
    2  — argument or I/O error (e.g. gold not found, candidate unreadable)
"""

# ruff: noqa: S108

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Diff classification helpers
# ---------------------------------------------------------------------------


# Match spaces and tabs only — NOT newlines; multi-line scalar differences are substantive
_WHITESPACE_RE = re.compile(r"[ \t]+")


def _normalize_whitespace(s: str) -> str:
    """Collapse runs of spaces/tabs to a single space and strip ends.

    Newlines are intentionally excluded: a YAML block scalar ('|' / '>')
    producing ``"Hello\\nWorld"`` differs semantically from ``"Hello World"``,
    so newline differences must fall through to the novel classifier.
    """
    return _WHITESPACE_RE.sub(" ", s.replace("\r\n", "\n")).strip()


def _is_whitespace_only_diff(a: str, b: str) -> bool:
    """Return True when two strings differ only in whitespace distribution."""
    if a == b:
        return False  # not a diff at all
    return _normalize_whitespace(a) == _normalize_whitespace(b)


def _is_key_order_only_diff(a: dict, b: dict) -> bool:
    """Return True when two dicts have identical key sets + deeply equal values,
    but their key ordering differs.

    Both conditions must hold:
      - set(a.keys()) == set(b.keys())
      - list(a.keys()) != list(b.keys())  (order is different)
      - all corresponding values are deeply equal
    """
    if set(a.keys()) != set(b.keys()):
        return False
    if list(a.keys()) == list(b.keys()):
        return False  # same order — not a key-order diff
    # Check values are all equal
    return all(a[k] == b[k] for k in a)


def _types_match(a: Any, b: Any) -> bool:
    """Return True when *a* and *b* have compatible types for comparison.

    Allows int <-> float numeric widening, but treats bool as distinct from
    int: in YAML, ``true`` deserialises to :class:`bool` and ``1`` to
    :class:`int` — they are semantically different on a clinical form.
    """
    if type(a) is type(b):
        return True
    # bool is a subclass of int in Python; exclude it from the int/float
    # widening carve-out so that True vs 1 produces a novel type_change entry.
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    return isinstance(a, (int, float)) and isinstance(b, (int, float))


# ---------------------------------------------------------------------------
# Recursive tree walker
# ---------------------------------------------------------------------------


def _walk(
    candidate: Any,
    gold: Any,
    path: str,
    cosmetic: list[dict],
    novel: list[dict],
) -> None:
    """Recursively compare *candidate* against *gold*, classifying each diff.

    Parameters
    ----------
    candidate, gold:
        Arbitrary YAML-deserialized values (dict, list, scalar, None).
    path:
        Dot-separated path string used to label diff entries.
    cosmetic, novel:
        Accumulator lists, mutated in place.
    """

    # --- Both are dicts ---
    if isinstance(candidate, dict) and isinstance(gold, dict):
        if _is_key_order_only_diff(candidate, gold):
            # Record one cosmetic entry for the parent; do NOT recurse into
            # children to produce additional entries.
            cosmetic.append(
                {
                    "path": path,
                    "kind": "key_order",
                    "candidate_keys": list(candidate.keys()),
                    "gold_keys": list(gold.keys()),
                }
            )
            return

        # Same key set in same order — recurse into values.
        if set(candidate.keys()) == set(gold.keys()):
            for k in gold:
                child_path = f"{path}.{k}" if path else k
                _walk(candidate[k], gold[k], child_path, cosmetic, novel)
            return

        # Key sets differ — structural change: added and/or removed keys.
        added = set(candidate.keys()) - set(gold.keys())
        removed = set(gold.keys()) - set(candidate.keys())
        common = set(candidate.keys()) & set(gold.keys())

        novel.extend(
            {
                "path": f"{path}.{k}" if path else k,
                "kind": "key_added",
                "candidate": candidate[k],
                "gold": None,
            }
            for k in added
        )
        novel.extend(
            {
                "path": f"{path}.{k}" if path else k,
                "kind": "key_removed",
                "candidate": None,
                "gold": gold[k],
            }
            for k in removed
        )
        for k in common:
            child_path = f"{path}.{k}" if path else k
            _walk(candidate[k], gold[k], child_path, cosmetic, novel)
        return

    # --- Both are lists ---
    if isinstance(candidate, list) and isinstance(gold, list):
        if candidate == gold:
            return  # identical — nothing to record
        if len(candidate) != len(gold):
            novel.append(
                {
                    "path": path,
                    "kind": "list_length_change",
                    "candidate_len": len(candidate),
                    "gold_len": len(gold),
                    "candidate": candidate,
                    "gold": gold,
                }
            )
            return
        # Same length — compare element by element
        for i, (c_elem, g_elem) in enumerate(zip(candidate, gold)):
            _walk(c_elem, g_elem, f"{path}[{i}]", cosmetic, novel)
        return

    # --- Type mismatch ---
    if not _types_match(candidate, gold):
        novel.append(
            {
                "path": path,
                "kind": "type_change",
                "candidate_type": type(candidate).__name__,
                "gold_type": type(gold).__name__,
                "candidate": candidate,
                "gold": gold,
            }
        )
        return

    # --- Both are strings ---
    if isinstance(candidate, str) and isinstance(gold, str):
        if candidate == gold:
            return
        if _is_whitespace_only_diff(candidate, gold):
            cosmetic.append(
                {
                    "path": path,
                    "kind": "whitespace",
                    "candidate": candidate,
                    "gold": gold,
                }
            )
            return
        novel.append(
            {
                "path": path,
                "kind": "value_change",
                "candidate": candidate,
                "gold": gold,
            }
        )
        return

    # --- Scalar comparison (int, float, bool, None) ---
    if candidate != gold:
        novel.append(
            {
                "path": path,
                "kind": "value_change",
                "candidate": candidate,
                "gold": gold,
            }
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_diff(candidate: Any, gold: Any) -> dict[str, list]:
    """Return the structured diff dict with keys cosmetic, within_rule, novel."""
    cosmetic: list[dict] = []
    within_rule: list[dict] = []
    novel: list[dict] = []

    _walk(candidate, gold, "", cosmetic, novel)

    return {
        "cosmetic": cosmetic,
        "within_rule": within_rule,  # always empty in Phase 1
        "novel": novel,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.source_truth.diff_against_gold",
        description=(
            "Phase-1 SoT diff-against-gold: compare a candidate lean YAML "
            "against the frozen gold and classify differences as cosmetic, "
            "within_rule, or novel.  "
            "Exit 0 when all diffs are cosmetic/within_rule or absent; "
            "Exit 1 when novel diffs exist; Exit 2 on I/O errors."
        ),
    )
    p.add_argument("--study", required=True, help="Study folder name (e.g. Indo-VAP)")
    p.add_argument("--form", required=True, help="Form identifier (e.g. 6_HIV)")
    p.add_argument(
        "--candidate",
        required=True,
        type=Path,
        help="Path to the candidate lean YAML to evaluate",
    )
    p.add_argument(
        "--repo-root",
        default=None,
        type=Path,
        help=(
            "Repository root directory. "
            "Defaults to the parent of the 'scripts/' directory relative to "
            "this file, or CWD if that heuristic fails."
        ),
    )
    p.add_argument(
        "--out",
        default=None,
        type=Path,
        help="Path for the output diff JSON (default: /tmp/diff_<form>.json)",
    )
    return p


def _resolve_repo_root(explicit: Path | None) -> Path:
    """Resolve the repository root.

    Priority:
    1. Explicit --repo-root argument.
    2. Parent of the 'scripts/' directory that contains this file.
    3. CWD.
    """
    if explicit is not None:
        return explicit.resolve()

    # Heuristic: walk up from this file until we find a directory containing
    # a 'scripts/' subdirectory, which is the repo root.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts").is_dir() and (parent / "data").is_dir():
            return parent

    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = _resolve_repo_root(args.repo_root)

    # Resolve gold path
    gold_path = repo_root / "data" / "SoT" / args.study / f"{args.form}_policy.lean.yaml"
    if not gold_path.exists():
        print(
            f"error: gold YAML not found for study={args.study!r} form={args.form!r}\n"
            f"  expected: {gold_path}",
            file=sys.stderr,
        )
        return 2

    # Resolve output path
    out_path: Path = args.out if args.out is not None else Path(f"/tmp/diff_{args.form}.json")

    # Load candidate
    candidate_path: Path = args.candidate
    if not candidate_path.exists():
        print(
            f"error: candidate YAML not found: {candidate_path}",
            file=sys.stderr,
        )
        return 2

    try:
        candidate_data = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: failed to load candidate YAML: {exc}", file=sys.stderr)
        return 2

    # Load gold
    try:
        gold_data = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: failed to load gold YAML: {exc}", file=sys.stderr)
        return 2

    # Build diff
    diff = build_diff(candidate_data, gold_data)

    # Write output JSON
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(diff, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"error: failed to write diff JSON: {exc}", file=sys.stderr)
        return 2

    # Summary line
    n_cosmetic = len(diff["cosmetic"])
    n_within = len(diff["within_rule"])
    n_novel = len(diff["novel"])
    print(
        f"cosmetic={n_cosmetic} within_rule={n_within} novel={n_novel} wrote={out_path}"
    )

    return 0 if n_novel == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
