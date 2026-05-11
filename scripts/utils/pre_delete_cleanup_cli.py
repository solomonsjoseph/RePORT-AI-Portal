"""CLI entry point for the Phase 5b clean-legacy operation."""
import argparse
import sys
from pathlib import Path

import config
from scripts.utils.evidence_pack_pruner import _form_names_from_sot, prune_per_variable_packs
from scripts.utils.pre_delete_cleanup import (
    _LEGACY_DIR_NAMES,
    delete_legacy_dirs,
    write_pre_delete_manifest,
)
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _print_dry_run_plan(*, output_root: Path, packs_dir: Path, sot_dir: Path) -> None:
    """Print what the real run WOULD delete. Read-only."""
    print("\n=== DRY RUN — no files will be deleted ===\n")

    # Legacy dirs
    print(f"Legacy directories under {output_root}:")
    for name in _LEGACY_DIR_NAMES:
        target = output_root / name
        if target.is_dir():
            file_count = sum(1 for _ in target.rglob("*") if _.is_file())
            print(f"  [WILL DELETE] {target} ({file_count} files)")
        else:
            print(f"  [skip — not present] {target}")

    # Per-variable evidence packs
    if packs_dir.is_dir():
        keep = _form_names_from_sot(sot_dir) if sot_dir.is_dir() else frozenset()
        all_packs = sorted(packs_dir.glob("*.json"))
        per_variable = [p for p in all_packs if p.stem not in keep]
        per_form = [p for p in all_packs if p.stem in keep]
        print(f"\nEvidence packs in {packs_dir}:")
        print(f"  [KEEP] {len(per_form)} per-form packs (matching SoT policy YAMLs)")
        print(f"  [WILL DELETE] {len(per_variable)} per-variable packs")
        if per_variable:
            preview = ", ".join(p.name for p in per_variable[:10])
            print(f"    first 10: {preview}{' ...' if len(per_variable) > 10 else ''}")
    else:
        print(f"\nEvidence packs dir not present: {packs_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5b: clean legacy output dirs.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted; do not modify the filesystem (except the manifest).",
    )
    args = parser.parse_args(argv)

    output_root = Path(config.STUDY_OUTPUT_DIR)
    audit_dir = Path(config.STUDY_AUDIT_DIR)
    sot_dir = Path(config.SOT_DIR)
    packs_dir = Path(config.LLM_SOURCE_EVIDENCE_PACKS_DIR)

    manifest_path = audit_dir / "lineage_manifest_pre_delete.json"

    logger.info("Phase 5b clean-legacy: study=%s (dry_run=%s)", config.STUDY_NAME, args.dry_run)

    # Manifest write is observational (SHA-256 snapshot) — safe in dry-run.
    # Skip only if audit_dir is absent AND we're in dry-run (avoid creating dirs).
    if audit_dir.is_dir() or not args.dry_run:
        audit_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Writing pre-delete manifest to %s", manifest_path)
        write_pre_delete_manifest(output_root=output_root, manifest_path=manifest_path)

    if args.dry_run:
        _print_dry_run_plan(output_root=output_root, packs_dir=packs_dir, sot_dir=sot_dir)
        print("\n=== END DRY RUN — re-run without --dry-run to execute. ===\n")
        return 0

    logger.info("Pruning per-variable evidence packs from %s", packs_dir)
    deleted_packs = prune_per_variable_packs(packs_dir=packs_dir, sot_dir=sot_dir)
    logger.info("Pruned %d per-variable evidence packs", deleted_packs)

    logger.info("Deleting legacy output dirs under %s", output_root)
    delete_legacy_dirs(output_root=output_root)

    logger.info("Phase 5b clean-legacy complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
