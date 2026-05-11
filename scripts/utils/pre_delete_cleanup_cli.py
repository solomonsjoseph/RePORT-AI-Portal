"""CLI entry point for the Phase 5b clean-legacy operation."""
import sys
from pathlib import Path

import config
from scripts.utils.evidence_pack_pruner import prune_per_variable_packs
from scripts.utils.pre_delete_cleanup import delete_legacy_dirs, write_pre_delete_manifest
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def main() -> int:
    output_root = Path(config.STUDY_OUTPUT_DIR)
    audit_dir = Path(config.STUDY_AUDIT_DIR)
    sot_dir = Path(config.SOT_DIR)
    packs_dir = Path(config.LLM_SOURCE_EVIDENCE_PACKS_DIR)

    manifest_path = audit_dir / "lineage_manifest_pre_delete.json"

    logger.info("Phase 5b clean-legacy: study=%s", config.STUDY_NAME)
    logger.info("Writing pre-delete manifest to %s", manifest_path)
    write_pre_delete_manifest(output_root=output_root, manifest_path=manifest_path)

    logger.info("Pruning per-variable evidence packs from %s", packs_dir)
    deleted_packs = prune_per_variable_packs(packs_dir=packs_dir, sot_dir=sot_dir)
    logger.info("Pruned %d per-variable evidence packs", deleted_packs)

    logger.info("Deleting legacy output dirs under %s", output_root)
    delete_legacy_dirs(output_root=output_root)

    logger.info("Phase 5b clean-legacy complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
