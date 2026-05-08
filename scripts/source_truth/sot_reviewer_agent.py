"""Per-form SoT reviewer agent harness.

Reads the same inputs as the extractor PLUS the extractor's draft, then
issues a verdict (agree / disagree_minor / disagree_major) with notes.
Same column-keys-only contract as the extractor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.source_truth.sot_extractor_agent import gather_inputs
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


_PROMPT_TEMPLATE = """\
You are the SoT reviewer for the form `{form}`.

Read the source inputs and the extractor's draft below. Produce a verdict
(`agree`, `disagree_minor`, or `disagree_major`) and notes pointing at
specific variables that need correction. You must not invent variables
and you must not reference dataset row values (none are provided).

Source inputs:
- existing_yaml:
{existing_yaml}

- pdf_text:
{pdf_text}

- dataset_columns:
{dataset_columns}

- pilot_artifact:
{pilot_artifact}

Extractor draft (YAML):
{draft_yaml}

Extractor draft (evidence pack JSON):
{draft_pack}

Output JSON shape:
{{
  "verdict": "agree" | "disagree_minor" | "disagree_major",
  "notes": "<markdown explaining specific variables and reasons>"
}}
"""


def invoke_reviewer_subagent(prompt: str) -> dict[str, str]:
    """Real implementation calls the Claude Agent SDK with the reviewer
    role. Tests monkeypatch this. Task 0-8 wires the SDK call."""
    raise NotImplementedError("Wire to Claude Agent SDK in Task 0-8")


def run_reviewer(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    draft_yaml_path: Path,
    draft_pack_path: Path,
    reviews_dir: Path,
) -> dict[str, Any]:
    sources = gather_inputs(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
    )
    prompt = _PROMPT_TEMPLATE.format(
        form=form,
        existing_yaml=sources["existing_yaml"] or "(none)",
        pdf_text=sources["pdf_text"] or "(none)",
        dataset_columns="\n".join(sources["dataset_columns"]) or "(none)",
        pilot_artifact=sources["pilot_artifact"] or "(none)",
        draft_yaml=draft_yaml_path.read_text(encoding="utf-8"),
        draft_pack=draft_pack_path.read_text(encoding="utf-8"),
    )
    out = invoke_reviewer_subagent(prompt)

    reviews_dir.mkdir(parents=True, exist_ok=True)
    review_md = reviews_dir / f"{form}_review.md"
    review_md.write_text(
        f"# Review for {form}\n\n"
        f"verdict: {out['verdict']}\n\n"
        f"## notes\n\n{out['notes']}\n",
        encoding="utf-8",
    )
    _LOG.info("sot_reviewer.review_written form=%s verdict=%s", form, out["verdict"])
    return {"form": form, "verdict": out["verdict"], "review_md": str(review_md)}
