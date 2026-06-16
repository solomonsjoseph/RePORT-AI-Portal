"""Architectural boundary guards (Note 19 one-way dependency rule).

These tests enforce the one-way dependency contract documented in the pipeline
re-architecture plan (``docs/plans/pipeline_redesign_implementation_plan.md``,
Wave 0; rationale: Note 20 Gap C + Note 19):

    plugins/  ->  scripts/        (allowed: skills import shared utilities)
    scripts/  -X> plugins/        (FORBIDDEN: shared utilities never depend on
                                   plugin code)

The shared extraction utilities (``scripts/extraction/dedup.py`` and
``scripts/extraction/io/*``) are deliberately kept under ``scripts/`` and
imported read-only by plugin skills.  They must never import from ``plugins/``,
or the dependency would become circular and the utilities could no longer be a
stable shared base.  This test makes that invariant executable so a future wave
cannot silently introduce a ``scripts/`` -> ``plugins/`` import.

Documented exception — the Note-19 migration bridge
---------------------------------------------------
``scripts/__init__.py`` installs a ``sys.meta_path`` finder that loads the
handful of pipeline modules now physically living under
``plugins/.../skills/<skill>/scripts/`` (e.g. ``phi_scrub``, ``phi_review``,
``dataset_pipeline``) under their original ``scripts.*`` canonical names. That
bridge realises the edge through a *file-path spec string*, never a literal
``import plugins`` / ``from plugins import`` statement, so it is intentionally
invisible to this AST guard. This is the ONLY sanctioned ``scripts/`` ->
``plugins/`` edge and is a deliberate migration mechanism (the one-way rule
remains the goal); the guard below still forbids every *literal* such import,
which is what would actually create a hard, non-removable circular dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo root: tests/ is a direct child of the project root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _iter_scripts_py_files() -> list[Path]:
    """Every ``*.py`` file under ``scripts/`` (recursively), sorted.

    ``__pycache__`` byte-compiled files are ``*.pyc`` and never matched by the
    ``*.py`` glob, so no explicit exclusion is needed.
    """
    return sorted(_SCRIPTS_DIR.rglob("*.py"))


def _imports_from_plugins(tree: ast.AST) -> list[str]:
    """Return the offending ``plugins``-rooted module names imported in ``tree``.

    Detects both ``import plugins...`` (``ast.Import``) and
    ``from plugins... import ...`` (``ast.ImportFrom``).  A module name counts
    as a ``plugins`` import when it is exactly ``plugins`` or begins with
    ``plugins.`` — so an unrelated module such as ``pluginshelper`` is not a
    false positive.  Relative imports (``from . import x``, ``module=None``)
    cannot reach ``plugins/`` and are ignored.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "plugins" or name.startswith("plugins."):
                    offenders.append(name)
        # node.level > 0 is a relative import (cannot target plugins/).
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            module = node.module
            if module == "plugins" or module.startswith("plugins."):
                offenders.append(module)
    return offenders


def test_scripts_dir_exists() -> None:
    """Guard against a silently-passing test if the layout ever changes."""
    assert _SCRIPTS_DIR.is_dir(), f"expected scripts/ at {_SCRIPTS_DIR}"


def test_scripts_never_import_from_plugins() -> None:
    """No file under ``scripts/`` may import from ``plugins/`` (Note 19).

    The dependency edge ``scripts/ -> plugins/`` is forbidden: the shared
    extraction utilities (and all of ``scripts/``) form a stable base that
    plugin skills import read-only, never the reverse.  This test parses each
    ``scripts/*.py`` file with ``ast`` (so comments and string literals
    mentioning ``plugins`` are ignored) and fails with the exact offending
    file + module name(s) if the edge is ever introduced.
    """
    py_files = _iter_scripts_py_files()
    # Sanity: the tree is non-empty, otherwise the test is vacuously green.
    assert py_files, f"no *.py files found under {_SCRIPTS_DIR}"

    violations: list[str] = []
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - signals a broken file
            rel = path.relative_to(_REPO_ROOT)
            violations.append(f"{rel}: could not parse ({exc})")
            continue
        for module in _imports_from_plugins(tree):
            rel = path.relative_to(_REPO_ROOT)
            violations.append(f"{rel}: imports forbidden module '{module}'")

    assert not violations, (
        "scripts/ must not import from plugins/ (Note 19 one-way dependency "
        "rule: plugins/ -> scripts/ only). Offending import(s):\n  " + "\n  ".join(violations)
    )
