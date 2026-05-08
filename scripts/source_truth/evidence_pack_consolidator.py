"""SoT-driven per-form evidence pack consolidator.

Replaces 947 per-variable evidence packs with M per-form packs (one per
SoT YAML). PHI variable_ids in ``name_phi_uncovered`` or
``review_required_open`` categories are masked through Phase 1's
``mask_variable_id`` helper. Covered variables stay clear.

Output: ``output/<study>/llm_source/evidence_packs/<form>.json``.
No row values. Atomic temp+replace via ``atomic_write_json``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.security.phi_id_masker import mask_variable_id
from scripts.security.phi_scrub import PHIScrubError
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_SCHEMA_VERSION = 1


_NAME_PHI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HIPAA #18 (any unique id)", re.compile(r"(?i)(_NAME|^NAME$|FNAME|LNAME)")),
    ("HIPAA #5 (phone)", re.compile(r"(?i)(PHONE|MOBILE|TEL)")),
    ("HIPAA #2 (geographic)", re.compile(r"(?i)(_ADDR|ADDRESS|^CITY$|PINCODE|ZIP)")),
    ("Aadhaar Act §29 (Aadhaar #)", re.compile(r"(?i)AADHAAR")),
    ("SPDI Rule 3 (financial)", re.compile(r"(?i)(PAN_NO|PANNUM|VOTER_ID)")),
    ("HIPAA #4 (email)", re.compile(r"(?i)EMAIL")),
    ("HIPAA #3 (DOB)", re.compile(r"(?i)(BIRTHDAT|^DOB$|BRTHDAT)")),
]


_HANDLING_COVERED: frozenset[str] = frozenset(
    {"keep", "drop", "pseudonymize", "jitter_date", "cap", "generalize", "suppress_small_cell"}
)


class EvidencePackError(PHIScrubError):
    pass


def _is_uncovered_or_review(variable_id: str, action: str) -> tuple[bool, str | None]:
    if action == "review_required":
        return True, None
    for anchor_hint, pat in _NAME_PHI_PATTERNS:
        if pat.search(variable_id) and action not in _HANDLING_COVERED - {"keep"}:
            return True, anchor_hint
    return False, None


def _iter_variables(variables: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(variables, list):
        out = []
        for v in variables:
            if not isinstance(v, dict):
                continue
            vid = v.get("variable_id")
            if vid:
                out.append((str(vid), v))
        return out
    if isinstance(variables, dict):
        return [(str(k), v) for k, v in variables.items() if isinstance(v, dict)]
    raise EvidencePackError(f"variables must be list or dict, got {type(variables).__name__}")


def build_evidence_packs(
    *,
    sot_dir: Path | None = None,
    output_dir: Path | None = None,
    key_path: Path | None = None,
) -> int:
    """Build per-form evidence packs from SoT YAMLs. Returns form count."""

    sot_dir = sot_dir if sot_dir is not None else config.SOT_DIR
    output_dir = output_dir if output_dir is not None else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    files = sorted(sot_dir.glob("*_policy.yaml"))
    dataset_dir = sot_dir / "dataset_policies"
    if dataset_dir.is_dir():
        files.extend(sorted(dataset_dir.glob("*_policy.yaml")))
    count = 0
    for policy_path in files:
        try:
            policy = yaml.safe_load(policy_path.read_text())
        except yaml.YAMLError as exc:
            raise EvidencePackError(f"malformed YAML at {policy_path}: {exc}") from exc
        if not isinstance(policy, dict):
            continue
        form = policy.get("form") or policy_path.stem.replace("_policy", "")
        variables_payload: list[dict[str, Any]] = []
        for vid, var in _iter_variables(policy.get("variables") or []):
            handling = var.get("handling_intent") or {}
            action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
            should_mask, anchor = _is_uncovered_or_review(vid, action)
            display_id = mask_variable_id(form, vid, key_path=key_path) if should_mask else vid
            entry = {
                "variable_id": display_id,
                "id_masked": should_mask,
                "handling_action": action,
                "regulatory_anchor_hint": anchor,
                "description": var.get("description"),
                "options": var.get("options") or var.get("option_set"),
            }
            variables_payload.append(entry)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "study": policy.get("study") or "unknown",
            "form": form,
            "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "variables": variables_payload,
        }
        atomic_write_json(output_dir / f"{form}.json", payload)
        count += 1
    logger.info("evidence_packs.built forms=%d output=%s", count, str(output_dir))
    return count


if __name__ == "__main__":
    build_evidence_packs()
