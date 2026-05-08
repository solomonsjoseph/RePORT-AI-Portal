"""Static-analysis check: no module under scripts/ writes row values to metadata paths.

Walks the AST of every .py file under scripts/, looking for:
  json.dump(d, ...) where d is constructed from row.values() / .iterrows() /
  similar row-level expressions, when the file path target string-matches one
  of the protected metadata path fragments.

The check is intentionally conservative: it flags suspicious patterns rather
than proving correctness. False positives must be either fixed (preferred)
or annotated with ``# phi-static: allow row=keys-only`` on the line above
the call.
"""

from __future__ import annotations

import ast
from pathlib import Path


_PROTECTED_PATH_FRAGMENTS = (
    "evidence_packs",
    "study_metadata_catalog",
    "dataset_schema/catalog",
    "sot_gap_drafts",
)

_ROW_LIKE_NAMES = {"iterrows", "itertuples", "to_dict"}

_ROW_VALUES_ATTRS = {"values"}

_ALLOW_PRAGMA = "# phi-static: allow row=keys-only"


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts"


def _line_above_has_allow(source: str, lineno: int) -> bool:
    if lineno <= 1:
        return False
    return _ALLOW_PRAGMA in source.splitlines()[lineno - 2]


def _is_row_value_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _ROW_LIKE_NAMES:
            return True
    if isinstance(node, ast.Attribute) and node.attr in _ROW_VALUES_ATTRS:
        return True
    return False


def _subtree_has_row_value(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if _is_row_value_node(sub):
            return True
    return False


def _walk_for_violations(path: Path) -> list[str]:
    src = path.read_text()
    if not any(frag in src for frag in _PROTECTED_PATH_FRAGMENTS):
        return []
    violations: list[str] = []
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"dump", "dumps"}:
                if any(_subtree_has_row_value(arg) for arg in node.args):
                    if not _line_above_has_allow(src, node.lineno):
                        violations.append(f"{path}:{node.lineno} json.{node.func.attr} on row-value expression")
    return violations


def test_no_row_values_in_metadata_paths() -> None:
    violations: list[str] = []
    for py in _scripts_dir().rglob("*.py"):
        if py.name == "__init__.py":
            continue
        violations.extend(_walk_for_violations(py))
    assert not violations, "row values written to metadata path:\n" + "\n".join(violations)


def test_check_catches_known_violation(tmp_path: Path) -> None:
    bad = tmp_path / "scripts" / "fake_writer.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "import json\n"
        "def go(rows, path):\n"
        "    target = 'output/study/llm_source/evidence_packs/foo.json'\n"
        "    json.dump(list(rows.values()), open(path, 'w'))\n"
    )
    violations = _walk_for_violations(bad)
    assert violations, "static check failed to catch a known row-value violation"


def test_allow_pragma_suppresses(tmp_path: Path) -> None:
    good = tmp_path / "scripts" / "ok_writer.py"
    good.parent.mkdir(parents=True)
    good.write_text(
        "import json\n"
        "def go(rows, path):\n"
        "    target = 'output/study/llm_source/evidence_packs/foo.json'\n"
        "    # phi-static: allow row=keys-only\n"
        "    json.dump(list(rows.values()), open(path, 'w'))\n"
    )
    violations = _walk_for_violations(good)
    assert not violations
