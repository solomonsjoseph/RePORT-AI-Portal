"""Console-script entry point for ``report-ai-pipeline``.

Pre-scans ``sys.argv`` for ``--root`` / ``--pack-dir`` and exports them as
``REPORT_AI_ROOT`` / ``REPORT_AI_STUDY_PACK_DIR`` *before* importing
:mod:`scripts.skills.extract_to_llm_source`.

Why this indirection is required: ``extract_to_llm_source`` imports
``config`` lazily inside ``_cmd_run``/``_cmd_verify``, but its *module-level*
imports (``scripts.audit.ledger``, ``scripts.extraction.dataset_pipeline`` ->
``scripts.extraction.dedup``) import ``config`` eagerly. Python resolves all
of a module's top-level imports before calling any function defined in it —
including a console-script's ``main()`` — so by the time ``main()`` runs,
``config`` (and its module-level ``DATA_ROOT``/``BASE_DIR``-derived
constants) is already cached in ``sys.modules``. Setting the env var from
inside ``main()`` is too late; it must happen before
``scripts.skills.extract_to_llm_source`` is imported at all.
"""

from __future__ import annotations

import os
import sys


def _prescan_root_and_pack_dir(argv: list[str]) -> None:
    """Extract ``--root``/``--pack-dir`` from *argv* into the environment.

    A minimal, tolerant scan (not a full argparse pass) — it only needs to
    find these two flags before the real parser (inside
    ``extract_to_llm_source``) runs and validates everything properly.
    """
    root: str | None = None
    pack_dir: str | None = None
    it = iter(argv)
    for arg in it:
        if arg == "--root":
            root = next(it, None)
        elif arg.startswith("--root="):
            root = arg.split("=", 1)[1]
        elif arg == "--pack-dir":
            pack_dir = next(it, None)
        elif arg.startswith("--pack-dir="):
            pack_dir = arg.split("=", 1)[1]

    if root:
        os.environ["REPORT_AI_ROOT"] = root
    if pack_dir:
        os.environ["REPORT_AI_STUDY_PACK_DIR"] = pack_dir


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns an integer exit code."""
    argv = sys.argv[1:] if argv is None else argv
    _prescan_root_and_pack_dir(argv)

    from scripts.skills.extract_to_llm_source import main as _dispatch

    return _dispatch(argv)


if __name__ == "__main__":
    sys.exit(main())
