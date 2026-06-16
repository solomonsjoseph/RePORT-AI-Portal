#!/usr/bin/env python3
"""Skill entrypoint: dataset-deduplication (Phase 2).

Cleans the staging datasets tree: removes known junk files and merges only
*provably-safe* duplicate file pairs (keeping the larger when one is a strict
subset of the other); value-divergent pairs are routed to human review rather
than union-merged. **Fail-closed scrub-first** — ``clean_trio_datasets`` refuses
to run unless every staging row carries the ``_phi_scrubbed`` marker, so this
skill can never touch unscrubbed PHI. Invoked by the orchestrator as a file-path
subprocess (D3). Emits a value-free SkillResult (counts/names only).
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
    parser = argparse.ArgumentParser(description="Deduplicate staging dataset files (scrub-first).")
    add_common_skill_args(parser)
    args = parser.parse_args(argv)

    from scripts.extraction.dataset_cleanup import clean_trio_datasets

    try:
        report = clean_trio_datasets(study_name=args.study)
    except Exception as exc:  # UnscrubbedDatasetError + I/O — fail-closed
        emit_skill_result(
            SkillResult(
                skill="dataset-deduplication",
                ok=False,
                exit_code=1,
                summary=f"dedup refused/failed: {type(exc).__name__}",
                data={"study": args.study},
            )
        )
        print(f"dataset-deduplication failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    held = len(report.duplicates_skipped)
    emit_skill_result(
        SkillResult(
            skill="dataset-deduplication",
            ok=True,  # routing a divergent pair to review is a normal outcome
            summary=(
                f"{len(report.junk_removed)} junk, {len(report.duplicates_merged)} merged, "
                f"{held} held for review"
            ),
            data={
                "study": args.study,
                "junk_removed": len(report.junk_removed),
                "duplicates_merged": len(report.duplicates_merged),
                "duplicates_skipped": held,
                "errors": len(report.errors),
            },
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
