"""Phase 3 PHI redactor — masks PHI variable_ids for LLM-visible artifacts.

Wraps Phase 1's ``mask_variable_id`` and consults the per-form evidence
pack to (a) determine whether the variable is PHI-classified, and (b)
fetch the human-readable description that replaces the variable_id in
artifacts.

Default-to-mask: any uncertainty (missing evidence pack, missing
variable, malformed JSON) returns a masked token.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from scripts.security.phi_id_masker import mask_variable_id
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _format_phi_token(token: str) -> str:
    return f"<phi:{token}>"


def _lookup(
    evidence_packs_dir: Path,
    form: str,
    variable_id: str,
    *,
    key_path: Path | None = None,
) -> tuple[bool, str]:
    """Return (id_masked, description). On any uncertainty, returns (True, "")."""

    pack_path = evidence_packs_dir / f"{form}.json"
    if not pack_path.is_file():
        return True, ""
    try:
        body = json.loads(pack_path.read_text())
    except json.JSONDecodeError:
        return True, ""
    if not isinstance(body, dict):
        return True, ""
    variables = body.get("variables") or []
    if not isinstance(variables, list):
        return True, ""
    masked_input = mask_variable_id(form, variable_id, key_path=key_path)
    for var in variables:
        if not isinstance(var, dict):
            continue
        ep_vid = var.get("variable_id")
        ep_masked = bool(var.get("id_masked"))
        # Match by cleartext when stored cleartext
        if not ep_masked and ep_vid == variable_id:
            return False, var.get("description") or ""
        # Match by re-masking when stored masked
        if ep_masked and ep_vid == masked_input:
            return True, var.get("description") or ""
    # Variable not present in pack — default-to-mask.
    return True, ""


def redact(
    form: str,
    variable_id: str,
    *,
    evidence_packs_dir: Path | None = None,
    sot_dir: Path | None = None,
    key_path: Path | None = None,
) -> tuple[str, str]:
    """Return ``(display_token, description)``.

    - For non-PHI variables: ``(variable_id, description)``.
    - For PHI-classified or unknown variables: ``(<phi:hash12>, description)``.
    """

    evidence_packs_dir = (
        evidence_packs_dir
        if evidence_packs_dir is not None
        else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    )
    if not evidence_packs_dir.is_dir():
        token = mask_variable_id(form, variable_id, key_path=key_path)
        return _format_phi_token(token), ""
    is_masked, description = _lookup(evidence_packs_dir, form, variable_id, key_path=key_path)
    if not is_masked:
        return variable_id, description
    token = mask_variable_id(form, variable_id, key_path=key_path)
    return _format_phi_token(token), description
