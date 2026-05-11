# scripts/source_truth/evidence_pack_splitter.py
"""Split the unified catalog artifact returned by
`catalog.build_catalog_artifact()` into a compact-only catalog mapping
plus a {variable_id: evidence_pack} dict suitable for per-variable
serialization under `llm_source/study_metadata/evidence_packs/`.

Also renames `artifact_type` from the legacy `study_variable_catalog`
to the new `study_metadata_catalog` (CONTEXT.md §"Build Pipeline —
May 2026" hard invariant #2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["split_catalog_artifact"]


_NEW_CATALOG_ARTIFACT_TYPE = "study_metadata_catalog"


def split_catalog_artifact(
    catalog_artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return (compact_only_catalog, evidence_packs_by_variable_id).

    `catalog_artifact` is the mapping returned by
    `catalog.build_catalog_artifact()`. The function does not mutate it.
    """
    compact: dict[str, Any] = {}
    for k, v in catalog_artifact.items():
        if k == "evidence_packs":
            continue
        # Rename the builder's "records" key to "compact_records" so the
        # compact catalog shape is unambiguous from the unified artifact.
        if k == "records":
            compact["compact_records"] = v
        else:
            compact[k] = v
    if compact.get("artifact_type") == "study_variable_catalog":
        compact["artifact_type"] = _NEW_CATALOG_ARTIFACT_TYPE

    packs_raw = catalog_artifact.get("evidence_packs") or []
    packs: dict[str, dict[str, Any]] = {}
    for pack in packs_raw:
        if not isinstance(pack, Mapping):
            continue
        vid = pack.get("variable_id")
        if not vid:
            continue
        packs[vid] = dict(pack)
    return compact, packs
