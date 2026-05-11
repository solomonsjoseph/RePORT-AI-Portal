"""Delete legacy per-variable evidence packs; keep only per-form packs."""
from __future__ import annotations

from pathlib import Path

from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _form_names_from_sot(sot_dir: Path) -> frozenset[str]:
    """Return form names from top-level and dataset_policies/ policy YAMLs."""
    names: set[str] = set()
    for yaml_path in sot_dir.glob("*_policy.yaml"):
        names.add(yaml_path.stem.removesuffix("_policy"))
    dp_dir = sot_dir / "dataset_policies"
    if dp_dir.is_dir():
        for yaml_path in dp_dir.glob("*_policy.yaml"):
            names.add(yaml_path.stem.removesuffix("_policy"))
    return frozenset(names)


def prune_per_variable_packs(*, packs_dir: Path, sot_dir: Path) -> int:
    """Delete .json files in packs_dir whose stem is not a known form name.

    Returns the number of files deleted.
    """
    keep = _form_names_from_sot(sot_dir)
    deleted = 0
    for pack in sorted(packs_dir.glob("*.json")):
        if pack.stem not in keep:
            pack.unlink()
            deleted += 1
    logger.info(
        "evidence pack pruner: deleted %d per-variable packs, kept %d per-form packs",
        deleted,
        len(list(packs_dir.glob("*.json"))),
    )
    return deleted
