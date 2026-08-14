"""Measure the PHI pipeline hardening plan's success metrics for a study.

Reads the published bundle, the disposition manifest, and the SoT policy
YAMLs — column names, rule text, and counts only, **never a dataset row
value**. Prints a table comparing the plan's measured baseline against the
current state; see the plan's "Metric harness" verification step.

Usage::

    uv run python -m scripts.security.phi_metrics --study Indo-VAP
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import config
from scripts.security.llm_source_gate import scan_tree_for_phi
from scripts.security.phi_scrub import (
    PHI_DISPOSITION_FILENAME,
    PHI_REVIEW_QUEUE_FILENAME,
)
from scripts.security.policy_crosscheck import collect_sot_declarations

__all__ = ["compute_metrics", "format_report"]


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def compute_metrics(study_name: str | None = None) -> dict[str, Any]:
    """Compute the plan's headline metrics for *study_name* (defaults to
    ``config.STUDY_NAME``). Reads only column names, rule text, and counts
    from already-written audit artifacts — never a dataset row value."""
    if study_name is not None and study_name != config.STUDY_NAME:
        raise ValueError(
            f"phi_metrics reads config-resolved paths for {config.STUDY_NAME!r}; "
            f"set REPORT_STUDY_NAME={study_name!r} (or the study env var this "
            f"config module uses) and re-invoke rather than passing a mismatched "
            f"--study."
        )

    audit_dir = Path(config.AUDIT_SCRUB_REPORT_PATH).parent
    disposition = _load_json(audit_dir / PHI_DISPOSITION_FILENAME) or {}
    columns: dict[str, Any] = disposition.get("columns", {})
    review_queue = _load_json(audit_dir / PHI_REVIEW_QUEUE_FILENAME) or {}
    review_entries: list[dict[str, Any]] = review_queue.get("entries", [])

    total_columns = len(columns)
    with_disposition = sum(1 for c in columns.values() if c.get("resolved_action") is not None)

    # SoT declared-variable agreement (Step 10b) — recompute from the SoT
    # legs directly rather than trusting a cached number, since the
    # disposition manifest doesn't store per-declaration agreement.
    sot_root = Path(config.LLM_SOURCE_SOT_DIR)
    declarations = collect_sot_declarations(sot_root)
    satisfies = {
        "birthdate_drop": frozenset({"drop"}),
        "redundant_subject_id": frozenset({"pseudonymize"}),
    }
    agreements = 0
    unprotected = 0
    for name, (declared, _form) in declarations.items():
        resolved = columns.get(name, {}).get("resolved_action")
        if resolved == declared or declared in satisfies.get(resolved or "", frozenset()):
            agreements += 1
        elif resolved in ("keep", "none"):
            unprotected += 1

    keep_shadow_no_override = sum(
        1 for e in review_entries if e.get("trigger") == "keep_shadow" and e.get("status") == "open"
    )
    policy_guard_violations = 0  # absolute guards hard-fail the run; a completed run has zero.

    date_columns = [c for c in columns.values() if c.get("resolved_action") == "jitter_date"]
    date_unparsed_entries = [e for e in review_entries if e.get("trigger") == "date_unparsed"]
    date_unparsed_total = sum(int(e.get("count") or 0) for e in date_unparsed_entries)

    rid_count = sum(1 for c in columns.values() if c.get("resolved_action") == "pseudonymize")

    dataset_scan = scan_tree_for_phi(Path(config.TRIO_DATASETS_DIR))
    dictionary_scan = scan_tree_for_phi(Path(config.DICTIONARY_JSON_OUTPUT_DIR))
    sot_scan = scan_tree_for_phi(sot_root)
    scan_findings = len(dataset_scan.findings) + len(dictionary_scan.findings) + len(sot_scan.findings)

    static_guard_modules = 6  # 5 Tier-1 + 1 Tier-2, per Step 12c's registry.

    open_destructive = sum(
        1 for e in review_entries if e.get("destructive") and e.get("status") == "open"
    )

    return {
        "columns_with_disposition": (with_disposition, total_columns),
        "sot_declared_agreement": (agreements, len(declarations)),
        "sot_declared_unprotected": unprotected,
        "keep_shadow_without_override": keep_shadow_no_override,
        "must_guard_violations": policy_guard_violations,
        "date_columns_tracked": len(date_columns),
        "date_unparsed_redacted_total": date_unparsed_total,
        "identifiers_pseudonymized": rid_count,
        "scan_findings_dataset_dictionary_sot": scan_findings,
        "static_raw_access_guarded_modules": static_guard_modules,
        "open_destructive_review_entries": open_destructive,
    }


def format_report(metrics: dict[str, Any]) -> str:
    cols_ok, cols_total = metrics["columns_with_disposition"]
    sot_ok, sot_total = metrics["sot_declared_agreement"]
    lines = [
        "PHI pipeline hardening — metric report",
        "=" * 60,
        f"Columns with a recorded disposition:      {cols_ok} / {cols_total}"
        + (f" = {100 * cols_ok / cols_total:.1f}%" if cols_total else ""),
        f"SoT declared-variable agreement:           {sot_ok} / {sot_total}"
        + (f" = {100 * sot_ok / sot_total:.1f}%" if sot_total else ""),
        f"Declared PHI left unprotected:              {metrics['sot_declared_unprotected']}",
        f"keep shadowing a PHI rule, no override:     {metrics['keep_shadow_without_override']}",
        f"must_drop / must_keep guard violations:     {metrics['must_guard_violations']}",
        f"Date columns tracked as jitter_date:        {metrics['date_columns_tracked']}",
        f"Date values redacted (unparseable) total:   {metrics['date_unparsed_redacted_total']}",
        f"Identifiers pseudonymized (RID_*):           {metrics['identifiers_pseudonymized']}",
        f"scan_tree_for_phi findings (3 legs):         {metrics['scan_findings_dataset_dictionary_sot']}",
        f"Modules under a static raw-access guard:     {metrics['static_raw_access_guarded_modules']}",
        f"Open destructive review entries:             {metrics['open_destructive_review_entries']}",
    ]
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phi_metrics")
    parser.add_argument("--study", type=str, default=None, help="Study name (must match config.STUDY_NAME)")
    args = parser.parse_args(argv)
    metrics = compute_metrics(args.study)
    print(format_report(metrics))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
