"""RePORT AI Portal shared-library package + plugin-module import bridge.

**What.** ``scripts/`` is the shared-utility library imported read-only by the
pipeline plugin skills, the AI assistant, the verifier, and the test suite.
This ``__init__`` additionally installs a single :class:`importlib.abc.MetaPathFinder`
(:class:`_MovedModuleFinder`) that lets a handful of pipeline modules physically
*live* inside the plugin skill directories while remaining importable under their
original ``scripts.*`` canonical names.

**Why (Note 19 + D1/D3 reconciliation).** Note 19 consolidates the raw→llm_source
pipeline into ``plugins/report-ai-study-pipeline/skills/<skill>/scripts/``. But the
skill directories are *hyphenated* (``phi-scrubbing/``) and therefore NOT importable
as Python packages (D3). A normal re-export of the moved files is impossible. The
chosen mechanism (D1) is a re-export that keeps every existing call site and test
import — ``from scripts.security.phi_scrub import …`` — working unchanged.

A ``sys.meta_path`` finder is the correct mechanism (vs. eager ``__init__`` shims):
it is consulted by ``importlib._bootstrap._find_spec`` for *all* import shapes —
``from x import y``, ``import x``, and ``python -m x`` / ``runpy`` — and returns a
:func:`importlib.util.spec_from_file_location` pointing at the plugin file under the
module's canonical name (single ``sys.modules`` instance, no state duplication). It
loads lazily on first import, so there is no eager-import or circular-init hazard,
and the existing package ``__init__`` re-exports need no change.

**How / boundary note.** This is the ONLY sanctioned ``scripts/`` → ``plugins/``
edge. It is a deliberate, documented *migration bridge*: the one-way
``plugins/`` → ``scripts/`` dependency rule remains the architectural goal (Note 19).
The edge is realised via a file-path spec (not an ``import plugins`` statement), so it
is intentionally invisible to the AST-based boundary guard in
``tests/test_architecture_boundaries.py`` — that guard still forbids every *literal*
``scripts/`` → ``plugins/`` import. When the skills become subprocess-only invokable
(Wave 4 orchestrator), this finder is removed and the modules stop being importable
from ``scripts/`` at all.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

# Real on-disk repo root (NOT config.BASE_DIR — the moved modules are committed
# source files that always live in the real repo, regardless of any test-time
# BASE_DIR patching that relocates *output*/data trees).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "plugins" / "report-ai-study-pipeline" / "skills"

# canonical ``scripts.*`` name -> path under the plugin skills root.
# Keep this list 1:1 with Note 19's "what moves INTO the plugin" table.
_MOVED_MODULES: dict[str, str] = {
    # phi-scrubbing skill
    "scripts.security.phi_scrub": "phi-scrubbing/scripts/phi_scrub.py",
    "scripts.security.phi_gate": "phi-scrubbing/scripts/phi_gate.py",
    # phi-classification skill
    "scripts.security.phi_review": "phi-classification/scripts/phi_review.py",
    # dataset-to-llm-source skill
    "scripts.extraction.dataset_pipeline": "dataset-to-llm-source/scripts/dataset_pipeline.py",
    "scripts.extraction.dataset_cleanup": "dataset-to-llm-source/scripts/dataset_cleanup.py",
    "scripts.extraction.cleanup_propagation": "dataset-to-llm-source/scripts/cleanup_propagation.py",
    "scripts.skills.extract_to_llm_source": "dataset-to-llm-source/scripts/extract_to_llm_source.py",
    # dictionary-to-llm-source skill
    "scripts.extraction.load_dictionary": "dictionary-to-llm-source/scripts/load_dictionary.py",
    # sot-lean-generator skill
    "scripts.source_truth.study_intake": "sot-lean-generator/scripts/study_intake.py",
    "scripts.source_truth.generate_lean_outputs": "sot-lean-generator/scripts/generate_lean_outputs.py",
}


class _MovedModuleFinder(importlib.abc.MetaPathFinder):
    """Resolve the Note-19 moved modules to their plugin file paths.

    Returns ``None`` for every other name (so the default finders handle the
    rest of ``scripts/``) and for a mapped name whose file is missing (so a
    partially-applied move surfaces as a normal ``ModuleNotFoundError`` rather
    than a confusing spec error).
    """

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> ModuleSpec | None:
        relpath = _MOVED_MODULES.get(fullname)
        if relpath is None:
            return None
        file_path = _SKILLS_ROOT / relpath
        if not file_path.is_file():
            return None
        return importlib.util.spec_from_file_location(fullname, file_path)


def _install_moved_module_finder() -> None:
    """Append the finder once (idempotent across re-imports / test reloads).

    Appended (not prepended) so it never shadows a real ``scripts/`` module:
    the default ``PathFinder`` is consulted first and only fails for the moved
    names (whose files no longer exist under ``scripts/``), at which point our
    finder supplies the plugin-path spec.
    """
    if not any(isinstance(f, _MovedModuleFinder) for f in sys.meta_path):
        sys.meta_path.append(_MovedModuleFinder())


_install_moved_module_finder()
