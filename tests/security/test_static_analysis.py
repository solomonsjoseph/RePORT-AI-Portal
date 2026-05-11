"""Static-analysis security checks: scan tracked source files for prohibited patterns."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# No static provider API key literals in tracked files
# ---------------------------------------------------------------------------

_TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
_PROVIDER_KEY_PATTERNS = {
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
        ROOT / path for path in result.stdout.splitlines() if Path(path).suffix in _TEXT_SUFFIXES
    ]


def test_tracked_files_do_not_contain_static_provider_api_key_literals() -> None:
    findings: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for provider, pattern in _PROVIDER_KEY_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_no}:{provider}")

    assert findings == []


# ---------------------------------------------------------------------------
# No row values in metadata path writes
# ---------------------------------------------------------------------------

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
    return ROOT / "scripts"


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
    return any(_is_row_value_node(sub) for sub in ast.walk(node))


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
                        violations.append(
                            f"{path}:{node.lineno} json.{node.func.attr} on row-value expression"
                        )
    return violations


def test_no_row_values_in_metadata_paths() -> None:
    violations: list[str] = []
    for py in _scripts_dir().rglob("*.py"):
        if py.name == "__init__.py":
            continue
        violations.extend(_walk_for_violations(py))
    assert not violations, "row values written to metadata path:\n" + "\n".join(violations)


def test_metadata_check_catches_known_violation(tmp_path: Path) -> None:
    bad = tmp_path / "scripts" / "fake_writer.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "import json\n"
        "def go(rows, path):\n"
        "    target = 'output/study/llm_source/study_metadata/evidence_packs/foo.json'\n"
        "    json.dump(list(rows.values()), open(path, 'w'))\n"
    )
    violations = _walk_for_violations(bad)
    assert violations, "static check failed to catch a known row-value violation"


def test_metadata_check_allow_pragma_suppresses(tmp_path: Path) -> None:
    good = tmp_path / "scripts" / "ok_writer.py"
    good.parent.mkdir(parents=True)
    good.write_text(
        "import json\n"
        "def go(rows, path):\n"
        "    target = 'output/study/llm_source/study_metadata/evidence_packs/foo.json'\n"
        "    # phi-static: allow row=keys-only\n"
        "    json.dump(list(rows.values()), open(path, 'w'))\n"
    )
    violations = _walk_for_violations(good)
    assert not violations
