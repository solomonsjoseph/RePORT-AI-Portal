"""Phase 3 cross-verify fix agent.

LLM call is injected. Production passes the Anthropic SDK; tests pass a
mock. ``llm_call=None`` runs scanner-only mode (no fix proposals).

Read-deny is applied to ``deny_paths`` for the duration of the run and
restored on exit (best-effort; the OS guarantee in production comes from
container bind mounts, not chmod).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.utils.logging_system import get_logger
from scripts.utils.read_deny import enforce_read_deny, restore_read_access

logger = get_logger(__name__)


def _load_ledger(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save_ledger(path: Path, ledger: dict[str, int]) -> None:
    atomic_write_json(path, ledger)


def _exists_in_sot(sot_dir: Path, form: str, variable_id: str) -> bool:
    yaml_path = sot_dir / f"{form}_policy.yaml"
    if not yaml_path.is_file():
        ds_dir = sot_dir / "dataset_policies"
        if ds_dir.is_dir():
            yaml_path = ds_dir / f"{form}_policy.yaml"
    if not yaml_path.is_file():
        return False
    body = yaml.safe_load(yaml_path.read_text()) or {}
    variables = body.get("variables") or []
    if isinstance(variables, list):
        return any(
            v.get("variable_id") == variable_id
            for v in variables
            if isinstance(v, dict)
        )
    if isinstance(variables, dict):
        return variable_id in variables
    return False


def _exists_in_evidence_pack(ep_dir: Path, form: str, variable_id: str) -> bool:
    pack = ep_dir / f"{form}.json"
    if not pack.is_file():
        return False
    body = json.loads(pack.read_text())
    for var in body.get("variables") or []:
        if isinstance(var, dict) and var.get("variable_id") == variable_id:
            return True
    return False


def _ledger_key(form: str, variable_id: str) -> str:
    return f"{form}:{variable_id}"


def _write_pr_draft(
    out_dir: Path, form: str, variable_id: str, fix: dict[str, Any]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_dir / f"{form}_{variable_id}.json", fix)


def _write_hitl_draft(out_dir: Path, form: str, variable_id: str, body: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{form}_{variable_id}.md"
    out.write_text(body, encoding="utf-8")


def run_fix_agent(
    *,
    safe_report_path: Path,
    sot_dir: Path,
    evidence_packs_dir: Path,
    phi_scrub_yaml: Path,
    deny_paths: list[Path],
    llm_call: Callable[[str], str] | None,
    repeat_ledger_path: Path,
    output_pr_drafts_dir: Path,
    output_hitl_drafts_dir: Path,
) -> dict[str, Any]:
    """Run the fix agent loop. Returns summary dict.

    {
      "proposed_fixes": int,
      "rejected_no_invent": int,
      "auto_fix_exhausted": int,
      "scanner_only": bool,
    }
    """
    safe = json.loads(safe_report_path.read_text())
    findings = safe.get("findings") or []
    proposed = 0
    rejected_no_invent = 0
    auto_fix_exhausted = 0
    if deny_paths:
        enforce_read_deny(deny_paths)
    try:
        if llm_call is None:
            return {
                "proposed_fixes": 0,
                "rejected_no_invent": 0,
                "auto_fix_exhausted": 0,
                "scanner_only": True,
            }
        ledger = _load_ledger(repeat_ledger_path)
        for f in findings:
            form = f["form"]
            vid = f["variable_id"]
            cp = f["column_present"]
            action = f["sot_action"]
            # Trivial skips
            if cp and action == "keep":
                continue
            if not cp and action != "drop":
                continue
            ledger_key = _ledger_key(form, vid)
            count = ledger.get(ledger_key, 0)
            if count >= config.CROSS_VERIFY_REPEAT_THRESHOLD:
                auto_fix_exhausted += 1
                _write_hitl_draft(
                    output_hitl_drafts_dir,
                    form,
                    vid,
                    f"# HITL: auto_fix_exhausted for {form}/{vid}\n\n"
                    f"Auto-fix retried {count}x without resolution. "
                    f"Marking auto_fix_exhausted=true.\n",
                )
                continue
            prompt = json.dumps(
                {
                    "finding": f,
                    "phi_scrub_yaml_path": str(phi_scrub_yaml),
                    "evidence_pack_path": str(evidence_packs_dir / f"{form}.json"),
                    "sot_yaml_path": str(sot_dir / f"{form}_policy.yaml"),
                }
            )
            try:
                resp = llm_call(prompt)
                fix = json.loads(resp)
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning(
                    "fix_agent.llm_failed form=%s vid=%s err=%s", form, vid, exc
                )
                continue
            kind = fix.get("kind")
            if kind == "rule_add":
                target_vid = fix.get("variable_id") or vid
                if not (
                    _exists_in_sot(sot_dir, form, target_vid)
                    or _exists_in_evidence_pack(evidence_packs_dir, form, target_vid)
                ):
                    rejected_no_invent += 1
                    continue
            elif kind == "sot_stub_add":
                if not _exists_in_evidence_pack(evidence_packs_dir, form, vid):
                    rejected_no_invent += 1
                    continue
                # Phase-0 invariant: SoT YAML must exist.
                yaml_path = sot_dir / f"{form}_policy.yaml"
                if not yaml_path.is_file():
                    rejected_no_invent += 1
                    continue
                fix.setdefault("variable_record", {})["claude_drafted"] = True
            elif kind == "hitl":
                _write_hitl_draft(
                    output_hitl_drafts_dir,
                    form,
                    vid,
                    f"# HITL: {fix.get('reason') or 'unspecified'}\n",
                )
                continue
            else:
                continue
            _write_pr_draft(output_pr_drafts_dir, form, vid, fix)
            proposed += 1
            ledger[ledger_key] = count + 1
        _save_ledger(repeat_ledger_path, ledger)
    finally:
        if deny_paths:
            restore_read_access(deny_paths)
    summary = {
        "proposed_fixes": proposed,
        "rejected_no_invent": rejected_no_invent,
        "auto_fix_exhausted": auto_fix_exhausted,
        "scanner_only": False,
    }
    logger.info("fix_agent.complete %s", summary)
    return summary
