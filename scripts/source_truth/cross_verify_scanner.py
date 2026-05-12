"""Phase 3 deterministic scanner — emits SAFE findings only.

Walks every SoT YAML and the corresponding ``dataset_schema/files/<form>.jsonl``.
Reads only the first line of each JSONL to get column keys, then iterates
remaining lines to count rows carrying the ``_phi_scrubbed`` marker.
NEVER writes raw values, sample values, value hashes, or value-derived
statistics. Counts and booleans only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)

_SCHEMA_VERSION = 1


def _read_column_keys(jsonl_path: Path) -> set[str]:
    """Read only line 1 of the JSONL to get the column-key set."""
    if not jsonl_path.is_file():
        return set()
    with open(jsonl_path, encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        return set()
    try:
        row = json.loads(first)
    except json.JSONDecodeError:
        return set()
    if not isinstance(row, dict):
        return set()
    return set(row.keys())


def _count_scrubbed_marker_rows(jsonl_path: Path) -> int:
    """Count rows with `_phi_scrubbed` marker. Reads marker presence only."""
    if not jsonl_path.is_file():
        return 0
    count = 0
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and "_phi_scrubbed" in row:
                count += 1
    return count


def _iter_sot_variables(policy: dict) -> list[tuple[str, str]]:
    variables = policy.get("variables") or []
    out: list[tuple[str, str]] = []
    if isinstance(variables, list):
        for v in variables:
            if not isinstance(v, dict):
                continue
            vid = v.get("variable_id")
            if not vid:
                continue
            handling = v.get("handling_intent") or {}
            action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
            out.append((str(vid), action))
    elif isinstance(variables, dict):
        for vid, var in variables.items():
            if not isinstance(var, dict):
                continue
            handling = var.get("handling_intent") or {}
            action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
            out.append((str(vid), action))
    return out


def scan(
    *,
    sot_dir: Path | None = None,
    dataset_files_dir: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    sot_dir = sot_dir if sot_dir is not None else config.SOT_DIR
    dataset_files_dir = (
        dataset_files_dir
        if dataset_files_dir is not None
        else config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    )
    output_path = output_path if output_path is not None else config.CROSS_VERIFY_SAFE_REPORT_PATH
    policy_files = sorted(sot_dir.glob("*_policy.yaml"))
    dataset_policies = sot_dir / "dataset_policies"
    if dataset_policies.is_dir():
        policy_files.extend(sorted(dataset_policies.glob("*_policy.yaml")))
    findings: list[dict[str, Any]] = []
    discrepancy_count = 0
    forms_seen: set[str] = set()
    study: str | None = None
    for policy_path in policy_files:
        policy = yaml.safe_load(policy_path.read_text()) or {}
        if not isinstance(policy, dict):
            continue
        form = policy.get("form") or policy_path.stem.replace("_policy", "")
        forms_seen.add(form)
        if study is None:
            study = policy.get("study")
        jsonl_path = dataset_files_dir / f"{form}.jsonl"
        column_keys = _read_column_keys(jsonl_path)
        scrubbed_count = _count_scrubbed_marker_rows(jsonl_path)
        for vid, action in _iter_sot_variables(policy):
            column_present = vid in column_keys
            findings.append(
                {
                    "form": form,
                    "variable_id": vid,
                    "column_present": column_present,
                    "scrubbed_count": scrubbed_count if column_present else 0,
                    "sot_action": action,
                }
            )
            # Discrepancy classification:
            # - drop + column present
            # - keep/cap/generalize/suppress + column absent
            if (action == "drop" and column_present) or (
                action in {"keep", "cap", "generalize", "suppress_small_cell"}
                and not column_present
            ):
                discrepancy_count += 1
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "study": study or "unknown",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {
            "forms": len(forms_seen),
            "variables_scanned": len(findings),
            "discrepancies": discrepancy_count,
        },
        "findings": findings,
    }
    atomic_write_json(output_path, payload)
    logger.info(
        "cross_verify_scan.complete forms=%d vars=%d discrepancies=%d output=%s",
        len(forms_seen),
        len(findings),
        discrepancy_count,
        str(output_path),
    )
    return payload


if __name__ == "__main__":
    scan()
