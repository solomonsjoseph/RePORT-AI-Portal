# scripts/source_truth/build.py
"""SoT-driven build coordinator.

CLI entrypoint that reads:
    - data/{study}/{form_id}_policy.yaml × N (manual SoT, frozen)
    - data/{study}/study_concepts.yaml (concept SoT)
    - optional column inventory from dataset extraction

and emits to output/{study}/:
    - llm_source/study_metadata_catalog.json
    - llm_source/evidence_packs/{variable_id}.json
    - llm_source/concept_index.json (initial — analysis_queryable=null)
    - audit/phi_handling_ledger.declared.json
    - audit/dataset_cleanup_ledger.declared.json

If column_inventory is provided, also emits to staging/llm_source/:
    - phi_handled_dataset_schema.json
    - concept_index.json (enriched copy)

Manual policy YAMLs are not modified. PHI scrub, dedup, and consumer
chat tools are not invoked by this module.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["BuildCoordinatorError", "main", "run_build"]


class BuildCoordinatorError(RuntimeError):
    """Raised when the build coordinator cannot proceed."""


def run_build(
    *,
    study: str,
    policies_dir: Path,
    concepts_file: Path,
    output_root: Path,
    column_inventory: Path | None,
) -> dict[str, Any]:
    """Run the full Branch Y emission and (if column inventory present) Stage 2.

    Returns a summary dict listing emitted file paths and counts.
    """
    if not policies_dir.is_dir():
        raise BuildCoordinatorError(f"policies_dir does not exist: {policies_dir}")
    if not concepts_file.is_file():
        raise BuildCoordinatorError(f"concepts_file does not exist: {concepts_file}")

    llm_source_dir = output_root / "llm_source"
    audit_dir = output_root / "audit"
    staging_llm_source_dir = output_root / "staging" / "llm_source"
    staging_audit_dir = output_root / "staging" / "audit"
    evidence_pack_dir = llm_source_dir / "evidence_packs"

    for directory in (llm_source_dir, audit_dir, staging_llm_source_dir, staging_audit_dir, evidence_pack_dir):
        directory.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "study": study,
        "output_root": str(output_root),
        "emitted": [],
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.source_truth.build")
    parser.add_argument("--study", required=True)
    parser.add_argument("--policies-dir", type=Path, required=True)
    parser.add_argument("--concepts-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--column-inventory", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = run_build(
            study=args.study,
            policies_dir=args.policies_dir,
            concepts_file=args.concepts_file,
            output_root=args.output_root,
            column_inventory=args.column_inventory,
        )
    except BuildCoordinatorError as exc:
        logger.error("build failed: %s", exc)
        return 2

    logger.info("build summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
