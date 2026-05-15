"""Per-form SoT reviewer agent harness.

Reads the same inputs as the extractor PLUS the extractor's draft, then
issues a verdict (agree / disagree_minor / disagree_major) with notes.
Same column-keys-only contract as the extractor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from anthropic import Anthropic

import config
from scripts.source_truth.sot_extractor_agent import gather_inputs
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)

_VALID_VERDICTS: frozenset[str] = frozenset({"agree", "disagree_minor", "disagree_major"})

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
    """Call the Claude Agent SDK with the reviewer role.

    Returns a JSON-decoded dict with keys 'verdict' and 'notes'.
    Tests monkeypatch this function.
    """
    client = Anthropic()
    msg = client.messages.create(
        model=config.AGENT_MODEL_ID,
        max_tokens=4096,
        system=(
            "You are a clinical-data SoT reviewer. Output strict JSON "
            "with keys 'verdict' (one of 'agree', 'disagree_minor', "
            "'disagree_major') and 'notes' (string). No prose outside the JSON."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    if not msg.content or not getattr(msg.content[0], "text", None):
        raise ValueError(
            "Anthropic SDK returned no text block "
            f"(stop_reason={getattr(msg, 'stop_reason', 'unknown')!r})"
        )
    text = msg.content[0].text  # type: ignore[union-attr]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Anthropic SDK returned non-object JSON")
    return {str(key): str(value) for key, value in payload.items()}


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
    try:
        out = invoke_reviewer_subagent(prompt)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reviewer agent returned non-JSON for form {form!r}") from exc

    verdict = out.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise ValueError(
            f"Unexpected verdict {verdict!r} for form {form!r}. "
            f"Expected one of {sorted(_VALID_VERDICTS)}."
        )

    reviews_dir.mkdir(parents=True, exist_ok=True)
    review_md = reviews_dir / f"{form}_review.md"
    review_md.write_text(
        f"# Review for {form}\n\nverdict: {verdict}\n\n## notes\n\n{out['notes']}\n",
        encoding="utf-8",
    )
    _LOG.info("sot_reviewer.review_written form=%s verdict=%s", form, verdict)
    return {"form": form, "verdict": verdict, "review_md": str(review_md)}
