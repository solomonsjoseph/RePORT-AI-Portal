"""Convert phi_sot_sweep findings into PR-draft + HITL-draft markdown files.

Phase 1 produces drafts only — no live gh invocation. The drafts include
masked variable_ids only and cite the regulatory anchor.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import config
from scripts.security.phi_scrub import PHIScrubError
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


class PHISweepEmitError(PHIScrubError):
    pass


def _slug(text: str | None) -> str:
    if not text:
        return "uncategorized"
    s = _SLUG_RE.sub("-", text).strip("-").lower()
    return s or "uncategorized"


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


def _render_pr_body(anchor: str, items: list[dict[str, Any]]) -> str:
    lines = [
        f"# PHI Rule Addition: {anchor}",
        "",
        "**Status:** DRAFT (Phase 1 emitter, not yet filed)",
        f"**Anchor:** {anchor}",
        f"**Affected variables:** {len(items)}",
        "",
        "## Variables (masked)",
        "",
        "| Form | Variable (masked) | Current action |",
        "|---|---|---|",
    ]
    for it in sorted(items, key=lambda x: (x["form"], x["variable_id_masked"])):
        lines.append(f"| {it['form']} | `{it['variable_id_masked']}` | {it['current_action']} |")
    lines.extend(
        [
            "",
            "## Proposed change",
            "",
            "Add a rule to `scripts/security/phi_scrub.yaml` (or a regex pattern to",
            "`scripts/security/phi_patterns.py`) that covers the variables above.",
            "Cite the anchor in the rule comment.",
            "",
            "## Citations",
            "",
            f"- Regulatory anchor: {anchor}",
            "- Inventoried technique to extend: see "
            "`docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`",
            "",
        ]
    )
    return "\n".join(lines)


def _render_hitl_body(item: dict[str, Any]) -> str:
    lines = [
        f"# HITL: review_required for {item['form']} / `{item['variable_id_masked']}`",
        "",
        "**Status:** DRAFT (Phase 1 emitter, not yet filed)",
        f"**Form:** {item['form']}",
        f"**Variable (masked):** `{item['variable_id_masked']}`",
        f"**Current action:** `{item['current_action']}`",
        "",
        "## Decision needed",
        "",
        "The SoT for this variable carries `handling_intent.action: review_required`.",
        "A human owner must choose one of:",
        "- `keep` (allowlist; cite the anchor that permits)",
        "- `drop`",
        "- `pseudonymize`",
        "- `jitter_date`",
        "- `cap`",
        "- `generalize`",
        "- `suppress_small_cell`",
        "",
        "The masked variable_id above is opaque; recovery requires the HMAC key.",
        "",
        "## Labels",
        "",
        "`HITL`, `phi-audit`, `phase-1`",
        "",
    ]
    return "\n".join(lines)


def emit_drafts(
    *,
    findings_path: Path | None = None,
    pr_drafts_dir: Path | None = None,
    hitl_drafts_dir: Path | None = None,
) -> None:
    findings_path = findings_path if findings_path is not None else config.PHI_SWEEP_FINDINGS_PATH
    pr_drafts_dir = pr_drafts_dir if pr_drafts_dir is not None else config.PHI_SWEEP_PR_DRAFTS_DIR
    hitl_drafts_dir = hitl_drafts_dir if hitl_drafts_dir is not None else config.PHI_SWEEP_HITL_DRAFTS_DIR
    if not findings_path.is_file():
        raise PHISweepEmitError(f"findings file missing: {findings_path}")
    payload = json.loads(findings_path.read_text())
    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in payload["findings"]:
        if f["category"] in {"name_phi_uncovered", "column_shape_phi_uncovered"}:
            by_anchor[f.get("regulatory_anchor_hint") or "uncategorized"].append(f)
    for anchor, items in sorted(by_anchor.items()):
        out = pr_drafts_dir / f"{_slug(anchor)}.md"
        _atomic_write_text(out, _render_pr_body(anchor, items))
    for f in payload["findings"]:
        if f["category"] != "review_required_open":
            continue
        out = hitl_drafts_dir / f"{f['form']}_{f['variable_id_masked']}.md"
        _atomic_write_text(out, _render_hitl_body(f))
    logger.info(
        "phi_sweep_emit.complete pr_drafts=%d hitl_drafts=%d pr_dir=%s hitl_dir=%s",
        len(by_anchor),
        sum(1 for f in payload["findings"] if f["category"] == "review_required_open"),
        str(pr_drafts_dir),
        str(hitl_drafts_dir),
    )


if __name__ == "__main__":
    emit_drafts()
