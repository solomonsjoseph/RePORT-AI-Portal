"""Structured tool registry for the RePORT AI Portal AI Assistant system.

All read-side tools resolve every path through
``scripts.ai_assistant.file_access.validate_agent_read`` — the unified
agent-zone chokepoint. The permitted read zone is
``output/{STUDY}/trio_bundle/`` (PHI-scrubbed artifacts) plus
``output/{STUDY}/agent/`` (the agent's own analysis outputs,
and conversations). Telemetry lives under ``audit/`` and is
off-limits to the agent, so is raw data and staging. Writes (analysis
figures and narratives) are confined to ``output/{STUDY}/agent/`` via
``validate_agent_write``, with a narrower ``validate_sandbox_write``
for the ``exec_python`` path (LLM-generated code → ``agent/analysis/``
only). The pipeline-side ``assert_output_zone`` helper is still called as a
directory-level early-reject before glob iteration — it layers beneath the
unified validator, not instead of it.
Each tool is decorated with ``@tool`` so it is automatically registered
with the LangGraph ReAct agent.

Tools
-----
1.  search_variables — fuzzy search across the unified variables reference
2.  find_variable_candidates — always-returns-top-k ranked candidates for disambiguation
3.  get_variable_details — full metadata for a specific variable
4.  list_forms — list all CRF forms in the study (derived from published trio bundle)
5.  get_form_variables — list all variables belonging to a specific form
6.  query_dataset — structural query on a JSONL dataset
7.  get_dataset_stats — summary statistics for a dataset (record counts, columns)
8.  get_study_overview — high-level study summary (datasets, forms, variables)
9.  run_python_analysis — sandboxed code execution for statistical analysis
10. cross_reference_variables — cross-reference a variable across datasets + forms
11. run_study_analysis — deterministic epidemiological analysis
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

import config
from scripts.ai_assistant.file_access import (
    validate_agent_read,
    validate_agent_write,
)
from scripts.ai_assistant.phi_safe import (
    phi_safe_return,
    sanitise_traceback,
)
from scripts.ai_assistant.tool_cache import tool_cache
from scripts.security.secure_env import assert_output_zone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Pipeline-internal columns — filter from query results.
_INTERNAL_COLUMNS = frozenset(
    {
        "source_file",
        "_provenance",
        "_source_row",
        "_ingestion_ts",
    }
)

_DATE_VALUE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

_FORM_TOKEN_EXPANSIONS: dict[str, str] = {
    "ic": "index case",
    "hc": "household contact",
    "hhc": "household contact",
    "cxr": "chest x ray",
    "elig": "eligibility",
    "tx": "treatment",
    "fu": "follow up",
    "fua": "follow up a",
    "fub": "follow up b",
    "foa": "final outcome a",
    "fob": "final outcome b",
    "fsa": "final status a",
    "fsb": "final status b",
}


def _normalise_search_text(value: str) -> str:
    """Return a lowercase, token-spaced string for stable local matching."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return re.sub(r"[^a-z0-9]+", " ", spaced.lower()).strip()


def _expanded_form_text(value: str) -> str:
    """Expand study abbreviations in dataset/form names for concept matching."""
    base = _normalise_search_text(value)
    expansions = [_FORM_TOKEN_EXPANSIONS[t] for t in base.split() if t in _FORM_TOKEN_EXPANSIONS]
    return " ".join([base, *expansions]).strip()


def _query_terms(value: str, *, stop: set[str]) -> tuple[list[str], str]:
    words = [w for w in value.split() if w.lower() not in stop] or value.split()
    terms: list[str] = []
    for word in words:
        term = _normalise_search_text(word)
        if term.endswith("s") and len(term) > 3:
            term = term[:-1]
        if term:
            terms.append(term)
    return terms, " ".join(terms).strip()


def _term_variants(term: str) -> set[str]:
    variants = {term}
    if term.startswith("eligib"):
        variants.add("elig")
    if term in {"tuberculosis", "tb"}:
        variants.update({"tb", "mtb"})
    if term == "household":
        variants.update({"hc", "hhc", "hh"})
    if term in {"hc", "hhc", "hh"}:
        variants.update({"household", "contact"})
    if term == "contact":
        variants.update({"cont", "contact"})
    if term in {"chest", "xray", "ray"}:
        variants.add("cxr")
    if term in {"cavity", "cavitation"}:
        variants.add("cavit")
    if term == "treatment":
        variants.add("tx")
    if term == "follow":
        variants.add("fu")
    return variants


def _text_has_term(text: str, term: str) -> bool:
    tokens = set(text.split())
    return any(v in text or v in tokens for v in _term_variants(term))


def _dataset_label(stem: str) -> str:
    return _expanded_form_text(stem).title()


def _load_dataset_column_variables() -> list[dict[str, Any]]:
    """Expose published dataset columns as retrieval candidates.

    The per-form evidence packs may not enumerate every published column. The
    agent still needs to discover columns that are demonstrably present in the
    published trio bundle, so we build a metadata-only reference from JSONL
    headers without surfacing row values.
    """
    datasets_dir = config.TRIO_DATASETS_DIR
    cache_dir = str(datasets_dir.resolve())
    hit = tool_cache.get("_dataset_column_variables", datasets_dir=cache_dir)
    if hit is not None:
        try:
            loaded = json.loads(hit)
            if isinstance(loaded, list):
                return loaded  # type: ignore[return-value]
        except json.JSONDecodeError:
            pass

    assert_output_zone(datasets_dir)
    if not datasets_dir.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for path in sorted(datasets_dir.glob("*.jsonl")):
        try:
            validated = validate_agent_read(path)
            columns: set[str] = set()
            record_count = 0
            with open(validated, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        record_count += 1
                        columns.update(str(k) for k in rec if k not in _INTERNAL_COLUMNS)
        except OSError:
            logger.warning("Failed to inspect dataset schema: %s", path.name)
            continue

        form_name = _dataset_label(path.stem)
        out.extend(
            [
                {
                    "variable_name": column,
                    "form_id": "",
                    "form_name": form_name,
                    "dataset": path.stem,
                    "description": f"Dataset column {column} in {form_name}",
                    "data_type": "unknown",
                    "coded_options": "",
                    "is_phi": False,
                    "phi_type": "",
                    "source": "dataset_schema",
                    "record_count": record_count,
                }
                for column in sorted(columns)
            ]
        )

    tool_cache.put(
        "_dataset_column_variables",
        json.dumps(out, ensure_ascii=False),
        datasets_dir=cache_dir,
    )
    return out


def _combined_variable_reference() -> list[dict[str, Any]]:
    # Phase 5b: the unified variables.json pipeline was dead code (never
    # produced on disk and the loader silently returned []). The agent now
    # relies entirely on published dataset column schemas; the per-form
    # evidence packs and study_metadata_catalog.json are consumed by the
    # catalog-side tools, not this generic retrieval surface.
    return _load_dataset_column_variables()


# Conservative default quasi-identifier columns for Indo-VAP. The k-anon
# gate uses any of these that are actually present in the query result;
# missing columns are silently skipped (no false-positive blocking on
# datasets that don't carry that QI). Phase 4 will elevate this to a
# per-dataset entry in the data dictionary.
_DEFAULT_QUASI_IDENTIFIERS: tuple[str, ...] = (
    "AGE",
    "AGEY",
    "AGEM",
    "SEX",
    "IS_SEX",
    "IC_SEX",
    "HHC_SEX",
    "HC_SEX",
    "DISTRICT",
    "DIST",
    "IS_DIST",
    "IC_DIST",
)

# Conservative default sensitive attributes for l-diversity. Outcome /
# diagnosis columns whose value (e.g., "DIED", "TB+") could re-identify
# a small homogeneous equivalence class.
_DEFAULT_SENSITIVE_ATTRIBUTES: tuple[str, ...] = (
    "EE_DIED",
    "EE_DIEDTB",
    "TB_DX",
    "TBSTATUS",
    "OUTCOME",
    "DEATHCAUSE",
)


def _present_columns(rows: list[dict[str, Any]], candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of *candidates* that actually appears in any row."""
    seen: set[str] = set()
    for row in rows[:50]:  # sample is enough; column set is stable across rows
        seen.update(row.keys())
    return tuple(c for c in candidates if c in seen)


def _read_jsonl(path: Path, *, max_records: int = 0) -> list[dict[str, Any]]:
    """Read a JSONL file returning a list of records.

    If *max_records* > 0, stop after that many records.
    """
    validated = validate_agent_read(path)
    records: list[dict[str, Any]] = []
    with open(validated, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if max_records and len(records) >= max_records:
                break
    return records


def _surface_safe_records(
    rows: list[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    """Return row samples safe for LLM/tool transport.

    The trio bundle stores SANT-shifted clinical dates, but the generic PHI
    return gate correctly treats exact ISO date strings as blocking patterns.
    Row samples are only structural previews, so redact date-shaped values
    instead of letting one date suppress the whole tool response.
    """
    redacted_columns: set[str] = set()
    safe_rows: list[Mapping[str, Any]] = []
    for row in rows:
        safe: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str) and _DATE_VALUE_RE.match(value.strip()):
                safe[key] = "<DATE_SHIFTED>"
                redacted_columns.add(str(key))
            else:
                safe[key] = value
        safe_rows.append(safe)
    return safe_rows, sorted(redacted_columns)


# ============================================================================
# Short-query / conversational-shortcut guard
# ============================================================================
#
# Why this exists: the agent's ReAct loop routes every user turn through a
# tool. When a researcher types a greeting ("hi", "hello", "thanks"), the
# LLM obeys and calls `search_variables("hi")` or similar. The fuzzy
# substring matcher then surfaces any variable name or description
# containing the substring "hi" (HIV_STATUS, history-related fields, form
# sections with "Name" in them). The LLM paraphrases the hit and the user
# sees their greeting answered with a name-variable — a poor UX that also
# wastes an LLM turn. The guard catches this before any matching runs.
#
# NOTE: this is UX hygiene, not a security control. It does not replace
# `scripts.ai_assistant.phi_safe.guard_user_prompt`, which still runs on
# every incoming researcher prompt at the UI / CLI entry points and
# refuses blocking-tier PHI before the agent is invoked.

_MIN_QUERY_LENGTH = 3

_CONVERSATIONAL_STOPLIST: frozenset[str] = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "hii",
        "heyy",
        "hola",
        "thanks",
        "thank you",
        "ty",
        "thx",
        "ok",
        "okay",
        "cool",
        "nice",
        "got it",
        "help",
        "test",
        "try",
        "again",
        "no",
        "yes",
        "y",
        "n",
    }
)


def _query_looks_conversational(query: str) -> bool:
    """Return True when *query* is a greeting / acknowledgement / too-short.

    The agent should answer these directly without any tool call; see the
    CONVERSATIONAL WORLD rule in ``scripts/ai_assistant/agent_prompts.py``.
    """
    if not isinstance(query, str):
        return False
    stripped = query.strip()
    if len(stripped) < _MIN_QUERY_LENGTH:
        return True
    normalised = re.sub(r"[^a-z ]+", "", stripped.lower()).strip()
    return normalised in _CONVERSATIONAL_STOPLIST


_CONVERSATIONAL_REFUSAL_MESSAGE = (
    "That looks like a greeting rather than a study question — ask about a "
    'variable, form, dataset, cohort, or analysis (e.g. "show me TB outcome '
    'variables" or "how many subjects completed treatment?").'
)


# ============================================================================
# Tool 1: search_variables
# ============================================================================


@tool
@phi_safe_return
def search_variables(query: str) -> str:
    """Search study variables by name or description.

    Use this to find variables related to a concept (e.g. "tuberculosis",
    "age", "HIV", "chest x-ray"). Returns matching variable names with
    descriptions.

    Args:
        query: Search term — matches against variable_name and description.
            Queries shorter than 3 characters or matching a greeting
            stoplist (hi / hello / thanks / ok / …) return an explicit
            refusal so the LLM does not surface noisy substring hits.
    """
    if _query_looks_conversational(query):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    hit = tool_cache.get("search_variables", query=query)
    if hit is not None:
        return hit

    variables = _combined_variable_reference()
    if not variables:
        return "No variables reference found. Ensure trio_bundle/datasets/ is populated."

    # Weighted scoring: prefer exact phrase hits in variable names/descriptions
    # and keep the payload compact for smaller local models.
    stop = {"the", "a", "an", "of", "for", "and", "in", "is", "to", "or", "overall"}
    query_terms, query_phrase = _query_terms(query, stop=stop)
    scored: list[tuple[int, dict[str, str]]] = []
    for var in variables:
        name = var.get("variable_name", "") or ""
        desc = var.get("description", "") or ""
        form = var.get("form_name", "") or ""
        dataset = var.get("dataset", "") or ""
        section = var.get("section", "") or ""
        name_terms = [_normalise_search_text(name.replace("_", " "))]
        desc_terms = [_normalise_search_text(desc)]
        form_terms = [_expanded_form_text(" ".join([form, dataset, section]))]
        score = 0
        if query_phrase:
            if query_phrase == " ".join(name_terms):
                score += 50
            if query_phrase in " ".join(name_terms):
                score += 18
            if query_phrase in " ".join(desc_terms):
                score += 22
            if query_phrase in " ".join(form_terms):
                score += 8

        for term in query_terms:
            if _text_has_term(_normalise_search_text(name), term):
                score += 8
            if _text_has_term(" ".join(desc_terms), term):
                score += 10
            if _text_has_term(" ".join(form_terms), term):
                score += 3

        combined_terms = " ".join(name_terms + desc_terms)
        if query_terms and all(_text_has_term(combined_terms, term) for term in query_terms):
            score += 16
        if query_terms and all(_text_has_term(" ".join(desc_terms), term) for term in query_terms):
            score += 12
        if score > 0:
            scored.append(
                (
                    score,
                    {
                        "variable_name": name,
                        "form_name": var.get("form_name") or "",
                        "dataset": var.get("dataset") or "",
                        "source": var.get("source") or "dataset_schema",
                        "section": var.get("section") or "",
                        "description": desc,
                        "data_type": var.get("data_type", "unknown"),
                        "is_phi": str(var.get("is_phi", False)),
                        "phi_type": var.get("phi_type") or "",
                    },
                )
            )

    if not scored:
        return f"No variables found matching '{query}'."
    scored.sort(key=lambda x: (-x[0], len(x[1]["variable_name"]), x[1]["variable_name"]))
    matches = [item for _, item in scored[:12]]
    result = json.dumps(matches, indent=2, ensure_ascii=False)
    tool_cache.put("search_variables", result, query=query)
    return result


# ============================================================================
# Tool 2: get_variable_details
# ============================================================================


@tool
@phi_safe_return
def get_variable_details(variable_name: str) -> str:
    """Get full metadata for a specific study variable.

    Returns all 23 fields: variable_name, form_id, form_name, source_pdf,
    form_version, form_summary, section, section_context, description,
    coded_options, depends_on, condition, data_type, core_status, is_phi,
    phi_reason, phi_type, date_kind, anchor_rule, suggested_output_variable,
    approved_for_transform, date_group_by, deidentified_as.

    Args:
        variable_name: Exact variable name (case-insensitive).
    """
    hit = tool_cache.get("get_variable_details", variable_name=variable_name)
    if hit is not None:
        return hit

    variables = _combined_variable_reference()
    target = variable_name.upper()
    for var in variables:
        if var.get("variable_name", "").upper() == target:
            result = json.dumps(var, indent=2, ensure_ascii=False)
            tool_cache.put("get_variable_details", result, variable_name=variable_name)
            return result
    return f"Variable '{variable_name}' not found in the study reference."


# ============================================================================
# Tool 3: list_forms
# ============================================================================


@tool
@phi_safe_return
def list_forms() -> str:
    """List all CRF (Case Report Form) forms available in the study.

    Returns form names, versions, and variable counts.
    """
    hit = tool_cache.get("list_forms")
    if hit is not None:
        return hit

    variables = _combined_variable_reference()
    if not variables:
        return "No variables reference found. Ensure trio_bundle/datasets/ is populated."

    forms_dict: dict[str, dict[str, Any]] = {}
    for var in variables:
        form = var.get("form_name") or "Unknown"
        if form not in forms_dict:
            forms_dict[form] = {
                "form_name": form,
                "version": var.get("form_version", ""),
                "variable_count": 0,
            }
        forms_dict[form]["variable_count"] += 1

    if not forms_dict:
        return "No forms found."
    forms = sorted(forms_dict.values(), key=lambda f: f["form_name"])
    result = json.dumps(forms, indent=2, ensure_ascii=False)
    tool_cache.put("list_forms", result)
    return result


# ============================================================================
# Tool 4: get_form_variables
# ============================================================================


@tool
@phi_safe_return
def get_form_variables(form_name: str) -> str:
    """List all variables defined in a specific CRF form.

    Args:
        form_name: Form name (e.g. "1A Index Case Screening" or
            "Form 1A"). Partial match supported.
    """
    hit = tool_cache.get("get_form_variables", form_name=form_name)
    if hit is not None:
        return hit

    variables = _combined_variable_reference()
    if not variables:
        return "No variables reference found. Ensure trio_bundle/datasets/ is populated."

    # Word-split matching: rank forms by how many query words they contain.
    stop = {"form", "the", "a", "an", "of", "for", "and", "in", "-", "--"}
    query_terms, _ = _query_terms(form_name, stop=stop)

    # Group variables by form_name and score each form
    forms: dict[str, list[dict[str, Any]]] = {}
    for var in variables:
        fname = var.get("form_name") or "Unknown"
        forms.setdefault(fname, []).append(var)

    best_form: str | None = None
    best_score = 0
    for fname in forms:
        searchable = _expanded_form_text(fname)
        dataset_names = {str(v.get("dataset") or "") for v in forms[fname] if v.get("dataset")}
        if dataset_names:
            searchable = " ".join([searchable, *(_expanded_form_text(n) for n in dataset_names)])
        score = sum(1 for term in query_terms if _text_has_term(searchable, term))
        if score > best_score:
            best_score = score
            best_form = fname

    if best_form and best_score > 0:
        matched_vars = forms[best_form]
        result = {
            "form_name": best_form,
            "version": matched_vars[0].get("form_version", ""),
            "summary": matched_vars[0].get("form_summary", ""),
            "variables": [
                {
                    "name": v.get("variable_name", ""),
                    "description": v.get("description", ""),
                    "values": v.get("coded_options"),
                    "depends_on": v.get("depends_on"),
                    "condition": v.get("condition"),
                }
                for v in matched_vars
            ],
        }
        result_str = json.dumps(result, indent=2, ensure_ascii=False)
        tool_cache.put("get_form_variables", result_str, form_name=form_name)
        return result_str

    return f"No form found matching '{form_name}'."


# ============================================================================
# Tool 5: query_dataset
# ============================================================================


@tool
@phi_safe_return
def query_dataset(
    dataset_name: str,
    columns: str | None = None,
    filter_column: str | None = None,
    filter_value: str | None = None,
    limit: int = 20,
) -> str:
    """Query a processed study dataset (structural access only).

    Returns column names and sample records from a JSONL dataset.
    All datasets are processed in accordance with the study's data
    governance and regulatory anonymization protocol.

    Args:
        dataset_name: Dataset filename (e.g. "1A_ICScreening" or
            "1A_ICScreening.jsonl"). Partial match supported.
        columns: Comma-separated list of columns to include. If None, all columns.
        filter_column: Column name to filter on (optional).
        filter_value: Value to match in filter_column (optional, case-insensitive).
        limit: Maximum number of records to return (default 20, max 100).
    """
    datasets_dir = config.TRIO_DATASETS_DIR
    assert_output_zone(datasets_dir)

    if not datasets_dir.is_dir():
        return "No datasets directory found."

    limit = min(max(limit, 1), 100)

    # Find matching dataset file
    target = dataset_name.removesuffix(".jsonl")
    pat = re.compile(re.escape(target), re.IGNORECASE)
    matched_file: Path | None = None
    for f in sorted(datasets_dir.glob("*.jsonl")):
        if pat.search(f.stem):
            matched_file = f
            break

    if matched_file is None:
        return f"No dataset found matching '{dataset_name}'."

    # Read all records for accurate totals and cross-record filtering
    all_records = _read_jsonl(matched_file)
    if not all_records:
        return f"Dataset '{matched_file.name}' is empty."

    real_total = len(all_records)
    all_columns = sorted({k for r in all_records for k in r} - _INTERNAL_COLUMNS)

    # Apply column filter
    col_set: set[str] | None = None
    if columns:
        col_set = {c.strip() for c in columns.split(",")}

    # Apply row filter on all records
    filtered = all_records
    if filter_column and filter_value:
        fv_lower = filter_value.lower()
        filtered = [r for r in all_records if str(r.get(filter_column, "")).lower() == fv_lower]

    # Project columns and limit
    results: list[dict[str, Any]] = []
    for rec in filtered[:limit]:
        if col_set:
            results.append(
                {k: v for k, v in rec.items() if k in col_set and k not in _INTERNAL_COLUMNS}
            )
        else:
            results.append({k: v for k, v in rec.items() if k not in _INTERNAL_COLUMNS})

    # k-anonymity + l-diversity gate (Phase 3.A + 3.B). Run on the
    # projected ``results`` (post-column-filter, post-row-filter) using
    # whichever default QI / sensitive columns are present. If blocked,
    # surface aggregate-only metadata + a clear refusal so the LLM can
    # respond with bands rather than rows.
    from scripts.ai_assistant.phi_safe import guard_rows_with_kanon_and_ldiv

    qi_present = _present_columns(results, _DEFAULT_QUASI_IDENTIFIERS)
    sens_present = _present_columns(results, _DEFAULT_SENSITIVE_ATTRIBUTES)

    safe_records: list[Mapping[str, Any]] = list(results)
    kanon_violation: dict[str, Any] | None = None

    if qi_present and results:
        gated, kanon_res, ldiv_res = guard_rows_with_kanon_and_ldiv(
            results,
            quasi_identifiers=qi_present,
            sensitive_attributes=sens_present or None,
            tool_name="query_dataset",
        )
        if kanon_res.blocked or (ldiv_res is not None and ldiv_res.blocked):
            safe_records = []
            kanon_violation = {
                "gate": "kanon" if kanon_res.blocked else "l_diversity",
                "k": 5,
                "l": 2 if ldiv_res is not None else None,
                "smallest_class_size": kanon_res.smallest_class_size,
                "smallest_diversity": ldiv_res.smallest_diversity if ldiv_res else None,
                "quasi_identifiers": list(qi_present),
                "sensitive_attributes": list(sens_present),
                "message": (
                    "Row-level surface suppressed: an equivalence class of size "
                    f"{kanon_res.smallest_class_size} would be re-identifiable. "
                    "Re-query with broader bins (age band / district) or aggregate "
                    "via get_dataset_stats / cross_reference_variables."
                ),
            }
        else:
            safe_records = list(gated)

    safe_records, date_values_redacted = _surface_safe_records(safe_records)

    return json.dumps(
        {
            "dataset": matched_file.stem,
            "total_records": real_total,
            "rows_matching_filter": len(filtered) if filter_column else real_total,
            "returned": len(safe_records),
            "available_columns": all_columns,
            "records": safe_records,
            "date_values_redacted": date_values_redacted,
            "kanon_violation": kanon_violation,
        },
        indent=2,
        ensure_ascii=False,
    )


# ============================================================================
# Tool 6: get_dataset_stats
# ============================================================================


@tool
@phi_safe_return
def get_dataset_stats(dataset_name: str | None = None) -> str:
    """Get summary statistics for study datasets.

    Returns record counts, column counts, and column names for each dataset.
    If dataset_name is provided, returns stats for that dataset only.
    Otherwise returns stats for all datasets.

    Args:
        dataset_name: Optional dataset name to filter (partial match).
    """
    hit = tool_cache.get("get_dataset_stats", dataset_name=dataset_name)
    if hit is not None:
        return hit

    datasets_dir = config.TRIO_DATASETS_DIR
    assert_output_zone(datasets_dir)

    if not datasets_dir.is_dir():
        return "No datasets directory found."

    files = sorted(datasets_dir.glob("*.jsonl"))
    if dataset_name:
        pat = re.compile(re.escape(dataset_name), re.IGNORECASE)
        files = [f for f in files if pat.search(f.stem)]

    if not files:
        msg = (
            f"No datasets found matching {dataset_name!r}."
            if dataset_name
            else "No datasets found."
        )
        return msg

    stats: list[dict[str, Any]] = []
    total_records = 0
    for f in files:
        record_count = 0
        all_columns: set[str] = set()
        with open(validate_agent_read(f), encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    all_columns.update(rec.keys())
                    record_count += 1
                except json.JSONDecodeError:
                    continue
        visible_columns = all_columns - _INTERNAL_COLUMNS
        stats.append(
            {
                "dataset": f.stem,
                "records": record_count,
                "column_count": len(visible_columns),
                "columns": sorted(visible_columns),
            }
        )
        total_records += record_count

    result = json.dumps(
        {
            "total_datasets": len(stats),
            "total_records": total_records,
            "datasets": stats,
        },
        indent=2,
        ensure_ascii=False,
    )
    tool_cache.put("get_dataset_stats", result, dataset_name=dataset_name)
    return result


# ============================================================================
# Tool 7: get_study_overview
# ============================================================================


@tool
@phi_safe_return
def get_study_overview() -> str:
    """Get a high-level overview of the study.

    Returns counts of datasets, forms, variables, and regulated data elements.
    Use this as a starting point to understand what data is available.
    """
    hit = tool_cache.get("get_study_overview")
    if hit is not None:
        return hit

    # Variables summary — sourced from the published trio dataset schemas
    # (per-form evidence packs carry PHI metadata; see Phase 5b notes).
    variables = _combined_variable_reference()
    phi_count = sum(1 for v in variables if v.get("is_phi"))

    # Dataset counts
    datasets_dir = config.TRIO_DATASETS_DIR
    dataset_files: list[str] = []
    total_records = 0
    if datasets_dir.is_dir():
        for f in sorted(datasets_dir.glob("*.jsonl")):
            validate_agent_read(f)
            with open(f, encoding="utf-8") as fh:
                count = sum(1 for line in fh if line.strip())
            dataset_files.append(f.name)
            total_records += count

    # Forms count (derived from unique form_name values in the combined
    # variable reference; the per-form evidence packs are the canonical
    # source for CRF metadata, but this overview only needs a count.)
    form_count = len({v.get("form_name") for v in variables if v.get("form_name")})

    overview = {
        "study_name": config.STUDY_NAME,
        "total_variables": len(variables),
        "regulated_data_elements": phi_count,
        "non_phi_variables": len(variables) - phi_count,
        "total_datasets": len(dataset_files),
        "total_records": total_records,
        "total_crf_forms": form_count,
        "datasets": [f.removesuffix(".jsonl") for f in dataset_files],
    }
    result = json.dumps(overview, indent=2, ensure_ascii=False)
    tool_cache.put("get_study_overview", result)
    return result


# ============================================================================
# Tool 8: run_python_analysis — sandboxed execution
# ============================================================================

# Sandbox security boundaries (hardcoded import allowlist + dunder block list)
# now live in ``scripts/ai_assistant/sandbox/runner.py`` so they stay co-located
# with the code that enforces them. Operational tunables (timeout, memory,
# figure count, persistence toggle) come from ``config.ANALYSIS_*`` and
# ``config.SANDBOX_*``.


def _load_dataframes() -> dict[str, Any]:
    """Pre-load JSONL datasets as pandas DataFrames.

    Returns a dict mapping ``df_{stem}`` names to DataFrames.

    Retained for callers that need the in-memory DataFrames directly (e.g.,
    ``run_study_analysis``). The sandboxed ``run_python_analysis`` now uses
    :func:`_discover_trio_dataframe_paths` instead — the child process loads
    the DataFrames itself from the path manifest.
    """
    import pandas as pd

    datasets_dir = config.TRIO_DATASETS_DIR
    if not datasets_dir.is_dir():
        return {}

    frames: dict[str, Any] = {}
    for f in sorted(datasets_dir.glob("*.jsonl")):
        try:
            validate_agent_read(f)
            df = pd.read_json(f, lines=True)
            # Sanitise stem for use as Python variable name
            var_name = "df_" + re.sub(r"[^a-zA-Z0-9_]", "_", f.stem)
            frames[var_name] = df
        except Exception:
            logger.debug("Failed to load %s as DataFrame", f.name)
            continue
    return frames


def _discover_trio_dataframe_paths() -> dict[str, str]:
    """Discover trio JSONL files and return ``{var_name: path}`` for the sandbox.

    The sandbox child process loads the DataFrames itself; this avoids
    serialising/deserialising potentially large DataFrames across the subprocess
    boundary. Each path is validated through ``validate_agent_read`` before
    being included so that an unexpected symlink or sibling-prefix file in
    ``TRIO_DATASETS_DIR`` cannot leak into the sandbox's read allow-list.
    """
    datasets_dir = config.TRIO_DATASETS_DIR
    if not datasets_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for f in sorted(datasets_dir.glob("*.jsonl")):
        try:
            validate_agent_read(f)
            var_name = "df_" + re.sub(r"[^a-zA-Z0-9_]", "_", f.stem)
            out[var_name] = str(f.resolve())
        except Exception:
            logger.debug("Skipping %s (failed validate_agent_read)", f.name)
            continue
    return out


def _safe_import_check(code: str) -> str | None:
    """Return an error message if ``code`` violates the sandbox AST guards.

    Thin shim over :func:`scripts.ai_assistant.sandbox.runner._ast_pre_check`.
    Kept for backward compatibility with ``tests/test_agent_tools.py``; the
    canonical guard now runs inside the sandbox subprocess so that even a
    direct call to a sandbox bypass would not skip it.
    """
    from scripts.ai_assistant.sandbox.runner import (
        SandboxRejectionError,
        _ast_pre_check,
    )

    try:
        _ast_pre_check(code)
    except SyntaxError as exc:
        return f"Syntax error in code: {exc}"
    except SandboxRejectionError as exc:
        return str(exc)
    return None


@tool
@phi_safe_return
def run_python_analysis(code: str) -> str:
    """Execute Python code for statistical analysis on study datasets.

    Runs in an isolated subprocess sandbox with pre-loaded DataFrames
    sourced from the study's catalog and current (de-identified)
    dataset. The sandbox cannot read API keys from ``os.environ``,
    cannot escape its narrow output directory, and is wall-clock and
    (on Linux) memory bounded. See
    ``docs/sphinx/developer_guide/sandbox.rst`` for the full threat model.

    **Available DataFrames** (named ``df_<dataset>``, e.g. ``df_1A_ICScreening``):
    Call ``print(list(locals().keys()))`` to see all available DataFrames.

    **Allowed imports:** pandas, numpy, scipy, statsmodels, plotly,
    matplotlib, collections, math, statistics, re, json, datetime, itertools.

    **Pre-imported:** ``pd`` (pandas), ``np`` (numpy), ``px`` (plotly.express),
    ``go`` (plotly.graph_objects).

    **Prefer Plotly** for interactive charts: ``fig = px.bar(...); fig.show()``.
    Matplotlib is available as a fallback for static plots.

    **Limits:** ``ANALYSIS_TIMEOUT`` seconds wall-clock, ``ANALYSIS_MAX_OUTPUT``
    bytes of stdout, ``ANALYSIS_MAX_FIGURES`` figures, ``SANDBOX_MAX_MEMORY_MB``
    address-space cap (Linux). All configurable via env vars.

    **Persistence:** when ``SANDBOX_PERSIST_CODE`` is true (default), the
    executed code is also saved as a runnable ``.py`` file under
    ``output/{STUDY}/agent/analysis/code/`` with a header explaining how to
    re-run it locally — surfaced to the UI via ``<RPLN_CODE:...>`` markers.

    Args:
        code: Python code to execute. Use print() for output.
            Call fig.show() for Plotly figures or just create matplotlib figures.
    """
    from scripts.ai_assistant import sandbox

    output_dir = config.AGENT_OUTPUT_DIR
    assert_output_zone(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df_paths = _discover_trio_dataframe_paths()

    result = sandbox.run_in_subprocess(
        code,
        df_paths=df_paths,
        output_dir=output_dir,
        timeout_s=config.ANALYSIS_TIMEOUT,
        max_memory_mb=config.SANDBOX_MAX_MEMORY_MB,
        max_procs=config.SANDBOX_MAX_PROCS,
        max_files=config.SANDBOX_MAX_FILES,
        persist_code=config.SANDBOX_PERSIST_CODE,
        max_output_bytes=config.ANALYSIS_MAX_OUTPUT,
        max_figures=config.ANALYSIS_MAX_FIGURES,
    )

    return _format_sandbox_result_for_agent(result)


def _format_sandbox_result_for_agent(result: Any) -> str:
    """Format a :class:`SandboxResult` into the marker-bearing string the
    streaming UI parses (``<RPLN_PLOTLY:>``, ``<RPLN_FIGURE:>``, ``<RPLN_CODE:>``).

    Friendly error envelopes preserve the previous tone: ``**Import Error:**``,
    ``**Security Error:**``, ``**Timeout:**``, ``**Runtime Error:**``.
    """
    # Pre-execution rejection (AST guard, blocked import, blocked builtin).
    if result.exit_code == 2:
        first_line = (result.stderr or "").splitlines()[0] if result.stderr else "rejected"
        if "Import not allowed" in first_line:
            return f"**Import Error:** {first_line}"
        if "not allowed in the sandbox" in first_line:
            return f"**Security Error:** {first_line}"
        if "Syntax error" in first_line:
            return f"**Syntax Error:** {first_line}"
        return f"**Sandbox Rejection:** {first_line}"

    if result.timed_out:
        return f"**Timeout:** Code execution exceeded {config.ANALYSIS_TIMEOUT}s limit."

    if result.oom_killed:
        return f"**Memory Exceeded:** Code exceeded {config.SANDBOX_MAX_MEMORY_MB}MB cap."

    if result.exit_code != 0:
        # Runtime error inside user code: stderr ends with the traceback.
        tail = "\n".join((result.stderr or "").splitlines()[-3:]).strip()
        if not tail:
            tail = f"sandbox exited with code {result.exit_code}"
        return f"**Runtime Error:** {tail}"

    # Success path — stdout + figure + code markers.
    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.strip())

    plotly_paths = [p for p in result.figure_paths if p.suffix == ".json"]
    matplotlib_paths = [p for p in result.figure_paths if p.suffix == ".png"]
    total_figs = len(plotly_paths) + len(matplotlib_paths)
    if total_figs:
        parts.append(f"\n[{total_figs} figure(s) generated]")
        parts.extend(f"\n<RPLN_PLOTLY:{p}>" for p in plotly_paths)
        parts.extend(f"\n<RPLN_FIGURE:{p}>" for p in matplotlib_paths)

    parts.extend(f"\n<RPLN_CODE:{code_path}>" for code_path in result.code_paths)

    if not parts:
        parts.append("Code executed successfully (no output).")

    formatted = "\n".join(parts)
    logger.info(
        "run_python_analysis: %d chars stdout, %d plotly, %d matplotlib, %d code-saved",
        len(result.stdout),
        len(plotly_paths),
        len(matplotlib_paths),
        len(result.code_paths),
    )
    return formatted


# ============================================================================
# Tool 9: cross_reference_variables
# ============================================================================


@tool
@phi_safe_return
def cross_reference_variables(variable_name: str) -> str:
    """Cross-reference a variable across all datasets and forms.

    Shows which datasets contain this column, record counts, completeness
    rates, and the corresponding CRF form definition.  Use this when you want
    to know *where* a variable appears in the study and how populated it is.

    Args:
        variable_name: Variable name to cross-reference (case-insensitive, partial match).
    """
    hit = tool_cache.get("cross_reference_variables", variable_name=variable_name)
    if hit is not None:
        return hit

    pat = re.compile(re.escape(variable_name), re.IGNORECASE)

    # 1. Variable definitions from reference (dataset column schemas)
    variables = _combined_variable_reference()
    matching_vars = [
        {
            "variable_name": v.get("variable_name"),
            "form_name": v.get("form_name"),
            "description": v.get("description"),
            "is_phi": v.get("is_phi"),
            "phi_type": v.get("phi_type"),
            "data_type": v.get("data_type"),
            "deidentified_as": v.get("deidentified_as"),
        }
        for v in variables
        if pat.search(v.get("variable_name", ""))
    ][:20]

    # 2. Dataset presence: scan first record for column discovery, then full scan
    datasets_dir = config.TRIO_DATASETS_DIR
    assert_output_zone(datasets_dir)
    dataset_presence: list[dict[str, Any]] = []

    if datasets_dir.is_dir():
        for f in sorted(datasets_dir.glob("*.jsonl")):
            try:
                f_validated = validate_agent_read(f)
            except PermissionError:
                continue
            # Quick peek at first record
            first_rec: dict[str, Any] = {}
            try:
                with open(f_validated, encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            first_rec = json.loads(line)
                            break
            except (OSError, json.JSONDecodeError):
                continue

            matching_cols = [c for c in first_rec if pat.search(c)]
            if not matching_cols:
                continue

            # Full scan for counts
            total = 0
            populated = 0
            try:
                with open(f_validated, encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                            total += 1
                            if any(rec.get(col) not in (None, "", "nan") for col in matching_cols):
                                populated += 1
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

            # Apply small-cell suppression (Phase 3.A) so a population /
            # completeness pair smaller than k=5 is not surfaced as an
            # exact count — it becomes the suppressed label "<5". The
            # completeness percentage is recomputed against the masked
            # numerator so it doesn't reveal the suppressed count via
            # arithmetic.
            from scripts.security.kanon_gate import mask_small_cell

            populated_safe = mask_small_cell(populated, k=5)
            total_safe = mask_small_cell(total, k=5)
            completeness_pct: Any
            if isinstance(populated_safe, int) and isinstance(total_safe, int) and total_safe:
                completeness_pct = round(populated_safe / total_safe * 100, 1)
            else:
                completeness_pct = "<5"

            dataset_presence.append(
                {
                    "dataset": f.stem,
                    "matching_columns": matching_cols,
                    "total_records": total_safe,
                    "populated_records": populated_safe,
                    "completeness_pct": completeness_pct,
                }
            )

    result_data = {
        "query": variable_name,
        "variable_definitions": matching_vars,
        "dataset_presence": dataset_presence,
        "total_datasets_with_variable": len(dataset_presence),
    }
    result_str = json.dumps(result_data, indent=2, ensure_ascii=False)
    tool_cache.put("cross_reference_variables", result_str, variable_name=variable_name)
    return result_str


# ============================================================================
# Tool 10: run_study_analysis
# ============================================================================


# Map friendly outcome phrasings to the canonical enum the analytical engine
# expects. Small LLMs routinely write "TB recurrence" instead of "recurrence";
# normalising here avoids a tool round-trip the user pays for in latency.
_OUTCOME_ALIASES: dict[str, str] = {
    "recurrence": "recurrence",
    "tb recurrence": "recurrence",
    "recurrent tb": "recurrence",
    "relapse": "recurrence",
    "tb relapse": "recurrence",
    "failure": "recurrence",
    "tb failure": "recurrence",
    "incident_tb": "incident_tb",
    "incident tb": "incident_tb",
    "tb incidence": "incident_tb",
    "incidence of tb": "incident_tb",
    "progression": "incident_tb",
    "tb progression": "incident_tb",
}


def _normalise_outcome(outcome: str, cohort: str) -> str:
    """Accept friendly outcome names; fall back to the cohort default if empty."""
    key = outcome.strip().lower()
    if not key:
        return "recurrence" if cohort == "cohort_a" else "incident_tb"
    return _OUTCOME_ALIASES.get(key, outcome)


@tool
@phi_safe_return
def run_study_analysis(
    cohort: str,
    outcome: str = "",
    predictors: str = "",
    analysis_types: str = "",
    plot_types: str = "",
) -> str:
    """Run a complete statistical analysis on study data.

    This tool executes pre-built, deterministic epidemiological analyses for
    outcome relationships, predictor effects, odds ratios, and regression-style
    questions. No arbitrary code is executed — all analyses are pre-validated
    functions. Metadata questions about which variables, fields, forms, or
    coded values exist are usually better served by the variable/form tools,
    but the agent remains free to choose the tool path.

    Args:
        cohort: Which cohort to analyze — "cohort_a" (index cases) or "cohort_b" (household contacts).
        outcome: Canonical outcome enum. For cohort_a use "recurrence" (aliases: "tb recurrence", "relapse", "failure"). For cohort_b use "incident_tb" (aliases: "incident tb", "progression"). Leave empty for the cohort default.
        predictors: Comma-separated predictor names. Default set is smoking, diabetes, bmi, alcohol, age, sex. Additional available predictors (must be named explicitly, not in the default set): malnutrition (BMI<18.5 binary, depends on bmi).
        analysis_types: Comma-separated analysis types from: univariate, multivariate, interaction, descriptive. Default: all.
        plot_types: Comma-separated plot types from: violin, scatter, interaction_violin, interaction_scatter. Default: all.
    """
    import traceback

    from scripts.ai_assistant.analytical_engine import run_full_analysis
    from scripts.ai_assistant.study_knowledge import StudyKnowledge

    if not cohort:
        return "Missing required parameter 'cohort'. Use 'cohort_a' (index cases) or 'cohort_b' (household contacts)."

    outcome = _normalise_outcome(outcome, cohort)

    try:
        knowledge = StudyKnowledge()
        data_dir = config.TRIO_DATASETS_DIR
        output_dir = config.AGENT_OUTPUT_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        pred_list = [p.strip() for p in predictors.split(",") if p.strip()] if predictors else None
        atype_list = (
            [a.strip() for a in analysis_types.split(",") if a.strip()] if analysis_types else None
        )
        ptype_list = [p.strip() for p in plot_types.split(",") if p.strip()] if plot_types else None

        result = run_full_analysis(
            knowledge=knowledge,
            data_dir=data_dir,
            output_dir=output_dir,
            cohort_id=cohort,
            outcome=outcome or None,
            predictors=pred_list,
            analysis_types=atype_list,
            plot_types=ptype_list,
            timeout=config.ANALYSIS_TIMEOUT,
        )

        # Soft caveat when events are low enough that ORs should be
        # reported with explicit power warnings.
        events_per_variable = result.events / max(len(pred_list or []) or 6, 1)
        underpowered = result.events < 10 or events_per_variable < 5

        # Save full narrative to disk for direct UI rendering
        narrative_path = validate_agent_write(output_dir / f"{cohort}_narrative.md")
        full_parts: list[str] = [result.narrative]
        full_parts.extend(f"<RPLN_PLOTLY:{fig_path}>" for fig_path in result.interactive_figures)
        full_parts.extend(f"<RPLN_FIGURE:{fig_path}>" for fig_path in result.figures)
        full_narrative = "\n\n".join(full_parts)
        narrative_path.write_text(full_narrative, encoding="utf-8")

        # Build a SHORT summary for the LLM (stays within context limits)
        sig_uni = []
        if result.univariate is not None:
            for _, row in result.univariate.iterrows():
                if row.get("significant"):
                    sig_uni.append(
                        f"  - {row['predictor']}: OR={row['OR']:.3f}, p={row['p_value']:.4f}"
                    )

        mv_retained = []
        if result.multivariate and "retained_predictors" in result.multivariate:
            mv_retained = result.multivariate["retained_predictors"]

        sig_int = []
        if result.interaction is not None:
            for _, row in result.interaction.iterrows():
                if row.get("significant"):
                    sig_int.append(
                        f"  - {row['factor']}x{row['moderator']}: p={row['interaction_p']:.4f}"
                    )

        figure_count = len(result.interactive_figures) + len(result.figures)
        summary_lines = [
            f"Analysis complete: {result.cohort_name} - {result.outcome}.",
            (
                f"Evidence: N={result.n}, events={result.events} "
                f"({result.events / result.n * 100:.1f}% rate), figures={figure_count}."
            ),
        ]
        if result.events < 5:
            summary_lines.append(
                "Inferential models were not run: fewer than 5 outcome events are present. "
                "Use the descriptive tables and plots only."
            )
            summary_lines.append("")
            summary_lines.append(
                "Detailed descriptive tables, plots, and caveats are rendered below."
            )
            summary_lines.append(f"<RPLN_ANALYSIS:{narrative_path}>")
            return "\n".join(summary_lines)
        if underpowered:
            summary_lines.append(
                f"Caveat: underpowered analysis; events={result.events}, "
                f"events/variable={events_per_variable:.1f} (target >=10, floor >=5). "
                "Multivariate odds ratios may be unstable; interpret point estimates cautiously."
            )
        if sig_uni:
            summary_lines.append("Significant univariate predictors:")
            summary_lines.extend(sig_uni)
        else:
            summary_lines.append("No significant univariate predictors (p<0.05).")

        if mv_retained:
            summary_lines.append(f"Multivariate retained: {', '.join(mv_retained)}.")
        elif result.multivariate and "error" in result.multivariate:
            summary_lines.append(f"Multivariate: {result.multivariate['error']}.")

        if sig_int:
            summary_lines.append("Significant interactions:")
            summary_lines.extend(sig_int)

        summary_lines.append("")
        summary_lines.append("Detailed model tables, plots, and narrative are rendered below.")
        summary_lines.append(f"<RPLN_ANALYSIS:{narrative_path}>")

        return "\n".join(summary_lines)

    except TimeoutError as e:
        logger.warning("run_study_analysis timed out: %s", e)
        return f"Analysis timed out: {e}\nTry reducing the analysis scope (fewer predictors or analysis types)."
    except Exception as e:
        from scripts.utils import errors as _rpln_err

        err = _rpln_err.wrap(
            e,
            stage="agent.tool",
            operation="run_study_analysis",
            hint="See traceback; narrow predictors or analysis types and retry.",
        )
        logger.error(err.as_log_block())
        return (
            f"Analysis failed: {type(e).__name__}: "
            f"{sanitise_traceback(str(e))}\n"
            f"{sanitise_traceback(traceback.format_exc())}"
        )


# ============================================================================
# Tool 11: find_variable_candidates — fuzzy top-k disambiguator
# ============================================================================


def _score_variable(var: dict[str, Any], query_terms: list[str], query_phrase: str) -> int:
    """Weighted score of one variable record vs a normalised query.

    Kept identical to ``search_variables`` weights so behaviour stays consistent
    when tools are chained.
    """
    name = var.get("variable_name") or ""
    desc = var.get("description") or ""
    form = var.get("form_name") or ""
    dataset = var.get("dataset") or ""
    section = var.get("section") or ""

    name_n = _normalise_search_text(name.replace("_", " "))
    desc_n = _normalise_search_text(desc)
    form_n = _expanded_form_text(" ".join([form, dataset, section]))

    score = 0
    if query_phrase:
        if query_phrase == name_n:
            score += 50
        if query_phrase in name_n:
            score += 18
        if query_phrase in desc_n:
            score += 22
        if query_phrase in form_n:
            score += 8

    for term in query_terms:
        if _text_has_term(_normalise_search_text(name), term):
            score += 8
        if _text_has_term(desc_n, term):
            score += 10
        if _text_has_term(form_n, term):
            score += 3

    combined = name_n + " " + desc_n
    if query_terms and all(_text_has_term(combined, term) for term in query_terms):
        score += 16
    if query_terms and all(_text_has_term(desc_n, term) for term in query_terms):
        score += 12
    if query_phrase and score == 0:
        fuzzy_text = " ".join([name_n, desc_n, form_n])
        if (
            SequenceMatcher(
                None, query_phrase, fuzzy_text[: max(len(query_phrase) * 3, 40)]
            ).ratio()
            >= 0.55
        ):
            score += 6
    return score


@tool
@phi_safe_return
def find_variable_candidates(description: str, k: int = 3) -> str:
    """Find the top-k most likely variables for a natural-language description.

    Unlike ``search_variables`` (which returns up to 12 keyword-style matches),
    this tool ALWAYS returns the ``k`` best candidates (default 3) ranked by
    confidence — even when no single variable is an obvious match. Use this
    whenever the user's phrasing is ambiguous ("the age thing on the SAE form",
    "smoking status", "date of death") and you want the user to pick.

    The response is designed for the agent to present to the user as a
    numbered shortlist: "I found these three candidates — which one did you
    mean?". When confidence is high (>= 0.8) on the top hit and the second
    hit trails by a wide gap, you may proceed without asking.

    Args:
        description: Natural-language description of the variable the user is
            asking about. Can be a phrase, a sentence, or a partial variable name.
        k: Number of candidates to return. Default 3. Clamped to [1, 10].

    Returns:
        JSON string with keys ``query``, ``count``, ``candidates``. Each
        candidate has: ``rank`` (1-indexed), ``variable_name``, ``form_id``,
        ``form_name``, ``description``, ``data_type``, ``coded_options``
        (when short), and ``confidence`` in [0, 1].

    Conversational / too-short inputs (greetings like "hi", <3-char
    strings) return an explicit refusal so the LLM does not surface
    noisy substring hits for small-talk.
    """
    if _query_looks_conversational(description):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    k = max(1, min(int(k), 10))
    hit = tool_cache.get("find_variable_candidates", description=description, k=k)
    if hit is not None:
        return hit

    variables = _combined_variable_reference()
    if not variables:
        return "No variables reference found. Ensure trio_bundle/datasets/ is populated."

    stop = {"the", "a", "an", "of", "for", "and", "in", "is", "to", "or", "on", "overall"}
    query_terms, query_phrase = _query_terms(description, stop=stop)

    scored: list[tuple[int, dict[str, Any]]] = []
    for var in variables:
        score = _score_variable(var, query_terms, query_phrase)
        if score > 0:
            scored.append((score, var))

    scored.sort(
        key=lambda x: (
            -x[0],
            len(x[1].get("variable_name") or ""),
            x[1].get("variable_name") or "",
        ),
    )
    top = scored[:k]

    # Confidence normalisation: divide by the max possible score we observed
    # in this run so the top hit is always relative. Saturate to 1.0 above a
    # strong-match threshold (score >= 50 = phrase + all-terms + name/desc hit).
    max_score = max((s for s, _ in top), default=0)
    denom = max(max_score, 50)

    candidates: list[dict[str, Any]] = []
    for rank, (score, var) in enumerate(top, start=1):
        coded = var.get("coded_options") or ""
        # Keep coded_options only if short — helps the user disambiguate yes/no
        # and enum columns without blowing up the payload.
        if isinstance(coded, str) and len(coded) > 240:
            coded = coded[:240].rstrip() + "…"
        candidates.append(
            {
                "rank": rank,
                "variable_name": var.get("variable_name") or "",
                "form_id": var.get("form_id") or "",
                "form_name": var.get("form_name") or "",
                "dataset": var.get("dataset") or "",
                "source": var.get("source") or "dataset_schema",
                "description": var.get("description") or "",
                "data_type": var.get("data_type") or "unknown",
                "coded_options": coded,
                "confidence": round(min(score / denom, 1.0), 3),
            }
        )

    payload = {
        "query": description,
        "count": len(candidates),
        "candidates": candidates,
        "low_confidence": bool(candidates and candidates[0]["confidence"] < 0.4),
    }
    result = json.dumps(payload, indent=2, ensure_ascii=False)
    tool_cache.put("find_variable_candidates", result, description=description, k=k)
    return result


# ============================================================================
# Tool 13: answer_catalog_question — boundary-aware catalog Q&A
# ============================================================================
#
# This tool is the LLM-facing surface for the dataset / source-only /
# dropped / audit-only boundary spelled out in issue #73 + HITL #83.
# Routing decisions stay with the LLM (informed by this tool's
# description and the result's flags) — there is NO outer keyword
# router. Validation lives INSIDE the tool implementation.


def _load_catalog_artifact() -> Mapping[str, Any] | None:
    """Locate and load the published catalog artifact for the current study.

    Returns None when no catalog has been generated yet (e.g. before the
    Source Truth → catalog step has run). A None return lets the tool
    fall back to a clear "catalog not available" answer rather than
    crashing the agent.
    """
    study = getattr(config, "STUDY_NAME", None) or getattr(config, "STUDY", None)
    candidates: list[Path] = []
    output_root = Path(getattr(config, "OUTPUT_DIR", "output"))
    if isinstance(study, str) and study:
        candidates.append(output_root / study / "trio_bundle" / "study_variable_catalog.json")
        candidates.append(output_root / study / "study_variable_catalog.json")
    candidates.append(output_root / "study_variable_catalog.json")
    for path in candidates:
        try:
            if path.exists():
                validate_agent_read(path)
                with path.open("r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                if isinstance(payload, Mapping):
                    return payload
        except (OSError, ValueError, PermissionError):
            continue
    return None


@tool
@phi_safe_return
def answer_catalog_question(question: str) -> str:
    """Answer a study-variable metadata question through the published catalog.

    Use this for ordinary questions about retained study variables: their
    label, dataset column, form, options, and provenance. The catalog is
    the canonical metadata layer — prefer this tool over
    ``search_variables`` / ``get_variable_details`` for boundary-sensitive
    questions about whether a variable is analysable, source-only, or
    dropped.

    Boundary handling (read this carefully — it shapes the LLM's reply):

    * **Dataset-backed retained variable** (``analysis_queryable=true``,
      ``audit_only=false``): answer normally. Do NOT add a Note about PHI
      handling for ordinary metadata answers — the catalog already
      sanitises sensitive content. Repeating "PHI-handled" on every reply
      is noisy and the maintainer has explicitly asked for it to stop.
    * **Source-only variable** (the answer text contains a ``Note:`` and
      ``analysis_queryable=false``): the variable lives in PDF/metadata
      only. Surface the metadata answer; if the user asked to analyse it,
      add a brief one-line note that it is not analysis-queryable.
    * **Dropped variable** (the answer text says the variable is not
      available): pass the polite maintainer-contact text through. Do
      NOT speculate about why the variable was dropped, do NOT name PHI
      or sensitivity classifications, and do NOT mention the audit
      ledger.
    * **Audit-only flagged content** (``audit_only=true``): the JSON
      ``answer`` field will contain exactly the verbatim audit-only
      note pinned in HITL #83. Surface that text verbatim. Do not
      paraphrase, do not append explanations about handling policy,
      and do not look up ledger detail through other tools. The
      verbatim text the tool will return is:
      "Note: PHI handling decisions are recorded in the study audit ledger and aren't exposed through normal chat. For audit questions, please reach out to the project maintainer."

    Args:
        question: Natural-language question about a study variable.

    Returns:
        JSON string with ``question``, ``answer`` (the chat-ready text),
        ``variable_ids`` (resolved ids, possibly empty), ``audit_only``
        (bool), ``analysis_queryable`` (bool), and ``needs_clarification``
        (bool). The ``answer`` is already boundary-aware; the LLM should
        normally pass it through verbatim.
    """
    # Late import to avoid pulling source_truth into module load time
    # for environments that only use the legacy variables-reference path.
    from scripts.source_truth.retrieval import SourceTruthRetriever

    if _query_looks_conversational(question):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    catalog = _load_catalog_artifact()
    if catalog is None:
        return json.dumps(
            {
                "question": question,
                "answer": (
                    "The study catalog is not available yet. Please run "
                    "the Source Truth → catalog generation step first."
                ),
                "variable_ids": [],
                "audit_only": False,
                "analysis_queryable": False,
                "needs_clarification": False,
            },
            indent=2,
            ensure_ascii=False,
        )

    retriever = SourceTruthRetriever.from_catalog_artifact(catalog)
    answer = retriever.answer_chat_question(question)
    return json.dumps(
        {
            "question": question,
            "answer": answer.text,
            "variable_ids": list(answer.variable_ids),
            "audit_only": answer.audit_only,
            "analysis_queryable": answer.analysis_queryable,
            "needs_clarification": answer.needs_clarification,
        },
        indent=2,
        ensure_ascii=False,
    )


# ============================================================================
# Tool registry
# ============================================================================

ALL_TOOLS = [
    search_variables,
    find_variable_candidates,
    get_variable_details,
    list_forms,
    get_form_variables,
    query_dataset,
    get_dataset_stats,
    get_study_overview,
    run_python_analysis,
    cross_reference_variables,
    run_study_analysis,
    answer_catalog_question,
]
