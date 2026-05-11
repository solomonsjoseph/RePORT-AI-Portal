"""Deterministic SoT-driven PHI sweep + Phase 1 draft emitter + verifier.

Three subcommands:

* ``sweep`` — walk SoT YAMLs, classify each ``(form, variable_id)``, write
  findings JSON. Categories: ``covered``, ``name_phi_uncovered``,
  ``column_shape_phi_uncovered``, ``review_required_open``.
* ``emit`` — convert findings into PR + HITL markdown drafts.
* ``verify`` — fail when any non-covered finding lacks a matching draft.

Findings are written to ``config.PHI_SWEEP_FINDINGS_PATH``. Variable ids
are masked through ``phi_id_masker.mask_variable_id`` so the JSON is safe
to attach to LLM-visible artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.security.phi_id_masker import mask_variable_id
from scripts.security.phi_scrub import PHIScrubError
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)

__all__ = [
    "PHISweepEmitError",
    "PHISweepError",
    "VerificationFailed",
    "emit_drafts",
    "main",
    "run_sweep",
    "verify",
]


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


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


class PHISweepError(PHIScrubError):
    """Raised when the sweep cannot complete (malformed YAML, etc.)."""


class PHISweepEmitError(PHIScrubError):
    """Raised when the emitter cannot read its findings input."""


class VerificationFailed(PHIScrubError):
    """Raised when the Phase 1 exit criterion is not met."""


def _slug(text: str | None) -> str:
    if not text:
        return "uncategorized"
    return _SLUG_RE.sub("-", text).strip("-").lower() or "uncategorized"


def _atomic_write(path: Path, body: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        mode = "wb" if isinstance(body, bytes) else "w"
        encoding = None if isinstance(body, bytes) else "utf-8"
        with open(fd, mode, encoding=encoding) as fh:
            fh.write(body)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True))


def _atomic_write_text(path: Path, body: str) -> None:
    _atomic_write(path, body)


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


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


def _iter_variables(
    variables: Any, policy_path: Path
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Normalize SoT variables to ``(variable_id, var_dict)`` pairs.

    Supports both shapes:

    * **List form** (used by fixtures): ``[{"variable_id": ..., ...}, ...]``
    * **Dict form** (used by production SoT YAMLs): ``{vid: {meta...}, ...}``
    """
    if isinstance(variables, list):
        for var in variables:
            if not isinstance(var, dict):
                continue
            vid = var.get("variable_id")
            if not vid:
                continue
            yield str(vid), var
    elif isinstance(variables, dict):
        for vid, var in variables.items():
            if not isinstance(var, dict):
                continue
            yield str(vid), var
    else:
        raise PHISweepError(
            f"{policy_path}: variables must be list or dict, got {type(variables).__name__}"
        )


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
        variables = policy.get("variables")
        if variables is None:
            continue
        for variable_id, var in _iter_variables(variables, policy_path):
            handling = var.get("handling_intent") or {}
            action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
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


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def verify(
    *,
    findings_path: Path | None = None,
    hitl_drafts_dir: Path | None = None,
    pr_drafts_dir: Path | None = None,
) -> None:
    findings_path = findings_path if findings_path is not None else config.PHI_SWEEP_FINDINGS_PATH
    hitl_drafts_dir = hitl_drafts_dir if hitl_drafts_dir is not None else config.PHI_SWEEP_HITL_DRAFTS_DIR
    pr_drafts_dir = pr_drafts_dir if pr_drafts_dir is not None else config.PHI_SWEEP_PR_DRAFTS_DIR
    payload = json.loads(findings_path.read_text())
    failures: list[str] = []
    for f in payload["findings"]:
        cat = f["category"]
        if cat == "covered":
            continue
        if cat == "review_required_open":
            expected = hitl_drafts_dir / f"{f['form']}_{f['variable_id_masked']}.md"
            if not expected.is_file():
                failures.append(f"missing HITL draft: {expected}")
            continue
        if cat in {"name_phi_uncovered", "column_shape_phi_uncovered"}:
            expected = pr_drafts_dir / f"{_slug(f.get('regulatory_anchor_hint'))}.md"
            if not expected.is_file():
                failures.append(f"missing PR draft for anchor {f.get('regulatory_anchor_hint')!r}: {expected}")
            continue
        failures.append(f"unknown category {cat!r} for {f['form']}/{f['variable_id_masked']}")
    if failures:
        raise VerificationFailed("Phase 1 exit criterion not met:\n  - " + "\n  - ".join(failures))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.security.phi_sot_sweep")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("sweep", help="run the SoT sweep (default)")
    sub.add_parser("emit", help="convert findings into PR + HITL markdown drafts")
    sub.add_parser("verify", help="fail when non-covered findings lack drafts")
    args = parser.parse_args(argv)
    cmd = args.cmd or "sweep"
    if cmd == "sweep":
        run_sweep()
        return 0
    if cmd == "emit":
        emit_drafts()
        return 0
    if cmd == "verify":
        try:
            verify()
        except VerificationFailed as exc:
            sys.stderr.write(str(exc) + "\n")
            return 1
        return 0
    parser.error(f"unknown subcommand: {cmd}")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    sys.exit(main())
