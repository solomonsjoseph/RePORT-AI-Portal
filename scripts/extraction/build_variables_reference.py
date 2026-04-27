"""Build a unified variables.json from the available annotation sources.

Merges two active data sources into a single, canonical reference:

1. **Extraction variables** (``tmp/extracted_variables/*_variables.json``, or
   ``trio_bundle/pdfs/*_variables.json`` when populated)
   — authoritative for *description*, *coded_options*, *depends_on*, *condition*,
   *section*, *section_context*, and form-level metadata (form_id, form_name,
   source_pdf, form_version, form_summary).
2. **Dictionary JSONL** (``trio_bundle/dictionary/tbl*/*.jsonl``)
   — authoritative for *data_type*, *core_status*, and codelist references.

The ``is_phi`` / ``phi_reason`` / ``phi_type`` fields still ship in the
output schema for backward compatibility but are always emitted as
``False`` / ``""`` / ``""``. PHI scrubbing lives in
:mod:`scripts.security.phi_scrub` (Step 1.6 of the pipeline, 8-action
catalog) and does not interact with this variables-reference builder —
by the time this module reads the trio bundle, the artifacts are
already PHI-free.

Output schema (v3, 23 fields per variable)::

    {
        "variable_name":            str,
        "form_id":                  str,
        "form_name":                str,
        "source_pdf":               str,
        "form_version":             str,
        "form_summary":             str,
        "section":                  str | None,
        "section_context":          str | None,
        "description":              str,
        "coded_options":            dict[str, str] | None,
        "depends_on":               str | None,
        "condition":                str | None,
        "data_type":                str,
        "core_status":              str,
        "is_phi":                   bool,       # always False (PHI scrubbing in phi_scrub.py)
        "phi_reason":               str,        # always ""
        "phi_type":                 "id" | "date" | None,  # always None
        "date_kind":                str | None,
        "anchor_rule":              str | None,
        "suggested_output_variable": str | None,
        "approved_for_transform":   bool | None,
        "date_group_by":            str | None,
        "deidentified_as":          list[str],
    }

Usage::

    uv run python main.py --build-variables
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from scripts.extraction._dict_keys import DICT_VAR_KEY as _DICT_VAR_KEY
from scripts.extraction.io import atomic_write_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------
_VarEntry = dict[str, Any]

# Dictionary JSONL column keys
_DICT_QUESTION_KEY = "Question"
_DICT_TYPE_KEY = "Type"
_DICT_CORE_KEY = "Core"
_DICT_CODELIST_KEY = "Code List or format"
_DICT_FORM_KEY = "Form"
_DICT_MODULE_KEY = "Module"

# Codelist JSONL keys
_CL_CODELIST_KEY = "Codelist"
_CL_CODE_KEY = "Codes"
_CL_DESC_KEY = "Descriptors"


# ============================================================================
# Source loaders
# ============================================================================


def _load_codelists(dd_dir: Path) -> dict[str, dict[str, str]]:
    """Load codelist mappings from ``Codelists/Codelists_table_*.jsonl``.

    Returns ``{codelist_name: {code_value: descriptor, ...}}``.
    """
    codelists: dict[str, dict[str, str]] = {}
    cl_dir = dd_dir / "Codelists"
    if not cl_dir.is_dir():
        return codelists

    for cl_file in sorted(cl_dir.glob("Codelists_table_*.jsonl")):
        with open(cl_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cl_name = rec.get(_CL_CODELIST_KEY, "")
                code = rec.get(_CL_CODE_KEY)
                desc = rec.get(_CL_DESC_KEY, "")
                if cl_name and code is not None:
                    codelists.setdefault(cl_name, {})[str(code)] = desc

    logger.info("Loaded %d codelists from %s", len(codelists), cl_dir)
    return codelists


def _load_dictionary_variables(
    dd_dir: Path,
    codelists: dict[str, dict[str, str]],
) -> dict[str, _VarEntry]:
    """Load all ``tbl*/tbl*_table.jsonl`` dictionary files.

    Returns ``{UPPER_VAR_NAME: {description, data_type, core_status, coded_options, form}}``.
    Same richness tie-break applies when a variable appears in multiple tables.
    """
    merged: dict[str, _VarEntry] = {}
    if not dd_dir.is_dir():
        logger.warning("Dictionary mappings directory not found: %s", dd_dir)
        return merged

    for tbl_dir in sorted(dd_dir.iterdir()):
        if (
            not tbl_dir.is_dir()
            or tbl_dir.name.startswith(".")
            or tbl_dir.name in ("Codelists", "Notes")
        ):
            continue
        for jsonl_file in tbl_dir.glob("*_table.jsonl"):
            _parse_dictionary_jsonl(jsonl_file, codelists, merged)

    logger.info("Loaded %d unique variables from dictionary mappings", len(merged))
    return merged


def _parse_dictionary_jsonl(
    jsonl_file: Path,
    codelists: dict[str, dict[str, str]],
    out: dict[str, _VarEntry],
) -> None:
    """Parse a single dictionary JSONL file into *out*."""
    with open(jsonl_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            var_name = rec.get(_DICT_VAR_KEY)
            if not var_name or not isinstance(var_name, str):
                continue
            # Skip retired/placeholder entries (e.g. "----")
            if not var_name.strip("-"):
                continue

            key = var_name.upper()
            description = rec.get(_DICT_QUESTION_KEY) or ""
            data_type = rec.get(_DICT_TYPE_KEY) or "unknown"
            core_status = rec.get(_DICT_CORE_KEY) or ""

            # Resolve codelist reference to actual coded_options
            codelist_ref = rec.get(_DICT_CODELIST_KEY) or ""
            coded_options: dict[str, str] | None = None
            if codelist_ref and codelist_ref in codelists:
                coded_options = codelists[codelist_ref]

            entry: _VarEntry = {
                "description": description,
                "data_type": data_type,
                "core_status": core_status,
                "coded_options": coded_options,
                "form": rec.get(_DICT_FORM_KEY) or "",
            }

            existing = out.get(key)
            if existing is None or len(entry["description"]) > len(existing["description"]):
                out[key] = entry


# ============================================================================
# Rich extraction loader (v3)
# ============================================================================

_FORM_ID_RE = re.compile(r"^(\w+)\s+")


def _parse_form_id(filename: str) -> str:
    """Extract form ID prefix (e.g. ``'1A'``, ``'10'``) from filename."""
    m = _FORM_ID_RE.match(filename)
    return m.group(1) if m else ""


def _build_var_to_section_map(
    sections: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Build ``{UPPER_VAR: (section_name, section_context)}`` from a sections dict."""
    mapping: dict[str, tuple[str, str]] = {}
    for section_name, section_data in sections.items():
        context: str = (section_data.get("context") or "") if isinstance(section_data, dict) else ""
        for var in section_data.get("variables") or [] if isinstance(section_data, dict) else []:
            mapping[var.upper()] = (section_name, context)
    return mapping


def _load_extraction_variables_rich(extraction_dir: Path) -> dict[str, _VarEntry]:
    """Load ``*_variables.json`` from *extraction_dir* with full form + section metadata.

    Keys are upper-cased variable names; values carry form_id, form_name,
    source_pdf, form_version, form_summary, section, section_context, description,
    coded_options, depends_on, and condition.

    When the same variable appears in multiple forms, the entry with the longest
    description wins (richness tie-break per dedup.py convention).
    """
    merged: dict[str, _VarEntry] = {}
    if not extraction_dir.is_dir():
        logger.warning("Extraction directory not found: %s", extraction_dir)
        return merged

    files = sorted(extraction_dir.glob("*_variables.json"))
    if not files:
        logger.warning("No *_variables.json files found in: %s", extraction_dir)
        return merged

    for json_file in files:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable extraction file %s: %s", json_file.name, exc)
            continue

        form_id = _parse_form_id(json_file.name)
        form_name: str = data.get("form_name") or json_file.stem
        source_pdf: str = data.get("source_pdf") or ""
        form_version: str = data.get("version") or ""
        form_summary: str = data.get("summary") or ""

        sections: dict[str, Any] = data.get("sections") or {}
        var_to_section = _build_var_to_section_map(sections)

        variables: dict[str, Any] = data.get("variables") or {}
        for var_name, var_meta in variables.items():
            if not isinstance(var_meta, dict):
                continue
            key = var_name.upper()
            description: str = var_meta.get("description") or ""
            section_name, section_context = var_to_section.get(key, (None, None))

            entry: _VarEntry = {
                "form_id": form_id,
                "form_name": form_name,
                "source_pdf": source_pdf,
                "form_version": form_version,
                "form_summary": form_summary,
                "section": section_name,
                "section_context": section_context or var_meta.get("section_context"),
                "description": description,
                "coded_options": var_meta.get("values"),
                "depends_on": var_meta.get("depends_on"),
                "condition": var_meta.get("condition"),
            }
            existing = merged.get(key)
            if existing is None or len(description) > len(existing["description"]):
                merged[key] = entry

    logger.info(
        "Loaded %d unique variables from %d extraction files in %s",
        len(merged),
        len(files),
        extraction_dir,
    )
    return merged


# ============================================================================
# De-identification mapping helper
# ============================================================================


def _compute_deidentified_as(
    var_name: str,
    is_phi: bool,
    phi_type: str | None,
    date_review: dict[str, dict[str, Any]],
) -> list[str]:
    """Determine what name(s) this variable takes in the de-identified dataset.

    * Non-PHI → ``[var_name]`` (kept as-is)
    * Date PHI → ``[suggested_output_variable]`` from the date review
    * ID PHI   → ``["{var_name}_PSEUDO", "{var_name}_PRESENT"]``
    * Unknown PHI type → ``[]`` (completely dropped / strategy unknown)
    """
    if not is_phi:
        return [var_name]
    if phi_type == "date":
        suggested = (date_review.get(var_name) or {}).get("suggested_output_variable")
        return [suggested] if suggested else []
    if phi_type == "id":
        return [f"{var_name}_PSEUDO", f"{var_name}_PRESENT"]
    if phi_type is not None:
        logger.warning(
            "Unknown phi_type %r for %s — deidentified_as will be empty", phi_type, var_name
        )
    return []


# ============================================================================
# v3 merge logic
# ============================================================================


def _merge_sources_v3(
    extraction_vars: dict[str, _VarEntry],
    dict_vars: dict[str, _VarEntry],
) -> list[_VarEntry]:
    """Merge extraction and dictionary sources into the v3 23-field schema.

    Source precedence:
    * description: extraction (rich CRF annotations) > dictionary
    * coded_options: extraction > dictionary codelist resolution
    * data_type, core_status: dictionary only
    * form metadata + section info: extraction only
    """
    all_keys = sorted(set(extraction_vars.keys()) | set(dict_vars.keys()))
    merged: list[_VarEntry] = []

    for key in all_keys:
        ext = extraction_vars.get(key, {})
        dd = dict_vars.get(key, {})

        description = ext.get("description") or dd.get("description") or ""
        coded_options = ext.get("coded_options") or dd.get("coded_options")

        is_phi: bool = False
        phi_reason: str = ""
        phi_type: str | None = None
        date_kind: str | None = None
        anchor_rule: str | None = None
        suggested_output_variable: str | None = None
        approved_for_transform: bool | None = None
        date_group_by: str | None = None

        deidentified_as = [key]

        entry: _VarEntry = {
            "variable_name": key,
            "form_id": ext.get("form_id", ""),
            "form_name": ext.get("form_name") or dd.get("form", ""),
            "source_pdf": ext.get("source_pdf", ""),
            "form_version": ext.get("form_version", ""),
            "form_summary": ext.get("form_summary", ""),
            "section": ext.get("section"),
            "section_context": ext.get("section_context"),
            "description": description,
            "coded_options": coded_options,
            "depends_on": ext.get("depends_on"),
            "condition": ext.get("condition"),
            "data_type": dd.get("data_type", "unknown"),
            "core_status": dd.get("core_status", ""),
            "is_phi": is_phi,
            "phi_reason": phi_reason,
            "phi_type": phi_type,
            "date_kind": date_kind,
            "anchor_rule": anchor_rule,
            "suggested_output_variable": suggested_output_variable,
            "approved_for_transform": approved_for_transform,
            "date_group_by": date_group_by,
            "deidentified_as": deidentified_as,
        }
        merged.append(entry)

    return merged


# ============================================================================
# Public entry point
# ============================================================================


def build_variables_reference(
    trio_bundle_dir: Path,
    output_path: Path,
    jurisdiction: str = "IN",
    tmp_dir: Path | None = None,
    *,
    pdf_extractions_dir: Path | None = None,
    dictionary_dir: Path | None = None,
) -> dict[str, Any]:
    """Build unified ``variables.json`` from all available annotation sources.

    Parameters
    ----------
    trio_bundle_dir:
        Root of the trio bundle.
    output_path:
        Full path for the output ``variables.json``.
    jurisdiction:
        Retained for backward compatibility (default ``"IN"``); PHI
        classification has been retired, so this value is ignored.
    tmp_dir:
        Optional path to the project ``tmp/`` directory.  When provided,
        ``tmp/extracted_variables/`` is used as a fallback extraction source
        when the PDF extractions dir is empty.
    pdf_extractions_dir:
        Explicit path for PDF extraction JSON files.  When omitted, uses
        ``config.PDF_EXTRACTIONS_DIR`` then falls back to
        ``trio_bundle_dir / "pdfs"``.
    dictionary_dir:
        Explicit path for dictionary mapping files.  When omitted, uses
        ``config.DICTIONARY_JSON_OUTPUT_DIR`` then falls back to
        ``trio_bundle_dir / "dictionary"``.

    Returns
    -------
    dict
        Summary statistics of the build.
    """
    logger.info("Building unified variables reference (jurisdiction=%s)", jurisdiction)

    import config as _cfg  # deferred: keeps module-level import-time clean for test isolation

    # Resolve variable source directories.  Priority:
    #   1. Explicit parameter (from tests or custom invocations)
    #   2. config paths (production layout: trio_bundle/{pdfs,dictionary}/)
    if pdf_extractions_dir is not None:
        pdf_dir = pdf_extractions_dir
    else:
        pdf_dir = Path(_cfg.PDF_EXTRACTIONS_DIR)

    dd_dir = dictionary_dir if dictionary_dir is not None else Path(_cfg.DICTIONARY_JSON_OUTPUT_DIR)

    # Choose extraction source: prefer the trio bundle; fall back to tmp/extracted_variables/
    if pdf_dir.is_dir() and any(pdf_dir.glob("*_variables.json")):
        extraction_dir = pdf_dir
        logger.info("Extraction source: trio bundle PDF extractions (%s)", extraction_dir)
    elif tmp_dir is not None:
        extraction_dir = tmp_dir / "extracted_variables"
        logger.info("PDF extractions dir is empty; using tmp fallback: %s", extraction_dir)
    else:
        extraction_dir = pdf_dir
        logger.warning(
            "PDF extractions dir is empty and no tmp_dir provided — "
            "variables.json will lack CRF metadata"
        )

    # Load all sources
    extraction_vars = _load_extraction_variables_rich(extraction_dir)
    if not extraction_vars:
        logger.warning(
            "No extraction variables loaded from %s — output will lack all CRF metadata",
            extraction_dir,
        )
    codelists = _load_codelists(dd_dir)
    dict_vars = _load_dictionary_variables(dd_dir, codelists)

    # Merge into v3 schema
    merged = _merge_sources_v3(extraction_vars, dict_vars)
    logger.info(
        "Merged %d unique variables (%d extraction + %d dictionary)",
        len(merged),
        len(extraction_vars),
        len(dict_vars),
    )

    # Write variables.json (LLM-visible)
    atomic_write_json(output_path, merged)
    logger.info("Wrote %d variables to %s", len(merged), output_path)

    # Count source files for the return summary (no sidecar written).
    extraction_sources = (
        sorted(f.name for f in extraction_dir.glob("*_variables.json"))
        if extraction_dir.is_dir()
        else []
    )
    dict_sources = (
        sorted(
            d.name
            for d in dd_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name not in ("Codelists", "Notes")
        )
        if dd_dir.is_dir()
        else []
    )

    summary = {
        "total_variables": len(merged),
        "extraction_sources": len(extraction_sources),
        "dict_sources": len(dict_sources),
    }
    logger.info("Variables reference complete: %d total", len(merged))
    return summary
