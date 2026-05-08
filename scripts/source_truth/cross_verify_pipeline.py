"""Phase 3 cross-verify pipeline orchestrator.

Order: scanner -> fix_agent (deny_paths enforced) -> emit.

Defaults pulled from config. Both ``llm_call`` and ``gh_runner`` are
optional; when None, the pipeline runs in scanner-only / no-gh mode.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import config
from scripts.source_truth.cross_verify_emit import emit
from scripts.source_truth.cross_verify_fix_agent import run_fix_agent
from scripts.source_truth.cross_verify_scanner import scan
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _default_phi_scrub_yaml() -> Path:
    return config.BASE_DIR / "scripts" / "security" / "phi_scrub.yaml"


def run(
    *,
    sot_dir: Path | None = None,
    dataset_files_dir: Path | None = None,
    evidence_packs_dir: Path | None = None,
    phi_scrub_yaml: Path | None = None,
    llm_call: Callable[[str], str] | None = None,
    gh_runner: Callable[[list[str], "str | None"], int] | None = None,
    key_path: Path | None = None,
) -> dict[str, Any]:
    """Run the Phase 3 cross-verify pipeline.

    Order: scanner -> fix_agent (with deny_paths) -> emit.

    Returns a summary dict with keys: scanner, fix_agent, emit.
    """
    sot_dir = sot_dir if sot_dir is not None else config.SOT_DIR
    dataset_files_dir = (
        dataset_files_dir if dataset_files_dir is not None else config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    )
    evidence_packs_dir = (
        evidence_packs_dir if evidence_packs_dir is not None else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    )
    phi_scrub_yaml = phi_scrub_yaml if phi_scrub_yaml is not None else _default_phi_scrub_yaml()

    scan_result = scan(
        sot_dir=sot_dir,
        dataset_files_dir=dataset_files_dir,
        output_path=config.CROSS_VERIFY_SAFE_REPORT_PATH,
    )

    deny_paths: list[Path] = []
    if config.TRIO_DATASETS_DIR.is_dir():
        deny_paths.append(config.TRIO_DATASETS_DIR)
    if config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR.is_dir():
        deny_paths.append(config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR)

    fix_summary = run_fix_agent(
        safe_report_path=config.CROSS_VERIFY_SAFE_REPORT_PATH,
        sot_dir=sot_dir,
        evidence_packs_dir=evidence_packs_dir,
        phi_scrub_yaml=phi_scrub_yaml,
        deny_paths=deny_paths,
        llm_call=llm_call,
        repeat_ledger_path=config.CROSS_VERIFY_REPEAT_LEDGER_PATH,
        output_pr_drafts_dir=config.CROSS_VERIFY_PR_DRAFTS_DIR,
        output_hitl_drafts_dir=config.CROSS_VERIFY_HITL_DRAFTS_DIR,
    )

    emit_summary = emit(
        pr_drafts_dir=config.CROSS_VERIFY_PR_DRAFTS_DIR,
        hitl_drafts_dir=config.CROSS_VERIFY_HITL_DRAFTS_DIR,
        evidence_packs_dir=evidence_packs_dir,
        gh_runner=gh_runner,
        key_path=key_path,
    )

    summary = {"scanner": scan_result, "fix_agent": fix_summary, "emit": emit_summary}
    logger.info(
        "cross_verify_pipeline.complete vars=%d discrepancies=%d proposed=%d emitted=%d",
        scan_result["summary"]["variables_scanned"],
        scan_result["summary"]["discrepancies"],
        fix_summary.get("proposed_fixes", 0),
        emit_summary.get("pr_emitted", 0) + emit_summary.get("issue_emitted", 0),
    )
    return summary


if __name__ == "__main__":
    run()
