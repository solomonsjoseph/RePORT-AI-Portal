#!/usr/bin/env python3
"""Skill entrypoint: raw-data-intake (skill 0, setup — NOT an orchestrator phase).

Sorts an unorganized study delivery (flat dump and/or zips) into the canonical
data/raw/<study>/ four-bucket layout and drafts config/<study>/_forms_manifest.yaml.
Classification is filename + extension ONLY (GR-1: no workbook is opened).
Idempotent: a no-op on an already-organized tree unless --force.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import intake  # noqa: E402

from scripts.utils.skill_protocol import (  # noqa: E402
    SkillResult,
    add_common_skill_args,
    emit_skill_result,
)


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val) if val else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sort an unorganized study delivery (skill 0).")
    add_common_skill_args(parser)
    parser.add_argument("--src", required=True, help="dir or zip of the unorganized delivery")
    parser.add_argument("--force", action="store_true", help="rebuild an already-organized tree")
    args = parser.parse_args(argv)

    try:
        result = intake.organize(
            args.study,
            Path(args.src),
            force=args.force,
            raw_root=_env_path("RPLN_INTAKE_RAW_ROOT"),
            config_root=_env_path("RPLN_INTAKE_CONFIG_ROOT"),
            audit_dir=_env_path("RPLN_INTAKE_AUDIT_DIR"),
        )
    except (FileNotFoundError, ValueError) as exc:
        emit_skill_result(
            SkillResult(
                skill="raw-data-intake",
                ok=False,
                exit_code=2,
                summary=f"intake failed: {exc}",
                data={"study": args.study},
            )
        )
        return 2

    if result.skipped:
        summary = "already organized — skipping"
    else:
        summary = "; ".join(f"{b}={n}" for b, n in sorted(result.counts.items()) if n)
    emit_skill_result(
        SkillResult(
            skill="raw-data-intake",
            ok=True,
            exit_code=0,
            summary=summary or "no files staged",
            data={
                "study": args.study,
                "skipped": result.skipped,
                "counts": result.counts,
                "unclassified": result.unclassified,
                "manifest_written": result.manifest_written,
                "review_note": result.review_note,
            },
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
