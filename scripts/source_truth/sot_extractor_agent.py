"""Per-form SoT extractor agent harness.

Gathers read-only inputs (PDF text, dataset column keys, pilot artifact,
existing YAML) and dispatches a Claude Agent SDK subagent to draft a
complete SoT YAML + per-form evidence pack. The harness never loads
dataset row values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.source_truth.sot_gap_inventory import _read_column_keys_only
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def _read_pdf_text(pdf_path: Path) -> str:
    """Placeholder for the project's PDF-text utility. For Phase 0 fixtures
    and stub PDFs, return a marker. Production wiring (Task 0-8 / Phase 1+)
    replaces this with the real extractor."""
    if not pdf_path.is_file():
        return ""
    return f"<pdf:{pdf_path.name}>"


def _read_pilot_artifact(pilot_dir: Path, form: str) -> str:
    folder = pilot_dir / f"policy_pilot_{form}"
    candidates = sorted(folder.glob("*.yaml")) if folder.is_dir() else []
    if not candidates:
        return ""
    if len(candidates) > 1:
        _LOG.warning(
            "sot_extractor.pilot_multiple_artifacts form=%s count=%d picked=%s",
            form, len(candidates), candidates[0].name,
        )
    return candidates[0].read_text(encoding="utf-8")


def _read_existing_yaml(sot_dir: Path, form: str) -> str:
    p = sot_dir / f"{form}_policy.yaml"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def gather_inputs(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
) -> dict[str, Any]:
    return {
        "form": form,
        "pdf_text": _read_pdf_text(raw_pdf_dir / f"{form}.pdf"),
        "dataset_columns": _read_column_keys_only(dataset_dir / f"{form}.jsonl")
        if (dataset_dir / f"{form}.jsonl").is_file()
        else [],
        "pilot_artifact": _read_pilot_artifact(pilot_dir, form),
        "existing_yaml": _read_existing_yaml(sot_dir, form),
    }


_PROMPT_TEMPLATE = """\
You are the SoT extractor for the form `{form}` of the RePORT-AI-Portal study.

Goal: produce a COMPLETE Source-of-Truth policy YAML and a per-form
evidence pack JSON for this form, sourced ONLY from the inputs below.
You must not invent variables. You must not reference dataset row values
(none are provided). Stick strictly to dataset column keys, PDF text,
and pilot artifact text.

Inputs:
- existing_yaml:
{existing_yaml}

- pdf_text:
{pdf_text}

- dataset_columns:
{dataset_columns}

- pilot_artifact:
{pilot_artifact}

Output JSON shape:
{{
  "yaml": "<full SoT YAML text>",
  "evidence_pack": "<JSON: {{form, variables: [{{variable_id, description, options, codings}}]}}>"
}}

Schema constraints:
- Every variable in dataset_columns MUST appear in `variables[].variable_id`.
- Every variable MUST carry a non-empty description.
- Every variable MUST carry a non-empty `handling_intent.action` (one of:
  drop, pseudonymize, jitter_date, cap, generalize, suppress_small_cell,
  keep). When uncertain, default to `keep` and add a `claude_drafted: true`
  flag on the variable.
"""


def invoke_subagent(prompt: str) -> dict[str, str]:
    """Real implementation calls the Claude Agent SDK with a constrained
    role. Tests monkeypatch this function. Task 0-8 wires the SDK call."""
    raise NotImplementedError("Wire to Claude Agent SDK in Task 0-8")


def run_extractor(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
) -> dict[str, str]:
    inputs = gather_inputs(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
    )
    prompt = _PROMPT_TEMPLATE.format(
        form=form,
        existing_yaml=inputs["existing_yaml"] or "(none)",
        pdf_text=inputs["pdf_text"] or "(none)",
        dataset_columns="\n".join(inputs["dataset_columns"]) or "(none)",
        pilot_artifact=inputs["pilot_artifact"] or "(none)",
    )
    out = invoke_subagent(prompt)

    drafts_dir.mkdir(parents=True, exist_ok=True)
    evidence_pack_drafts_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = drafts_dir / f"{form}_policy.yaml.draft"
    pack_path = evidence_pack_drafts_dir / f"{form}.json"
    yaml_path.write_text(out["yaml"], encoding="utf-8")
    pack_path.write_text(out["evidence_pack"], encoding="utf-8")

    _LOG.info("sot_extractor.draft_written form=%s", form)
    return {
        "form": form,
        "yaml_path": str(yaml_path),
        "evidence_pack_path": str(pack_path),
    }
