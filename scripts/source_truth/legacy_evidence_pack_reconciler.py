"""Legacy 947-pack reconciler.

Walks `output/<study>/llm_source/study_metadata/evidence_packs/` where legacy per-variable
JSONs and new per-form JSONs coexist. Legacy files are distinguished by a
top-level `variable_id` field; new per-form files have `form` + `variables[]`.

Diff produces:
- legacy_only: variables in legacy but absent from any per-form pack — one
  HITL draft per variable under `tmp/llm_source_reconcile_hitl_drafts/`.
- new_only: variables added by SoT-driven build but never in legacy — count
  only (no HITL).
- matched: present in both — count only.

A summary JSON is written under `tmp/`. All variable_ids in outputs are
masked via Phase 1's `mask_variable_id`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.security.phi_id_masker import mask_variable_id
from scripts.security.phi_scrub import PHIScrubError
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_LEGACY_FORM_PLACEHOLDER = "__legacy__"


class ReconcileError(PHIScrubError):
    pass


def _atomic_write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _hitl_body(masked: str) -> str:
    return (
        f"# HITL: legacy-only variable (masked) `{masked}`\n\n"
        "**Status:** DRAFT (Phase 2 reconciler, not yet filed)\n"
        f"**Variable (masked):** `{masked}`\n\n"
        "## Decision needed\n\n"
        "This variable exists in the legacy per-variable evidence pack but is\n"
        "absent from the SoT-derived per-form pack. Options:\n"
        "- Add to SoT YAML (recover into the new per-form pack).\n"
        "- Mark deprecated (will be deleted with legacy in Phase 5).\n\n"
        "## Labels\n\n"
        "`HITL`, `phase-2-reconcile`, `legacy-only`\n"
    )


def reconcile(
    *,
    evidence_packs_dir: Path | None = None,
    key_path: Path | None = None,
    hitl_drafts_dir: Path | None = None,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    evidence_packs_dir = (
        evidence_packs_dir
        if evidence_packs_dir is not None
        else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    )
    hitl_drafts_dir = (
        hitl_drafts_dir
        if hitl_drafts_dir is not None
        else config.TMP_DIR / "llm_source_reconcile_hitl_drafts"
    )
    summary_path = (
        summary_path
        if summary_path is not None
        else config.TMP_DIR / "llm_source_reconcile_summary.json"
    )
    legacy_vars: set[str] = set()
    new_vars: set[str] = set()
    if evidence_packs_dir.is_dir():
        for f in sorted(evidence_packs_dir.glob("*.json")):
            try:
                body = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(body, dict):
                continue
            # Distinguish shape: legacy has variable_id; new has form + variables[]
            if "form" in body and "variables" in body:
                for var in body.get("variables") or []:
                    if not isinstance(var, dict):
                        continue
                    vid = var.get("variable_id")
                    if vid:
                        new_vars.add(vid)
            elif "variable_id" in body:
                vid = body.get("variable_id") or f.stem
                legacy_vars.add(vid)
    legacy_only = sorted(legacy_vars - new_vars)
    new_only_count = len(new_vars - legacy_vars)
    matched_count = len(legacy_vars & new_vars)
    for vid in legacy_only:
        masked = mask_variable_id(_LEGACY_FORM_PLACEHOLDER, vid, key_path=key_path)
        out = hitl_drafts_dir / f"legacy_{masked}.md"
        _atomic_write_text(out, _hitl_body(masked))
    summary = {
        "schema_version": 1,
        "legacy_only_count": len(legacy_only),
        "new_only_count": new_only_count,
        "matched_count": matched_count,
    }
    atomic_write_json(summary_path, summary)
    logger.info(
        "legacy_evidence_pack_reconcile.complete legacy_only=%d new_only=%d matched=%d",
        summary["legacy_only_count"],
        summary["new_only_count"],
        summary["matched_count"],
    )
    return summary


if __name__ == "__main__":
    reconcile()
