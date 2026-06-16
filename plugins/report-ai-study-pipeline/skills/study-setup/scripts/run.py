#!/usr/bin/env python3
"""Skill entrypoint: study-setup (interactive/setup — NOT an orchestrator phase).

Prepares a study for a publish run *outside* the 10-phase DAG (Gap 4): it
pre-creates the run directory tree and reports readiness of the required inputs
(``config/<study>/_forms_manifest.yaml``, ``config/<study>/_study_privacy.yaml``,
the raw datasets dir, and the PHI HMAC key). With ``--bootstrap-key`` it creates
a fresh 0600 HMAC key when none exists (refusing to overwrite an existing key,
which would invalidate every prior pseudonym). Emits a value-free SkillResult.

This run.py is the non-interactive scaffold; the rich interactive wizard lives
in the host UI (``scripts/ai_assistant/ui/wizard.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.skill_protocol import (  # noqa: E402
    SkillResult,
    add_common_skill_args,
    emit_skill_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a study for a publish run.")
    add_common_skill_args(parser)
    parser.add_argument(
        "--bootstrap-key",
        action="store_true",
        help="Create a fresh 0600 PHI HMAC key when none exists (never overwrites).",
    )
    args = parser.parse_args(argv)

    import config

    config.ensure_directories()
    config.ensure_run_directories(study=args.study, run_id=args.run_id)

    forms_manifest = Path(config.study_config_path("_forms_manifest.yaml", study=args.study))
    study_privacy = Path(config.study_config_path("_study_privacy.yaml", study=args.study))
    datasets_dir = Path(config.RAW_DATA_DIR) / args.study / "datasets"
    key_path = Path(config.PHI_KEY_PATH) if config.PHI_KEY_PATH else None

    key_created = False
    if args.bootstrap_key and key_path is not None and not key_path.is_file():
        from scripts.security.phi_scrub import bootstrap_key

        bootstrap_key(key_path)
        key_created = True

    readiness = {
        "forms_manifest": forms_manifest.is_file(),
        "study_privacy": study_privacy.is_file(),
        "datasets_dir": datasets_dir.is_dir(),
        "phi_key": bool(key_path and key_path.is_file()),
    }
    missing = sorted(name for name, present in readiness.items() if not present)

    emit_skill_result(
        SkillResult(
            skill="study-setup",
            ok=not missing,
            exit_code=0 if not missing else 1,
            summary=("ready" if not missing else f"missing: {', '.join(missing)}")
            + (" (key created)" if key_created else ""),
            data={"study": args.study, "readiness": readiness, "key_created": key_created},
        )
    )
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
