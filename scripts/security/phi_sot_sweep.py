"""Deterministic SoT-driven PHI sweep.

Walks every SoT YAML under ``data/SoT/<study>/`` and classifies each
``(form, variable_id)`` into one of four categories:

* ``covered`` — declared action is in the inventoried PHI-handling set.
* ``name_phi_uncovered`` — variable name matches a PHI-shape regex but
  declared action is not a PHI handler.
* ``column_shape_phi_uncovered`` — reserved for Phase 2 (no source yet).
* ``review_required_open`` — declared action is ``review_required``.

Findings are written to ``config.PHI_SWEEP_FINDINGS_PATH``. Variable
ids are masked through ``phi_id_masker.mask_variable_id`` so the JSON
file is safe to attach to LLM-visible artifacts.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.security.phi_id_masker import mask_variable_id
from scripts.security.phi_scrub import PHIScrubError
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_HANDLING_ACTIONS_COVERED: frozenset[str] = frozenset(
    {
        "keep",
        "drop",
        "pseudonymize",
        "jitter_date",
        "cap",
        "generalize",
        "suppress_small_cell",
    }
)


_NAME_PHI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HIPAA #18 (any unique id)", re.compile(r"(?i)(_NAME|^NAME$|FNAME|LNAME)")),
    ("HIPAA #5 (phone)", re.compile(r"(?i)(PHONE|MOBILE|TEL)")),
    ("HIPAA #2 (geographic)", re.compile(r"(?i)(_ADDR|ADDRESS|^CITY$|PINCODE|ZIP)")),
    ("Aadhaar Act §29 (Aadhaar #)", re.compile(r"(?i)AADHAAR")),
    ("SPDI Rule 3 (financial)", re.compile(r"(?i)(PAN_NO|PANNUM|VOTER_ID)")),
    ("HIPAA #4 (email)", re.compile(r"(?i)EMAIL")),
    ("HIPAA #3 (DOB)", re.compile(r"(?i)(BIRTHDAT|^DOB$|BRTHDAT)")),
]


class PHISweepError(PHIScrubError):
    """Raised when the sweep cannot complete (malformed YAML, etc.)."""


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _iter_policy_files(sot_dir: Path) -> list[Path]:
    files = sorted(sot_dir.glob("*_policy.yaml"))
    dataset_dir = sot_dir / "dataset_policies"
    if dataset_dir.is_dir():
        files.extend(sorted(dataset_dir.glob("*_policy.yaml")))
    return files


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise PHISweepError(f"malformed YAML at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PHISweepError(f"policy {path} must be a mapping")
    return data


def _classify_variable(
    form: str,
    variable_id: str,
    action: str,
) -> tuple[str, str | None]:
    if action == "review_required":
        return ("review_required_open", None)
    for anchor_hint, pat in _NAME_PHI_PATTERNS:
        if pat.search(variable_id):
            if action not in _HANDLING_ACTIONS_COVERED - {"keep"}:
                return ("name_phi_uncovered", anchor_hint)
    if action in _HANDLING_ACTIONS_COVERED:
        return ("covered", None)
    return ("name_phi_uncovered", "uncategorized")


def run_sweep(
    *,
    sot_dir: Path | None = None,
    output_path: Path | None = None,
    key_path: Path | None = None,
) -> dict[str, Any]:
    sot_dir = sot_dir if sot_dir is not None else config.SOT_DIR
    output_path = output_path if output_path is not None else config.PHI_SWEEP_FINDINGS_PATH
    findings: list[dict[str, Any]] = []
    counters = {
        "covered": 0,
        "name_phi_uncovered": 0,
        "column_shape_phi_uncovered": 0,
        "review_required_open": 0,
    }
    for policy_path in _iter_policy_files(sot_dir):
        policy = _load_policy(policy_path)
        form = policy.get("form") or policy_path.stem.replace("_policy", "")
        variables = policy.get("variables") or []
        if not isinstance(variables, list):
            raise PHISweepError(f"{policy_path}: variables must be a list")
        for var in variables:
            if not isinstance(var, dict):
                continue
            variable_id = var.get("variable_id")
            handling = var.get("handling_intent") or {}
            action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
            if not variable_id:
                continue
            category, anchor = _classify_variable(form, variable_id, action)
            counters[category] += 1
            findings.append(
                {
                    "form": form,
                    "variable_id_masked": mask_variable_id(form, variable_id, key_path=key_path),
                    "category": category,
                    "regulatory_anchor_hint": anchor,
                    "current_action": action,
                }
            )
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {"total_variables": sum(counters.values()), **counters},
        "findings": findings,
    }
    _atomic_write_json(output_path, payload)
    logger.info(
        "phi_sot_sweep.complete summary=%s output=%s",
        payload["summary"],
        str(output_path),
    )
    return payload


if __name__ == "__main__":
    run_sweep()
