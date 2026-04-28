#!/usr/bin/env python3
"""Extract annotated study PDFs into structured variable JSON.

Reads annotated clinical research PDFs from ``data/raw/{STUDY}/annotated_pdfs/``
and produces per-form ``{stem}_variables.json`` files in
``output/{STUDY}/trio_bundle/pdfs/``.

This module is extraction-only: it sends PDFs to an LLM, parses the
returned JSON, and writes one per-form JSON file.

Supports **Anthropic Claude** and **Google Gemini** for PDF vision extraction.
Provider and API key are resolved from environment variables
(``LLM_PROVIDER``, ``ANTHROPIC_API_KEY`` / ``GOOGLE_API_KEY``, ``LLM_MODEL``).

Output JSON schema (per-form)::

    { "form_name", "source_pdf", "version", "summary",
      "variables": { "ABBREV": { "description", "values", "depends_on",
                                  "condition", "section_context" } },
      "sections": { "NAME": { "context", "variables": [...] } } }

Usage::

    >>> from scripts.extraction.extract_pdf_data import extract_pdfs_to_jsonl
    >>> result = extract_pdfs_to_jsonl()

    $ python -m scripts.extraction.extract_pdf_data
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, cast

import config
from scripts.extraction.dedup import clean_cross_form_duplicates, remove_within_file_duplicates
from scripts.extraction.io import (
    FILE_ENCODING,
    atomic_write_json,
)
from scripts.utils import logging_system as log

vlog = log.get_verbose_logger()

# --- Constants ---

PDF_PATTERN: str = "*.pdf"
MODULE_LOGGER: str = "scripts.extraction.extract_pdf_data"
JSON_VARIABLES_SUFFIX: str = "_variables.json"
NAMED_TEMP_PREFIX: str = config.TEMP_PREFIX_PDF  # PDF-specific prefix; cf. scripts.extraction.io.NAMED_TEMP_PREFIX which is the generic prefix used by file I/O helpers


# ---------------------------------------------------------------------------
# Inlined from former pdf_helpers.py (single-caller collapse)
# ---------------------------------------------------------------------------


def discover_variable_jsons(directory: Path) -> list[Path]:
    """Return sorted list of ``*_variables.json`` files, excluding junk files.

    Hidden files (``.*``) and Excel lock files (``~$*``) are skipped.
    Returns an empty list when *directory* does not exist, is not a
    directory, or contains no matching files.
    """
    if not directory.is_dir():
        return []

    return sorted(
        p
        for p in directory.glob(f"*{JSON_VARIABLES_SUFFIX}")
        if p.is_file() and not p.name.startswith(".") and not p.name.startswith("~")
    )


def load_variables_json(path: Path) -> dict[str, Any]:
    """Load and return a variables JSON file.

    Raises:
        json.JSONDecodeError: If the file is not valid JSON.
        OSError: If the file cannot be read.
    """
    with path.open(encoding=FILE_ENCODING) as fh:
        return json.load(fh)  # type: ignore[no-any-return]


RESULT_FILES_FOUND: str = "files_found"
RESULT_FILES_CREATED: str = "files_created"
RESULT_FILES_SKIPPED: str = "files_skipped"
RESULT_TOTAL_CHUNKS: str = "total_chunks"
RESULT_DUPLICATES_CLEANED: str = "duplicates_cleaned"
RESULT_ERRORS: str = "errors"
RESULT_PROCESSING_TIME: str = "processing_time"

INTER_PDF_DELAY: float = config.PDF_EXTRACTION_INTER_DELAY


# ---------------------------------------------------------------------------
# Extraction mode selector (Phase 3 PR #16)
# ---------------------------------------------------------------------------
# The wizard surfaces a binary choice to the operator:
#
#   1. ``llm``      — generate fresh PDF extraction via the orchestrator in
#                     ``scripts.extraction.pdf_pipeline`` (text redaction +
#                     capable-LLM call + snapshot fallback per-PDF when the
#                     LLM can't handle a form).
#   2. ``snapshot`` — skip the LLM entirely and publish the human-verified
#                     baseline JSONs from ``data/snapshots/{STUDY}/pdfs/`` (the
#                     reviewed baseline; LLM-invisible).
#
# When :data:`_PDF_EXTRACTION_MODE_ENV` is unset, ``extract_pdfs_to_jsonl``
# falls back to the legacy raw-PDF API path (gated by the two-part
# ``REPORTALIN_PDF_PHI_FREE`` + attestation note). The legacy path remains
# the CLI default so existing automation does not change behaviour.
_PDF_EXTRACTION_MODE_ENV: str = "REPORTALIN_PDF_EXTRACTION_MODE"
_PDF_EXTRACTION_MODE_LLM: str = "llm"
_PDF_EXTRACTION_MODE_SNAPSHOT: str = "snapshot"
_PDF_EXTRACTION_MODES: frozenset[str] = frozenset(
    {_PDF_EXTRACTION_MODE_LLM, _PDF_EXTRACTION_MODE_SNAPSHOT}
)


def _pdf_extraction_mode() -> str:
    """Return the configured extraction mode (``"llm"`` / ``"snapshot"``),
    or ``""`` when the env var is unset / unrecognised (legacy path)."""
    raw = os.environ.get(_PDF_EXTRACTION_MODE_ENV, "").strip().lower()
    return raw if raw in _PDF_EXTRACTION_MODES else ""


def _initial_snapshot_pdfs_dir() -> Path:
    """``data/snapshots/{STUDY}/pdfs/`` — the
    canonical location of the human-verified baseline PDF JSONs the
    snapshot fallback publishes verbatim. Layout matches
    ``trio_bundle/pdfs/`` (one ``{stem}_variables.json`` per form).
    """
    return Path(config.STUDY_SNAPSHOTS_DIR) / "pdfs"


__all__ = [
    "clean_existing_jsons",
    "extract_pdfs_to_jsonl",
    "process_single_pdf",
    "validate_depends_on",
]

# --- Prompts ---

PDF_EXTRACTION_SYSTEM_PROMPT: str = (
    "You are an expert clinical data analyst who extracts variable definitions from clinical research case report forms.\n"
    " \n"
    "Your task: given a form (as a PDF), produce a complete JSON dictionary that "
    "maps every field on the form to a structured variable definition.\n"
    " \n"
    "## Output format\n"
    " \n"
    "Return ONLY valid JSON (no markdown fences, no commentary) with this schema:\n"
    " \n"
    "{\n"
    '  "form_name": "Human-readable form title (e.g. Form 1A - Index Case Screening)",\n'
    '  "source_pdf": "the original PDF filename for traceability",\n'
    '  "version": "use the version number = v1.0",\n'
    '  "summary": "a brief high-level description of the form\'s purpose and content",\n'
    '  "variables": {\n'
    '    "ABBREVIATION": {\n'
    '        "description": "Full question or field text as written on the form",\n'
    '        "values": {\n'
    '            "1": "label",\n'
    '            "2": "label", // increase this list as needed for all categorical options\n'
    "        },  // only for categorical; null otherwise\n"
    '        "depends_on": "PARENT_ABBREVIATION or null",\n'
    '        "condition": "Human-readable activation condition, or null", \n'
    '        "section_context": "Full instruction text or description for the section containing this variable, if applicable",\n'
    "    }\n"
    "  },\n"
    '  "sections": {\n'
    '    "SECTION_NAME": {\n'
    '      "context": "Full instruction text or description for this specific section containing this variable, if applicable",\n'
    '      "variables": ["ABBREV1", "ABBREV2"] // list of variable abbreviations that belong to this section, if applicable\n'
    "    }\n"
    "  }\n"
    "}\n"
    "\n"
    "# Rules and tips:\n"
    "\n"
    "1. Extract variable abbreviations EXACTLY as printed on the form. Do not modify or create new abbreviations.\n"
    "\n"
    "2. For categorical variables (e.g. checkboxes, yes or no), extract ALL response options and their codes/labels directly from the FORM.\n"
    "\n"
    '3. Capture conditional logic: if a field only appears when another field has a specific value, note this in "depends_on" and "condition".\n'
    "\n"
    "4. Be thorough: capture every single field on the form.\n"
    "\n"
    "5. DONOT wraps the JSON in markdown code block\n"
)

PDF_EXTRACTION_USER_PROMPT: str = (
    "I'm providing a clinical research form (CRF) as a PDF.\n\n"
    "Please extract all variables from this form and return them in the specified JSON format. "
    "Be thorough — capture every single field, including:\n"
    "- All variable abbreviations exactly as printed on the form\n"
    "- Complete question/field descriptions\n"
    "- All categorical response options with their codes\n"
    "- Any conditional logic or dependencies between fields\n"
    "- Section groupings and their context"
)


# --- Provider initialization ---


# --- Vision-capable providers for PDF extraction ---

_PDF_VISION_PROVIDERS = frozenset({"anthropic", "google"})


def _init_pdf_anthropic(api_key: str, model: str) -> tuple[Any, str, dict[str, Any]]:
    """Initialize Anthropic client for PDF extraction."""
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError("anthropic package required: pip install anthropic") from exc
    return anthropic.Anthropic(api_key=api_key), model, {}


def _init_pdf_google(api_key: str, model: str) -> tuple[Any, str, dict[str, Any]]:
    """Initialize Google GenAI client for PDF extraction."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ImportError("google-genai package required: pip install google-genai") from exc
    client = genai.Client(api_key=api_key)
    return client, model, {"types": types}


_PDF_PHI_FREE_FLAG = "REPORTALIN_PDF_PHI_FREE"
"""Operator opt-in env var required before any raw PDF byte is sent to an
external LLM API (Anthropic / Google Gemini).

``data/raw/{STUDY}/annotated_pdfs/`` is treated as PHI-bearing unless
explicitly declared otherwise. Sending PHI-bearing PDFs to a third-
party API is a network egress of patient data. Without the flag,
``_resolve_pdf_provider`` refuses to initialise an external client.

Truthy values: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive).

The flag alone is not sufficient: the operator must ALSO create an
attestation note at :data:`_PDF_PHI_FREE_AUTHORITY` recording who
reviewed the PDFs, when, and what they checked. This mirrors the
``authorities/phi_limited_dataset.md`` pattern used by
:mod:`scripts.security.phi_scrub` for the Limited Dataset posture —
both are "signed operator assertions" the IRB dossier can reference.
"""

_PDF_PHI_FREE_AUTHORITY = "authorities/phi_free_pdfs.md"
"""Path (relative to ``config.BASE_DIR``) of the operator's PHI-free
attestation note. Must exist and be non-empty before external-API PDF
extraction is allowed. Content convention:

    * Who performed the review (name + role)
    * When (UTC timestamp)
    * Which files under ``data/raw/{STUDY}/annotated_pdfs/`` were reviewed
    * What was checked (no example subject IDs, no example PHI values,
      no staff signatures, no scanner burn-in, no version-control
      watermarks containing personal names)
    * A single-line declaration: "These PDFs are verified PHI-free."

The note is a text file under version control — changes to it are
audit-trail events. Deleting the file revokes the attestation and
reinstates the refusal.
"""


def _pdf_phi_free_opt_in() -> bool:
    return os.environ.get(_PDF_PHI_FREE_FLAG, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _pdf_phi_free_authority_path() -> Path:
    """Return the absolute path to the attestation note."""
    return Path(config.BASE_DIR) / _PDF_PHI_FREE_AUTHORITY


def _pdf_phi_free_authority_present() -> bool:
    """Return True iff the attestation note exists and is non-empty."""
    path = _pdf_phi_free_authority_path()
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _resolve_pdf_provider() -> tuple[str, Any, str, dict[str, Any]]:
    """Resolve LLM provider, client, and model for PDF extraction.

    Reads LLM provider, API key, and model from environment variables
    (``LLM_PROVIDER``, ``ANTHROPIC_API_KEY`` / ``GOOGLE_API_KEY``,
    ``LLM_MODEL``) with fallback to ``config.LLM_PROVIDER`` /
    ``config.LLM_MODEL``. PDF extraction requires a vision-capable
    provider (``anthropic`` or ``google``).

    **Two-part PHI-safety gate.** External-API PDF extraction is
    refused unless BOTH preconditions hold:

    1. ``REPORTALIN_PDF_PHI_FREE=1`` is set in the environment.
    2. ``authorities/phi_free_pdfs.md`` exists and is non-empty.

    Condition (1) is the run-time opt-in; condition (2) is the durable,
    version-controlled operator attestation that survives across
    machines and operator handoffs. Either alone is insufficient: the
    env flag without the note would let an operator bypass the audit
    trail; the note without the flag would let a stale attestation
    silently reactivate extraction on a different host.

    Returns:
        ``(provider_name, client, model, extras)``

    Raises:
        ValueError: if the env flag or the attestation note is missing,
            if no API key is configured, or if the provider lacks PDF
            vision support.
    """
    if not _pdf_phi_free_opt_in():
        raise ValueError(
            "PDF extraction via external LLM API refused.\n\n"
            "  Reason: data/raw/{STUDY}/annotated_pdfs/ is treated as PHI-bearing\n"
            "  by default. Sending raw PDF bytes to Anthropic / Google APIs\n"
            "  would egress patient data.\n\n"
            "  Remediation paths (pick one):\n"
            "    a) If your annotated_pdfs are VERIFIED PHI-FREE (blank CRFs,\n"
            "       protocol-only, MOP), declare so in TWO places:\n"
            "         1. Set the env flag:  export REPORTALIN_PDF_PHI_FREE=1\n"
            "         2. Create an attestation note at\n"
            f"            {_PDF_PHI_FREE_AUTHORITY}\n"
            "            recording who reviewed the PDFs, when, and what\n"
            "            they checked (see module docstring for template).\n"
            "    b) Use pre-extracted PHI-free JSON files via --pdf-source <path>.\n"
            "       The --pdf-source path is copied into the staging bundle\n"
            "       unchanged; no LLM call is made.\n"
            "    c) Skip the PDF leg entirely — the pipeline succeeds without it,\n"
            "       and trio_bundle/pdfs/ is simply omitted."
        )

    if not _pdf_phi_free_authority_present():
        authority = _pdf_phi_free_authority_path()
        raise ValueError(
            "PDF extraction via external LLM API refused.\n\n"
            "  Reason: REPORTALIN_PDF_PHI_FREE=1 is set, but the operator\n"
            f"  attestation note at {authority} is missing or empty.\n\n"
            "  The env flag is a runtime opt-in; the attestation note is the\n"
            "  durable, version-controlled record of WHO reviewed the PDFs,\n"
            "  WHEN, and WHAT they checked. Both are required so the IRB\n"
            "  dossier can reference a concrete signed attestation rather\n"
            "  than an ephemeral environment variable.\n\n"
            "  Create the note with content such as:\n\n"
            "    # PHI-free PDF attestation\n"
            "    Reviewed by:  <name, role>\n"
            "    Reviewed at:  <UTC timestamp>\n"
            "    Files:        data/raw/{STUDY}/annotated_pdfs/*.pdf\n"
            "    Verified:     no example subject IDs, no example PHI\n"
            "                  values, no staff signatures, no scanner\n"
            "                  burn-in, no version-control watermarks.\n"
            "    Declaration:  These PDFs are verified PHI-free."
        )

    provider = (
        os.environ.get("LLM_PROVIDER", "").strip().lower()
        or getattr(config, "LLM_PROVIDER", "").strip().lower()
    )
    # Normalise LangChain provider id → PDF vision provider id
    if provider == "google-genai":
        provider = "google"

    api_key = ""
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    elif provider == "google":
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip() or getattr(config, "LLM_MODEL", "").strip()

    if not provider:
        raise ValueError(
            "No LLM provider configured.\n\n"
            "  Set LLM_PROVIDER (anthropic or google-genai recommended for\n"
            "  PDF extraction) and provide the corresponding API key.\n\n"
            "  Via CLI:   python main.py --pipeline --provider anthropic --model claude-opus-4-7\n"
            "  Via env:   export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-...\n"
        )

    if provider not in _PDF_VISION_PROVIDERS:
        raise ValueError(
            f"PDF extraction requires a vision-capable LLM provider.\n\n"
            f"  Current provider: {provider}\n"
            f"  Supported for PDF extraction: {', '.join(sorted(_PDF_VISION_PROVIDERS))}\n\n"
            f"  PDF forms must be sent as documents to the LLM for structured\n"
            f"  variable extraction. Only Anthropic (Claude) and Google (Gemini)\n"
            f"  support native PDF document input.\n\n"
            f"  To fix: --provider anthropic (or google-genai) with the corresponding API key."
        )

    if not api_key:
        env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "GOOGLE_API_KEY"
        raise ValueError(
            f"No API key found for {provider} provider.\n\n  Set {env_var} in your .env file."
        )

    if not model:
        _default_models = {
            "anthropic": "claude-opus-4-7",
            "google": "gemini-3.1-pro-preview",
        }
        model = _default_models[provider]
        log.info("No model configured — defaulting to %s for PDF extraction", model)

    if provider == "anthropic":
        return ("anthropic", *_init_pdf_anthropic(api_key, model))
    else:
        return ("google", *_init_pdf_google(api_key, model))


# --- LLM extraction dispatch ---


def _extract_via_anthropic(client: Any, pdf_path: Path, model: str) -> str:
    """Send PDF to Anthropic Claude, return raw response text."""
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")
    full_response = ""
    with client.messages.stream(
        model=model,
        max_tokens=config.PDF_EXTRACTION_MAX_TOKENS,
        temperature=0.0,
        system=PDF_EXTRACTION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {"type": "text", "text": PDF_EXTRACTION_USER_PROMPT},
                ],
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            full_response += text

    final_msg = stream.get_final_message()
    usage = getattr(final_msg, "usage", None)
    if usage:
        log.debug(
            "PDF tokens for %s: in=%s, out=%s",
            pdf_path.name,
            f"{getattr(usage, 'input_tokens', 0):,}",
            f"{getattr(usage, 'output_tokens', 0):,}",
        )
    if getattr(final_msg, "stop_reason", "") == "max_tokens":
        log.warning("Max tokens reached for %s — extraction may be truncated", pdf_path.name)
    return full_response


def _extract_via_google(client: Any, types_mod: Any, pdf_path: Path, model: str) -> str:
    """Send PDF to Google Gemini, return raw response text."""
    cfg = types_mod.GenerateContentConfig(
        max_output_tokens=config.PDF_EXTRACTION_MAX_TOKENS,
        temperature=0.0,
        system_instruction=PDF_EXTRACTION_SYSTEM_PROMPT,
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            types_mod.Part.from_bytes(data=pdf_path.read_bytes(), mime_type="application/pdf"),
            PDF_EXTRACTION_USER_PROMPT,
        ],
        config=cfg,
    )
    usage = getattr(response, "usage_metadata", None)
    if usage:
        log.debug(
            "PDF tokens for %s: in=%s, out=%s",
            pdf_path.name,
            f"{getattr(usage, 'prompt_token_count', 0):,}",
            f"{getattr(usage, 'candidates_token_count', 0):,}",
        )
    text = getattr(response, "text", None)
    if not text:
        parts: list[str] = []
        for c in getattr(response, "candidates", []) or []:
            for p in getattr(getattr(c, "content", None), "parts", []) or []:
                t = getattr(p, "text", None)
                if t:
                    parts.append(t)
        text = "".join(parts)
    return text or ""


def _extract_variables_from_pdf(
    provider: str,
    client: Any,
    pdf_path: Path,
    model: str,
    **kw: Any,
) -> dict[str, Any]:
    """Send PDF to LLM and parse structured variable JSON response."""
    if provider == "anthropic":
        raw = _extract_via_anthropic(client, pdf_path, model)
    elif provider == "google":
        raw = _extract_via_google(client, kw.get("types"), pdf_path, model)
    else:
        raise ValueError(f"Unsupported PDF extraction provider: {provider}")
    json_str = raw.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", json_str, re.DOTALL)
    if m:
        json_str = m.group(1).strip()
    result: dict[str, Any] = json.loads(json_str)
    if not isinstance(result, dict):
        raise ValueError(
            f"LLM returned unexpected JSON type {type(result).__name__!r} "
            f"(expected object/dict) for {pdf_path.name}"
        )
    return result


# --- Cross-form dedup (thin wrapper) ---


def clean_duplicate_variables(
    json_files: list[Path],
) -> dict[str, dict[str, Any]]:
    """Remove cross-form duplicate variables from a set of per-form JSONs.

    Thin file-I/O wrapper around the pure
    :func:`~scripts.extraction.dedup.clean_cross_form_duplicates` helper.
    """
    if not json_files:
        return {}

    form_data: dict[str, dict[str, Any]] = {}
    for fp in json_files:
        try:
            form_data[fp.name] = load_variables_json(fp)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Skipping %s during dedup scan: %s", fp.name, e)
            continue

    return clean_cross_form_duplicates(form_data)


# --- File integrity ---


def check_json_integrity(file_path: Path) -> bool:
    """Validate variables JSON file: exists, non-empty, and parseable."""
    try:
        if not file_path.exists() or file_path.stat().st_size == 0:
            return False
        with file_path.open(encoding=FILE_ENCODING) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        typed_data = cast(dict[str, Any], data)
        variables = typed_data.get("variables")
        return isinstance(variables, dict) and len(cast(dict[str, Any], variables)) > 0
    except (json.JSONDecodeError, OSError):
        return False


# --- Validation & cleaning ---


def validate_depends_on(json_files: list[Path]) -> list[tuple[str, str, str]]:
    """Check for broken depends_on references across variable JSONs."""
    broken: list[tuple[str, str, str]] = []
    for fp in json_files:
        try:
            data = load_variables_json(fp)
        except (json.JSONDecodeError, OSError):
            continue
        variables = data.get("variables", {})
        var_names = set(variables.keys())
        for vn, vd in variables.items():
            dep = vd.get("depends_on")
            if dep and dep not in var_names:
                broken.append((fp.name, vn, dep))
    return broken


def clean_existing_jsons(
    json_dir: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run dedup + cross-form dedup + validation on existing JSONs in-place.

    Operates on the canonical output directory (default
    ``config.PDF_EXTRACTIONS_DIR``) without re-running LLM extraction.

    Note:
        The default directory (``config.PDF_EXTRACTIONS_DIR`` =
        ``output/{STUDY}/trio_bundle/pdfs/``) is the *published* bundle path,
        not the staging path.  :func:`extract_pdfs_to_jsonl` writes freshly
        extracted files to ``config.STAGING_PDFS_DIR`` (``tmp/{STUDY}/pdfs/``).
        If you run ``--clean-only`` immediately after extraction without
        passing ``--output-dir``, the default directory will contain no
        freshly extracted files (they are still in staging).  Pass
        ``--output-dir <staging_path>`` explicitly to target the staging
        directory, or let the pipeline's publish step promote staging files
        to the bundle before cleaning.
    """
    src = Path(json_dir) if json_dir else Path(config.PDF_EXTRACTIONS_DIR)
    json_files = discover_variable_jsons(src)
    if not json_files:
        log.warning("No *_variables.json files found in %s", src)
        return {"error": f"No JSON files found in {src}"}

    log.info("Found %d variable JSONs in %s", len(json_files), src)

    # Within-file dedup
    total_within_dupes = 0
    total_vars = 0
    for fp in json_files:
        try:
            data = load_variables_json(fp)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("%s: %s", fp.name, e)
            continue
        result = remove_within_file_duplicates(data, dry_run=dry_run)
        n_dupes = result.get("duplicates_removed", 0)
        if n_dupes > 0:
            total_within_dupes += n_dupes
            log.info("%s: removed %d within-file duplicate(s)", fp.name, n_dupes)
            if not dry_run and "cleaned_data" in result:
                atomic_write_json(fp, result["cleaned_data"], prefix=NAMED_TEMP_PREFIX)
                data = result["cleaned_data"]
        total_vars += len(data.get("variables", {}))

    if total_within_dupes == 0:
        log.info("No within-file duplicates")
    log.info("Total: %d variables across %d forms", total_vars, len(json_files))

    if dry_run:
        return {
            "files_found": len(json_files),
            "total_variables": total_vars,
            "within_file_duplicates_removed": total_within_dupes,
        }

    # Cross-form dedup
    modified = clean_duplicate_variables(json_files)
    cross_dupes = 0
    if modified:
        for filename, cleaned_data in modified.items():
            atomic_write_json(src / filename, cleaned_data, prefix=NAMED_TEMP_PREFIX)
            cross_dupes += 1

    # Validate depends_on
    broken_refs = validate_depends_on(json_files)
    if broken_refs:
        log.warning("%d broken depends_on references:", len(broken_refs))
        for fname, vn, dep in broken_refs:
            log.warning("%s: %s -> %s (MISSING)", fname, vn, dep)

    return {
        "files_found": len(json_files),
        "total_variables": total_vars,
        "within_file_duplicates_removed": total_within_dupes,
        "cross_form_duplicates_removed": cross_dupes,
        "broken_refs": broken_refs,
    }


# --- Single PDF processing ---


def process_single_pdf(
    pdf_path: Path,
    output_dir: Path,
    client: Any,
    model: str,
    *,
    provider: str = "anthropic",
    **kw: Any,
) -> tuple[bool, int, str | None]:
    """Extract one PDF into structured JSON.

    Produces ``{stem}_variables.json``.

    Args:
        pdf_path: Path to the annotated PDF file.
        output_dir: Output directory.
        client: Initialized LLM client (Anthropic or Google).
        model: Model identifier.
        provider: ``"anthropic"`` or ``"google"``.
        **kw: Extra provider kwargs (e.g. ``types`` for Google).

    Returns:
        ``(success, variable_count, error_message)``.

    Note:
        This function accepts a pre-built ``client`` and bypasses the
        two-part PHI safety gate that lives in :func:`_resolve_pdf_provider`.
        Callers must pass through ``_resolve_pdf_provider`` before invoking
        this function directly.  The only in-tree caller,
        :func:`extract_pdfs_to_jsonl`, always does this; external callers
        importing ``process_single_pdf`` from ``scripts.extraction`` must
        not construct a client themselves and skip the gate.
    """
    start = time.time()
    stem = pdf_path.stem

    try:
        variables_json = _extract_variables_from_pdf(provider, client, pdf_path, model, **kw)

        var_count = len(variables_json.get("variables", {}))
        if var_count == 0:
            log.warning("No variables extracted from %s", pdf_path.name)
            return False, 0, None

        # Within-file dedup (catch case-insensitive collisions from LLM)
        dedup_result = remove_within_file_duplicates(variables_json, dry_run=False)
        n_deduped = dedup_result.get("duplicates_removed", 0)
        if n_deduped and "cleaned_data" in dedup_result:
            variables_json = dedup_result["cleaned_data"]
            var_count = len(variables_json.get("variables", {}))
            log.info("Within-file dedup for %s: removed %d duplicate(s)", pdf_path.name, n_deduped)

        json_output = output_dir / f"{stem}{JSON_VARIABLES_SUFFIX}"
        atomic_write_json(json_output, variables_json, prefix=NAMED_TEMP_PREFIX)

        elapsed = time.time() - start
        vlog.detail(f"✓ {pdf_path.name}: {var_count} variables ({elapsed:.1f}s)")
        log.debug("Extracted %s: %d variables", pdf_path.name, var_count)
        return True, var_count, None

    except json.JSONDecodeError as e:
        error_msg = f"JSON parse error for {pdf_path.name}: {e}"
        log.error(error_msg)
        return False, 0, error_msg
    except FileNotFoundError as e:
        error_msg = f"PDF not found: {pdf_path.name}: {e}"
        log.error(error_msg)
        return False, 0, error_msg
    except Exception as e:
        error_msg = f"Error extracting {pdf_path.name}: {e}"
        log.error(error_msg)
        vlog.detail(f"✗ {error_msg}")
        return False, 0, error_msg


# --- Result helper ---


def _empty_result(
    files_found: int = 0,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return a zero-count result dict for early exits."""
    return {
        RESULT_FILES_FOUND: files_found,
        RESULT_FILES_CREATED: 0,
        RESULT_FILES_SKIPPED: 0,
        RESULT_TOTAL_CHUNKS: 0,
        RESULT_DUPLICATES_CLEANED: 0,
        RESULT_ERRORS: errors or [],
        RESULT_PROCESSING_TIME: 0,
    }


# --- Main orchestration ---


def _run_snapshot_mode(
    pdf_files: list[Path],
    dest_dir: Path,
    *,
    force: bool,
) -> tuple[int, int, int, list[dict[str, str]]]:
    """Publish the verified baseline JSONs verbatim — no LLM call.

    For each annotated PDF, copy the matching
    ``{stem}_variables.json`` from ``data/snapshots/{STUDY}/pdfs/`` (the
    reviewed baseline; LLM-invisible) into ``dest_dir``. A missing
    snapshot is reported as an error (the form will simply be absent
    from the published bundle); it is NOT a fatal failure.
    """
    snapshot_dir = _initial_snapshot_pdfs_dir()
    log.info("PDF extraction: snapshot mode — using %s", snapshot_dir)

    if not snapshot_dir.is_dir():
        msg = (
            f"Snapshot directory not found: {snapshot_dir}. "
            "Run --pipeline once with REPORTALIN_PDF_EXTRACTION_MODE=llm "
            "or seed the reviewed snapshot baseline."
        )
        log.error(msg)
        return 0, 0, 0, [{"file": "", "error": msg}]

    total_vars, files_created, files_skipped = 0, 0, 0
    errors: list[dict[str, str]] = []

    for idx, pdf_path in enumerate(pdf_files, 1):
        stem = pdf_path.stem
        json_out = dest_dir / f"{stem}{JSON_VARIABLES_SUFFIX}"

        if not force and json_out.exists() and check_json_integrity(json_out):
            files_skipped += 1
            log.info(
                "  [%d/%d] Skipping %s (valid output exists)",
                idx,
                len(pdf_files),
                pdf_path.name,
            )
            continue

        snap_path = snapshot_dir / f"{stem}{JSON_VARIABLES_SUFFIX}"
        if not snap_path.is_file():
            alt = snapshot_dir / f"{stem}.json"
            snap_path = alt if alt.is_file() else snap_path

        if not snap_path.is_file():
            msg = f"No snapshot for {pdf_path.name}: expected {snap_path.name}"
            log.warning(msg)
            errors.append({"file": pdf_path.name, "error": msg})
            continue

        try:
            data = json.loads(snap_path.read_text(encoding=FILE_ENCODING))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append({"file": pdf_path.name, "error": f"snapshot unreadable: {exc}"})
            continue

        if not isinstance(data, dict):
            errors.append({"file": pdf_path.name, "error": "snapshot is not a JSON object"})
            continue

        data["extraction_tier"] = "snapshot"
        atomic_write_json(json_out, data, prefix=NAMED_TEMP_PREFIX)
        files_created += 1
        total_vars += len(data.get("variables", {}) or {})
        log.info("  [%d/%d] %s ← snapshot", idx, len(pdf_files), pdf_path.name)

    return total_vars, files_created, files_skipped, errors


def _resolve_orchestrator_credentials() -> tuple[str | None, str | None, str | None]:
    """Pull provider/model/api_key from the subprocess env (the wizard
    populates ``ANTHROPIC_API_KEY``/``GOOGLE_API_KEY`` via the KeyStore's
    ``env_for_subprocess`` helper before spawning ``main.py``).

    Unlike :func:`_resolve_pdf_provider`, no PHI-free attestation gate is
    required: the orchestrator redacts PHI from extracted text before any
    byte leaves the host (it never sends raw PDF bytes), so the audit
    posture is fundamentally different.

    Returns ``(provider, model, api_key)``; any element may be ``None``,
    in which case :func:`pdf_pipeline.extract_pdf` will skip the LLM tier
    for that PDF and fall back to the snapshot.
    """
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider == "google-genai":
        provider = "google"
    if not provider:
        return None, None, None

    model = os.environ.get("LLM_MODEL", "").strip() or None

    api_key_env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
    }.get(provider)
    api_key = (os.environ.get(api_key_env, "").strip() if api_key_env else "") or None

    return provider, model, api_key


def _run_orchestrator_mode(
    pdf_files: list[Path],
    dest_dir: Path,
    *,
    force: bool,
) -> tuple[int, int, int, list[dict[str, str]]]:
    """Run the two-way orchestrator (``pdf_pipeline.extract_pdf``) per PDF.

    Each form goes through redacted-text → capable-LLM-call → merge with
    code candidate, and falls back to the reviewed snapshot baseline
    when the LLM tier is unavailable. The orchestrator's idempotent cache
    lives under ``tmp/{STUDY}/.pdf_cache/``.
    """
    from scripts.extraction.pdf_pipeline import extract_pdf

    provider, model, api_key = _resolve_orchestrator_credentials()
    snapshot_dir = _initial_snapshot_pdfs_dir()
    cache_dir = Path(config.STUDY_STAGING_DIR) / ".pdf_cache"
    log.info(
        "PDF extraction: orchestrator mode — provider=%s model=%s snapshot_dir=%s cache_dir=%s",
        provider,
        model,
        snapshot_dir,
        cache_dir,
    )

    total_vars, files_created, files_skipped = 0, 0, 0
    errors: list[dict[str, str]] = []

    with vlog.file_processing("PDF extraction (orchestrator)", total_records=len(pdf_files)):
        for idx, pdf_path in enumerate(pdf_files, 1):
            stem = pdf_path.stem
            json_out = dest_dir / f"{stem}{JSON_VARIABLES_SUFFIX}"

            if not force and json_out.exists() and check_json_integrity(json_out):
                files_skipped += 1
                log.info(
                    "  [%d/%d] Skipping %s (valid output exists)",
                    idx,
                    len(pdf_files),
                    pdf_path.name,
                )
                continue

            try:
                result = extract_pdf(
                    pdf_path,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    snapshot_dir=snapshot_dir if snapshot_dir.is_dir() else None,
                    cache_dir=cache_dir,
                )
            except Exception as exc:  # never let one PDF crash the run
                errors.append({"file": pdf_path.name, "error": str(exc)})
                log.error("orchestrator failed for %s: %s", pdf_path.name, exc)
                continue

            if result.tier == "empty":
                msg = (
                    f"orchestrator produced no extractable variables "
                    f"(llm_skipped={result.llm_skipped_reason!r})"
                )
                errors.append({"file": pdf_path.name, "error": msg})
                continue

            atomic_write_json(json_out, result.data, prefix=NAMED_TEMP_PREFIX)
            files_created += 1
            total_vars += len(result.data.get("variables", {}) or {})
            log.info(
                "  [%d/%d] %s ← %s%s",
                idx,
                len(pdf_files),
                pdf_path.name,
                result.tier,
                " (cache hit)" if result.cache_hit else "",
            )

    return total_vars, files_created, files_skipped, errors


def extract_pdfs_to_jsonl(
    pdf_dir: Path | None = None,
    output_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Extract all annotated PDFs into structured JSON outputs.

    Discovers PDFs and writes per-form structured JSON
    (``_variables.json``) files. The actual extraction strategy depends
    on the :data:`_PDF_EXTRACTION_MODE_ENV` env var, which the wizard
    sets per the operator's choice:

    - ``llm``      — :func:`_run_orchestrator_mode` (text-redacted LLM
                     call paired with code path; snapshot fallback per-PDF).
    - ``snapshot`` — :func:`_run_snapshot_mode` (publish verified baseline
                     JSONs verbatim; no LLM call).
    - unset        — legacy raw-PDF API path
                     (:func:`_resolve_pdf_provider`-gated). Preserves
                     existing CLI behaviour.

    .. note::
       Despite its name (kept for backward compatibility), this function now
       writes **only** JSON.

    Args:
        pdf_dir: Directory containing annotated PDFs.
            Defaults to ``config.ANNOTATED_PDFS_DIR``.
        output_dir: Output directory.
            Defaults to ``config.STAGING_PDFS_DIR`` (``tmp/{STUDY}/pdfs/``);
            a subsequent publish step promotes the bundle to
            ``trio_bundle/pdfs/``.
        force: If True, reprocess all files even if output exists.

    Returns:
        Dict with keys: files_found, files_created, files_skipped,
        variables_extracted, duplicates_cleaned, errors, processing_time.
    """
    overall_start = time.time()

    src_dir = Path(pdf_dir) if pdf_dir else Path(config.ANNOTATED_PDFS_DIR)
    dest_dir = Path(output_dir) if output_dir else Path(config.STAGING_PDFS_DIR)

    # Validate source
    if not src_dir.exists() or not src_dir.is_dir():
        msg = f"PDF directory not found: {src_dir}"
        log.error(msg)
        return _empty_result(errors=[{"file": "", "error": msg}])

    # Discover PDFs (ignore hidden/system junk files)
    pdf_files = sorted(
        p
        for p in src_dir.glob(PDF_PATTERN)
        if not p.name.startswith(".") and not p.name.startswith("~")
    )
    if not pdf_files:
        log.warning(f"No PDF files found in {src_dir}")
        return _empty_result()

    log.info(f"Found {len(pdf_files)} PDFs in {src_dir}")
    vlog.detail(f"Source: {src_dir}, Output: {dest_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Mode dispatch ─────────────────────────────────────────────────────
    mode = _pdf_extraction_mode()

    if mode == _PDF_EXTRACTION_MODE_SNAPSHOT:
        total_vars, files_created, files_skipped, errors = _run_snapshot_mode(
            pdf_files, dest_dir, force=force
        )
    elif mode == _PDF_EXTRACTION_MODE_LLM:
        total_vars, files_created, files_skipped, errors = _run_orchestrator_mode(
            pdf_files, dest_dir, force=force
        )
    else:
        # Legacy raw-PDF API path (CLI default; gated by the two-part
        # PHI-free attestation in ``_resolve_pdf_provider``).
        try:
            provider, client, model, extras = _resolve_pdf_provider()
            log.info("PDF extraction: provider=%s, model=%s", provider, model)
        except Exception as e:
            msg = f"Failed to initialize LLM client: {e}"
            log.error(msg)
            return _empty_result(
                files_found=len(pdf_files),
                errors=[{"file": "", "error": msg}],
            )

        total_vars, files_created, files_skipped = 0, 0, 0
        errors = []

        with vlog.file_processing("PDF extraction", total_records=len(pdf_files)):
            for idx, pdf_path in enumerate(pdf_files, 1):
                json_out = dest_dir / f"{pdf_path.stem}{JSON_VARIABLES_SUFFIX}"

                if not force and json_out.exists() and check_json_integrity(json_out):
                    files_skipped += 1
                    log.info(
                        "  [%d/%d] Skipping %s (valid output exists)",
                        idx,
                        len(pdf_files),
                        pdf_path.name,
                    )
                    continue

                log.info("  [%d/%d] Extracting %s", idx, len(pdf_files), pdf_path.name)

                success, count, error_msg = process_single_pdf(
                    pdf_path,
                    dest_dir,
                    client,
                    model,
                    provider=provider,
                    **extras,
                )

                if success:
                    files_created += 1
                    total_vars += count
                elif error_msg:
                    errors.append({"file": pdf_path.name, "error": error_msg})

                # Rate-limit between API calls
                if idx < len(pdf_files):
                    time.sleep(INTER_PDF_DELAY)

    # Post-extraction: cross-form duplicate variable removal
    duplicates_removed = 0
    all_json_outputs = sorted(dest_dir.glob(f"*{JSON_VARIABLES_SUFFIX}"))
    if all_json_outputs:
        log.info("Cleaning cross-form duplicate variables...")
        modified = clean_duplicate_variables(all_json_outputs)
        if modified:
            for filename, cleaned_data in modified.items():
                json_path = dest_dir / filename
                atomic_write_json(json_path, cleaned_data, prefix=NAMED_TEMP_PREFIX)
                duplicates_removed += 1
        else:
            log.info("  No cross-form duplicates found")

    # Post-extraction: validate depends_on references
    broken_refs = validate_depends_on(all_json_outputs)
    if broken_refs:
        log.warning("%d broken depends_on references:", len(broken_refs))
        for fname, vn, dep in broken_refs:
            log.warning("      %s: %s -> %s (MISSING)", fname, vn, dep)
    else:
        log.info("  No broken depends_on references")

    elapsed = time.time() - overall_start

    log.info("PDF Extraction complete:")
    log.info("  %d total variables extracted", total_vars)
    log.info("  %d JSON files created", files_created)
    log.info("  %d files skipped (already exist)", files_skipped)
    if duplicates_removed:
        log.info("  %d files cleaned (cross-form duplicates removed)", duplicates_removed)
    log.info("  Output: %s", dest_dir)
    if errors:
        log.warning("  %d files had errors", len(errors))

    log.info(
        "[PDF EXTRACTION] study=%s files=%d created=%d "
        "skipped=%d vars=%d deduped=%d errors=%d elapsed=%.1fs",
        str(config.STUDY_NAME),
        len(pdf_files),
        files_created,
        files_skipped,
        total_vars,
        duplicates_removed,
        len(errors),
        elapsed,
    )

    return {
        RESULT_FILES_FOUND: len(pdf_files),
        RESULT_FILES_CREATED: files_created,
        RESULT_FILES_SKIPPED: files_skipped,
        RESULT_TOTAL_CHUNKS: total_vars,
        RESULT_DUPLICATES_CLEANED: duplicates_removed,
        RESULT_ERRORS: errors,
        RESULT_PROCESSING_TIME: elapsed,
    }


# --- CLI ---

if __name__ == "__main__":
    try:
        log.setup_logging(
            module_name=MODULE_LOGGER,
            log_level=getattr(config, "LOG_LEVEL", "INFO"),
        )

        import argparse

        parser = argparse.ArgumentParser(
            description="Extract annotated study PDFs using LLM document understanding"
        )
        parser.add_argument(
            "--pdf-dir",
            type=Path,
            default=None,
            help=f"PDF source directory (default: {config.ANNOTATED_PDFS_DIR})",
        )
        parser.add_argument(
            "--output-dir", type=Path, default=None, help="Output directory for JSON files"
        )
        parser.add_argument("--force", action="store_true", help="Force reprocessing of all files")
        parser.add_argument(
            "--clean-only",
            action="store_true",
            help=(
                "Run dedup + validation on existing JSONs (no LLM extraction). "
                "Defaults to the published bundle directory "
                f"({config.PDF_EXTRACTIONS_DIR}), NOT the staging directory "
                f"({config.STAGING_PDFS_DIR}). Pass --output-dir to target "
                "freshly extracted (not yet promoted) files."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing files (use with --clean-only)",
        )
        args = parser.parse_args()

        if args.clean_only:
            result = clean_existing_jsons(
                json_dir=args.output_dir or Path(config.PDF_EXTRACTIONS_DIR),
                dry_run=args.dry_run,
            )
            sys.exit(1 if result.get("error") else 0)

        result = extract_pdfs_to_jsonl(
            pdf_dir=args.pdf_dir,
            output_dir=args.output_dir,
            force=args.force,
        )

        sys.exit(1 if result[RESULT_ERRORS] else 0)

    except KeyboardInterrupt:
        log.warning("Extraction cancelled by user")
        sys.exit(130)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
