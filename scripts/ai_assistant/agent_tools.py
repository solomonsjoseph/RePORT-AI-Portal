"""Structured tool registry for the RePORT AI Portal AI Assistant system.

All read-side tools resolve every path through
``scripts.ai_assistant.file_access.validate_agent_read`` — the unified
agent-zone chokepoint. The permitted read zone is
``output/{STUDY}/trio_bundle/`` (PHI-scrubbed artifacts) plus
``output/{STUDY}/agent/`` (the agent's own analysis outputs,
conversations, and snapshots). Telemetry lives under ``audit/`` and is
off-limits to the agent, so is raw data and staging. Writes (analysis
figures and narratives) are confined to ``output/{STUDY}/agent/`` via
``validate_agent_write``, with a narrower ``validate_sandbox_write``
for the ``exec_python`` path (LLM-generated code → ``agent/analysis/``
only). The pipeline-side ``assert_trio_bundle_zone`` / ``assert_output_zone``
helpers are still called as directory-level early-rejects before glob
iteration — they layer beneath the unified validator, not instead of it.
Each tool is decorated with ``@tool`` so it is automatically registered
with the LangGraph ReAct agent.

Tools
-----
1.  search_variables — fuzzy search across the unified variables reference
2.  find_variable_candidates — always-returns-top-k ranked candidates for disambiguation
3.  get_variable_details — full metadata for a specific variable
4.  list_forms — list all CRF forms in the study (from variables.json)
5.  get_form_variables — list all variables belonging to a specific form
6.  query_dataset — structural query on a JSONL dataset
7.  get_dataset_stats — summary statistics for a dataset (record counts, columns)
8.  get_study_overview — high-level study summary (datasets, forms, variables)
9.  run_python_analysis — sandboxed code execution for statistical analysis
10. cross_reference_variables — cross-reference a variable across datasets + forms
11. run_study_analysis — deterministic epidemiological analysis
12. search_pdf_context — keyword search over extracted CRF form text (qualitative Q&A)
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

import config
from scripts.ai_assistant.file_access import (
    validate_agent_read,
    validate_agent_write,
    validate_sandbox_write,
)
from scripts.ai_assistant.phi_safe import (
    phi_safe_return,
    sanitise_traceback,
    sanitise_untrusted_snippet,
)
from scripts.ai_assistant.tool_cache import tool_cache
from scripts.security.secure_env import assert_output_zone, assert_trio_bundle_zone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_variables_json() -> list[dict[str, Any]]:
    """Load the unified variables.json from the trio bundle."""
    path = config.VARIABLES_JSON_PATH
    if not path.exists():
        return []
    try:
        validated = validate_agent_read(path)
        return json.loads(validated.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load variables.json from %s", path)
        return []


# Pipeline-internal columns — filter from query results.
_INTERNAL_COLUMNS = frozenset(
    {
        "source_file",
        "_provenance",
        "_source_row",
        "_ingestion_ts",
    }
)


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
        "hi", "hello", "hey", "yo", "sup",
        "hii", "heyy", "hola",
        "thanks", "thank you", "ty", "thx",
        "ok", "okay", "cool", "nice", "got it",
        "help", "test", "try", "again", "no",
        "yes", "y", "n",
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
    "variable, form, dataset, cohort, or analysis (e.g. \"show me TB outcome "
    "variables\" or \"how many subjects completed treatment?\")."
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

    variables = _load_variables_json()
    if not variables:
        return "No variables reference found. Run --build-variables first."

    def _normalise_terms(values: Iterable[str]) -> list[str]:
        terms: list[str] = []
        for value in values:
            term = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
            if not term:
                continue
            if term.endswith("s") and len(term) > 3:
                term = term[:-1]
            terms.append(term)
        return terms

    # Weighted scoring: prefer exact phrase hits in variable names/descriptions
    # and keep the payload compact for smaller local models.
    stop = {"the", "a", "an", "of", "for", "and", "in", "is", "to", "or", "overall"}
    words = [w for w in query.split() if w.lower() not in stop]
    if not words:
        words = query.split()
    query_terms = _normalise_terms(words)
    query_phrase = " ".join(query_terms).strip()
    word_patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in query_terms]

    scored: list[tuple[int, dict[str, str]]] = []
    for var in variables:
        name = var.get("variable_name", "") or ""
        desc = var.get("description", "") or ""
        form = var.get("form_name", "") or ""
        section = var.get("section", "") or ""
        name_terms = _normalise_terms([name.replace("_", " ")])
        desc_terms = _normalise_terms([desc])
        form_terms = _normalise_terms([form, section])
        score = 0
        if query_phrase:
            if query_phrase in " ".join(name_terms):
                score += 18
            if query_phrase in " ".join(desc_terms):
                score += 22
            if query_phrase in " ".join(form_terms):
                score += 8

        for pattern in word_patterns:
            if pattern.search(name):
                score += 8
            if pattern.search(desc):
                score += 10
            if pattern.search(form) or pattern.search(section):
                score += 3

        combined_terms = " ".join(name_terms + desc_terms)
        if query_terms and all(term in combined_terms for term in query_terms):
            score += 16
        if query_terms and all(term in " ".join(desc_terms) for term in query_terms):
            score += 12
        if score > 0:
            scored.append(
                (
                    score,
                    {
                        "variable_name": name,
                        "form_name": var.get("form_name") or "",
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

    variables = _load_variables_json()
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

    variables = _load_variables_json()
    if not variables:
        return "No variables reference found. Run --build-variables first."

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

    variables = _load_variables_json()
    if not variables:
        return "No variables reference found. Run --build-variables first."

    # Word-split matching: rank forms by how many query words they contain.
    stop = {"form", "the", "a", "an", "of", "for", "and", "in", "-", "--"}
    words = [w for w in form_name.split() if w.lower() not in stop and re.search(r"\w", w)]
    if not words:
        words = form_name.split()
    word_patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in words]

    # Group variables by form_name and score each form
    forms: dict[str, list[dict[str, Any]]] = {}
    for var in variables:
        fname = var.get("form_name") or "Unknown"
        forms.setdefault(fname, []).append(var)

    best_form: str | None = None
    best_score = 0
    for fname in forms:
        score = sum(1 for p in word_patterns if p.search(fname))
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
    assert_trio_bundle_zone(datasets_dir)

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

    return json.dumps(
        {
            "dataset": matched_file.stem,
            "total_records": real_total,
            "rows_matching_filter": len(filtered) if filter_column else real_total,
            "returned": len(results),
            "available_columns": all_columns,
            "records": results,
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
    assert_trio_bundle_zone(datasets_dir)

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

    # Variables summary
    variables = _load_variables_json()
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

    # Forms count (derived from unique form_name values in variables.json)
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

# ── Sandbox security boundaries (hardcoded intentionally) ──────────────
# These limits are security controls, NOT tuneable config.  Expanding the
# import allowlist or raising limits requires a security review.
_ALLOWED_IMPORTS = frozenset(
    {
        "pandas",
        "numpy",
        "scipy",
        "statsmodels",
        "matplotlib",
        "plotly",
        "plotly.express",
        "plotly.graph_objects",
        "plotly.subplots",
        "collections",
        "math",
        "statistics",
        "re",
        "json",
        "matplotlib.pyplot",
        "scipy.stats",
        "statsmodels.api",
        "statsmodels.formula.api",
    }
)

_MAX_OUTPUT_BYTES = 50_000  # prevent memory exhaustion from large outputs
_MAX_FIGURES = 5  # cap matplotlib figure count per execution
_EXEC_TIMEOUT_SECONDS = 30  # hard wall-clock limit on sandboxed code


def _load_dataframes() -> dict[str, Any]:
    """Pre-load JSONL datasets as pandas DataFrames.

    Returns a dict mapping ``df_{stem}`` names to DataFrames.
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


def _safe_import_check(code: str) -> str | None:
    """Return an error message if code imports disallowed modules, else None."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Syntax error in code: {exc}"

    # Dangerous dunder attributes that enable sandbox escapes
    blocked_dunders = frozenset(
        {
            "__subclasses__",
            "__bases__",
            "__mro__",
            "__class__",
            "__globals__",
            "__code__",
            "__closure__",
            "__builtins__",
            "__loader__",
            "__spec__",
            "__import__",
        }
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _ALLOWED_IMPORTS:
                    return f"Import not allowed: {alias.name}. Allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Check top-level module
            top = module.split(".")[0]
            if top not in _ALLOWED_IMPORTS and module not in _ALLOWED_IMPORTS:
                return (
                    f"Import not allowed: {module}. Allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}"
                )
        # Block direct attribute access to dangerous dunders
        elif isinstance(node, ast.Attribute) and node.attr in blocked_dunders:
            return f"Access to `{node.attr}` is not allowed in the sandbox."

    return None


@tool
@phi_safe_return
def run_python_analysis(code: str) -> str:
    """Execute Python code for statistical analysis on study datasets.

    Runs in a restricted sandbox with pre-loaded DataFrames from the
    study's de-identified datasets. Use ``print()`` to output results.

    **Available DataFrames** (named ``df_<dataset>``, e.g. ``df_1A_ICScreening``):
    Call ``print(list(locals().keys()))`` to see all available DataFrames.

    **Allowed imports:** pandas, numpy, scipy, statsmodels, plotly,
    matplotlib, collections, math, statistics, re, json.

    **Pre-imported:** ``pd`` (pandas), ``np`` (numpy), ``px`` (plotly.express),
    ``go`` (plotly.graph_objects).

    **Prefer Plotly** for interactive charts: ``fig = px.bar(...); fig.show()``
    Matplotlib is available as a fallback for static plots.

    **Limits:** 30s timeout, 50KB output, 5 figures max.

    Args:
        code: Python code to execute. Use print() for output.
            Call fig.show() for Plotly figures or just create matplotlib figures.
    """
    import io
    import signal

    # 1. Validate imports
    import_err = _safe_import_check(code)
    if import_err:
        return f"**Import Error:** {import_err}"

    # 2. Block dangerous builtins (AST-based to avoid false positives on substrings)
    blocked = {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "breakpoint",
        "exit",
        "quit",
        "input",
        "globals",
    }

    import ast as _ast_check

    try:
        _tree = _ast_check.parse(code)
    except SyntaxError:
        pass  # _safe_import_check already reported syntax errors above
    else:
        for _node in _ast_check.walk(_tree):
            if isinstance(_node, _ast_check.Call):
                func = _node.func
                # Direct call: eval(...), exec(...), etc.
                if isinstance(func, _ast_check.Name) and func.id in blocked:
                    return f"**Security Error:** `{func.id}()` is not allowed in the sandbox."

    # 3. Build sandbox namespace
    import builtins

    safe_builtins = {
        k: v for k, v in vars(builtins).items() if k not in blocked and not k.startswith("_")
    }

    # Restricted __import__ that only allows pre-approved modules
    def _restricted_import(name: str, *args: Any, **kwargs: Any) -> Any:
        top = name.split(".")[0]
        if top not in _ALLOWED_IMPORTS and name not in _ALLOWED_IMPORTS:
            msg = f"Import not allowed: {name}"
            raise ImportError(msg)
        return __import__(name, *args, **kwargs)

    safe_builtins["__import__"] = _restricted_import
    safe_builtins["print"] = print  # will be redirected via stdout

    # Guard getattr to block dunder attribute access at runtime.
    # This prevents bypass via getattr(obj, chr(95)*2 + "globals" + chr(95)*2).
    runtime_blocked_dunders = frozenset(
        {
            "__subclasses__",
            "__bases__",
            "__mro__",
            "__class__",
            "__globals__",
            "__code__",
            "__closure__",
            "__builtins__",
            "__loader__",
            "__spec__",
            "__import__",
            "__qualname__",
        }
    )
    _real_getattr = getattr

    def _safe_getattr(obj: Any, name: str, *default: Any) -> Any:
        if name in runtime_blocked_dunders:
            msg = f"Access to `{name}` is not allowed in the sandbox."
            raise AttributeError(msg)
        return _real_getattr(obj, name, *default)

    safe_builtins["getattr"] = _safe_getattr

    # Guard vars() to strip dangerous dunder keys from returned dicts.
    # Without this, vars(module).__builtins__.__import__ escapes the sandbox.
    _real_vars = vars

    def _safe_vars(*args: Any) -> dict[str, Any]:
        result = _real_vars(*args)
        return {k: v for k, v in result.items() if k not in runtime_blocked_dunders}

    safe_builtins["vars"] = _safe_vars

    # Zone-guarded open() — prevents pandas/numpy from reading/writing outside
    # the output zone.  All file I/O (pd.read_csv, np.loadtxt, df.to_csv, etc.)
    # ultimately calls builtins.open(), so intercepting it here is sufficient.
    _real_open = builtins.open

    def _zone_guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        import pathlib

        _path = pathlib.Path(str(file)).resolve()
        _reading = not any(c in mode for c in "wxa+")
        try:
            if _reading:
                # Reads: delegate to the unified validator (trio + agent).
                validate_agent_read(_path)
            else:
                # Writes: the exec_python sandbox runs LLM-generated code,
                # which is a strictly narrower threat model than tool-code.
                # Keep writes scoped to AGENT_OUTPUT_DIR (analysis/) — not
                # the full agent/** zone. ``validate_sandbox_write`` uses
                # commonpath-based containment (symlink-safe, immune to
                # sibling-prefix escapes like ``agent/analysis_exfil/``).
                validate_sandbox_write(_path)
        except PermissionError:
            raise
        except Exception as exc:  # ZoneViolationError is a PermissionError subclass
            msg = (
                f"File access denied: {file}\n"
                "Sandbox can only read from trio_bundle/ + agent/ "
                "and write to agent/analysis/."
            )
            raise PermissionError(msg) from exc
        return _real_open(file, mode, *args, **kwargs)

    safe_builtins["open"] = _zone_guarded_open

    namespace: dict[str, Any] = {"__builtins__": safe_builtins}

    # Pre-load DataFrames
    dataframes = _load_dataframes()
    namespace.update(dataframes)

    # Pre-import common modules for convenience
    try:
        import numpy as np
        import pandas as pd

        namespace["pd"] = pd
        namespace["np"] = np
    except ImportError:
        pass

    try:
        import plotly.express as px
        import plotly.graph_objects as go

        namespace["px"] = px
        namespace["go"] = go

        # Monkeypatch fig.show() to collect figures instead of opening a browser
        _plotly_figs: list[Any] = []
        namespace["_rpln_plotly_figs"] = _plotly_figs
        _orig_show = go.Figure.show

        def _capture_show(self: Any, *args: Any, **kwargs: Any) -> None:
            _plotly_figs.append(self)

        go.Figure.show = _capture_show  # type: ignore[assignment]
    except ImportError:
        _orig_show = None

    # 4. Capture stdout
    stdout_capture = io.StringIO()

    # 5. Timeout handler (Unix, main-thread only)
    # Python's signal module restricts signal.signal() to the main thread of
    # the main interpreter; attempting to install a handler from a worker
    # thread (e.g. Streamlit's ScriptRunner) raises ValueError. See
    # https://docs.python.org/3/library/signal.html -- "Python signal handlers
    # are always executed in the main Python thread of the main interpreter".
    # We install the alarm when possible and fall back to an unguarded run
    # otherwise; the _MAX_OUTPUT_BYTES cap and sandboxed namespace remain as
    # secondary bounds.
    def _timeout_handler(signum: int, frame: Any) -> None:
        msg = f"Execution timed out after {_EXEC_TIMEOUT_SECONDS}s"
        raise TimeoutError(msg)

    import threading

    _alarm_installed = False
    old_handler: Any = None
    if threading.current_thread() is threading.main_thread():
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(_EXEC_TIMEOUT_SECONDS)
            _alarm_installed = True
        except (ValueError, OSError) as _alarm_exc:
            logger.warning(
                "SIGALRM-based timeout unavailable (%s); proceeding without wall-clock guard",
                _alarm_exc,
            )

    import sys

    old_stdout = sys.stdout
    try:
        sys.stdout = stdout_capture  # type: ignore[assignment]
        exec(compile(code, "<analysis>", "exec"), namespace)  # noqa: S102
    except TimeoutError:
        return f"**Timeout:** Code execution exceeded {_EXEC_TIMEOUT_SECONDS}s limit."
    except Exception as exc:
        from scripts.utils import errors as _rpln_err

        err = _rpln_err.wrap(
            exc,
            stage="agent.tool",
            operation="run_python_analysis",
            hint="Check the generated code; figures must be written under AGENT_OUTPUT_DIR.",
        )
        logger.error(err.as_log_block())
        return f"**Runtime Error:** {type(exc).__name__}: {exc}"
    finally:
        if _alarm_installed:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        sys.stdout = old_stdout
        # Restore Plotly's original show() method
        try:
            import plotly.graph_objects as _go_restore

            if _orig_show is not None:
                _go_restore.Figure.show = _orig_show  # type: ignore[assignment]
        except (ImportError, NameError):
            pass

    # 7. Collect output
    output = stdout_capture.getvalue()
    if len(output) > _MAX_OUTPUT_BYTES:
        output = output[:_MAX_OUTPUT_BYTES] + f"\n\n[Output truncated at {_MAX_OUTPUT_BYTES} bytes]"

    # 8. Capture Plotly figures — save as JSON for interactive rendering
    import uuid as _uuid

    fig_dir = config.AGENT_OUTPUT_DIR / "figures"
    assert_output_zone(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    plotly_paths: list[str] = []
    try:
        import plotly.io as _pio

        # Collect Plotly figures stored by user code via px or go
        # Convention: sandbox code calls fig.show() which we monkeypatch to collect
        collected_figs: list[Any] = namespace.get("_rpln_plotly_figs", [])
        for fig_obj in collected_figs[:_MAX_FIGURES]:
            fig_id = _uuid.uuid4().hex[:12]
            fig_path = fig_dir / f"plotly_{fig_id}.json"
            fig_path.write_text(_pio.to_json(fig_obj), encoding="utf-8")
            plotly_paths.append(str(fig_path))
    except ImportError:
        pass

    # 9. Capture matplotlib figures — save to output zone as fallback static images
    figure_paths: list[str] = []
    try:
        import matplotlib.pyplot as plt

        fig_nums = plt.get_fignums()
        for fig_num in fig_nums[:_MAX_FIGURES]:
            fig = plt.figure(fig_num)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
            buf.seek(0)
            fig_id = _uuid.uuid4().hex[:12]
            fig_path = fig_dir / f"fig_{fig_id}.png"
            fig_path.write_bytes(buf.read())
            figure_paths.append(str(fig_path))
            plt.close(fig)
        # Close any remaining figures
        plt.close("all")
    except ImportError:
        pass

    # 10. Build result — text output, then RPLN_PLOTLY / RPLN_FIGURE markers
    parts: list[str] = []
    if output.strip():
        parts.append(output.strip())
    total_figs = len(plotly_paths) + len(figure_paths)
    if total_figs:
        parts.append(f"\n[{total_figs} figure(s) generated]")
        parts.extend(f"\n<RPLN_PLOTLY:{fpath}>" for fpath in plotly_paths)
        parts.extend(f"\n<RPLN_FIGURE:{fpath}>" for fpath in figure_paths)
    if not parts:
        parts.append("Code executed successfully (no output).")

    result = "\n".join(parts)
    logger.info(
        "run_python_analysis: %d chars output, %d plotly, %d matplotlib",
        len(output),
        len(plotly_paths),
        len(figure_paths),
    )
    return result


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

    # 1. Variable definitions from reference
    variables = _load_variables_json()
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
    assert_trio_bundle_zone(datasets_dir)
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

            dataset_presence.append(
                {
                    "dataset": f.stem,
                    "matching_columns": matching_cols,
                    "total_records": total,
                    "populated_records": populated,
                    "completeness_pct": round(populated / total * 100, 1) if total else 0.0,
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

    This tool executes pre-built, deterministic epidemiological analyses.
    No arbitrary code is executed — all analyses are pre-validated functions.

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

        # Hard refuse when the sample cannot support any inferential stats.
        # Threshold = 5 events (Peduzzi et al. 1996 floor of ~10 EPV makes
        # anything below 5 events meaningless even for a 1-predictor model).
        if result.events < 5:
            return (
                f"Analysis not run for {result.cohort_name} — {result.outcome}: "
                f"only {result.events} event(s) in {result.n} subjects, below "
                "the 5-event floor for any logistic model. "
                "Report descriptive counts only — do not run univariate/multivariate/interaction."
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
        if result.caveats:
            full_parts.append(result.caveats)
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

        summary_lines = [
            f"ANALYSIS COMPLETE for {result.cohort_name} — {result.outcome}.",
            f"N={result.n}, Events={result.events} ({result.events / result.n * 100:.1f}% rate).",
            f"Figures generated: {len(result.interactive_figures) + len(result.figures)}.",
            "",
        ]
        if underpowered:
            summary_lines.insert(
                1,
                f"⚠️ UNDERPOWERED: events={result.events}, "
                f"events/variable={events_per_variable:.1f} (target ≥10, floor ≥5). "
                "Multivariate ORs may be unstable; interpret point estimates with caution.",
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
        summary_lines.append(f"<RPLN_ANALYSIS:{narrative_path}>")
        summary_lines.append("Tell the user the analysis is complete and results are shown below.")

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
    name = (var.get("variable_name") or "")
    desc = (var.get("description") or "")
    form = (var.get("form_name") or "")
    section = (var.get("section") or "")

    def _norm(value: str) -> str:
        term = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        if term.endswith("s") and len(term) > 3:
            term = term[:-1]
        return term

    name_n = _norm(name.replace("_", " "))
    desc_n = _norm(desc)
    form_n = _norm(form + " " + section)

    score = 0
    if query_phrase:
        if query_phrase in name_n:
            score += 18
        if query_phrase in desc_n:
            score += 22
        if query_phrase in form_n:
            score += 8

    for term in query_terms:
        pat = re.compile(re.escape(term), re.IGNORECASE)
        if pat.search(name):
            score += 8
        if pat.search(desc):
            score += 10
        if pat.search(form) or pat.search(section):
            score += 3

    combined = name_n + " " + desc_n
    if query_terms and all(term in combined for term in query_terms):
        score += 16
    if query_terms and all(term in desc_n for term in query_terms):
        score += 12
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

    variables = _load_variables_json()
    if not variables:
        return "No variables reference found. Run --build-variables first."

    stop = {"the", "a", "an", "of", "for", "and", "in", "is", "to", "or", "on", "overall"}
    words = [w for w in description.split() if w.lower() not in stop] or description.split()
    query_terms: list[str] = []
    for w in words:
        t = re.sub(r"[^a-z0-9]+", " ", w.lower()).strip()
        if not t:
            continue
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        query_terms.append(t)
    query_phrase = " ".join(query_terms).strip()

    scored: list[tuple[int, dict[str, Any]]] = []
    for var in variables:
        score = _score_variable(var, query_terms, query_phrase)
        if score > 0:
            scored.append((score, var))

    scored.sort(
        key=lambda x: (-x[0], len(x[1].get("variable_name") or ""), x[1].get("variable_name") or ""),
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
# Tool 12: search_pdf_context — keyword search over extracted CRF text
# ============================================================================


def _pdf_context_snippets() -> list[dict[str, str]]:
    """Flatten all extracted CRF JSON files into (form, section, text) snippets.

    Each snippet has a single text blob; the caller can score and rank.
    Cached on first call per process to avoid re-reading 28 JSON files.
    """
    cache_key = "_pdf_context_snippets"
    cached = tool_cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    pdf_dir = config.PDF_EXTRACTIONS_DIR
    assert_trio_bundle_zone(pdf_dir)
    snippets: list[dict[str, str]] = []
    if not pdf_dir.exists():
        return snippets

    for pth in sorted(pdf_dir.glob("*.json")):
        try:
            pth_validated = validate_agent_read(pth)
            data = json.loads(pth_validated.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, PermissionError):
            logger.warning("Failed to load PDF extract: %s", pth.name)
            continue

        form_name = data.get("form_name") or pth.stem
        source_pdf = data.get("source_pdf") or ""
        summary = (data.get("summary") or "").strip()
        if summary:
            snippets.append(
                {
                    "form_name": form_name,
                    "source_pdf": source_pdf,
                    "snippet_kind": "form_summary",
                    "variable_name": "",
                    "section": "",
                    "text": summary,
                }
            )

        variables = data.get("variables") or {}
        for var_name, var_body in variables.items():
            if not isinstance(var_body, dict):
                continue
            parts: list[str] = []
            desc = (var_body.get("description") or "").strip()
            ctx = (var_body.get("section_context") or "").strip()
            cond = (var_body.get("condition") or "").strip()
            if desc:
                parts.append(desc)
            if cond:
                parts.append(f"Condition: {cond}")
            if ctx:
                parts.append(ctx)
            if not parts:
                continue
            snippets.append(
                {
                    "form_name": form_name,
                    "source_pdf": source_pdf,
                    "snippet_kind": "variable",
                    "variable_name": str(var_name),
                    "section": "",
                    "text": "  ".join(parts),
                }
            )

    tool_cache.put(cache_key, snippets)  # type: ignore[arg-type]
    return snippets


def _score_text(text: str, query_terms: list[str], query_phrase: str) -> int:
    """Light keyword + phrase scoring for free-text snippets."""
    lower = text.lower()
    score = 0
    if query_phrase and query_phrase in lower:
        score += 25
    for term in query_terms:
        if term in lower:
            # multiple mentions reward modestly
            score += 4 + min(lower.count(term) - 1, 6)
    if query_terms and all(term in lower for term in query_terms):
        score += 10
    return score


@tool
@phi_safe_return
def search_pdf_context(query: str, k: int = 5) -> str:
    """Search the extracted CRF form text for qualitative study-design questions.

    Use this when the user asks about **study design, definitions, eligibility
    criteria, schedules, or procedures** — e.g. "what are the inclusion criteria
    for Cohort A?", "how is a household contact defined?", "what is the
    follow-up schedule?". This tool does NOT count records and does NOT run
    statistics — for those, use ``query_dataset`` / ``get_dataset_stats`` /
    ``run_study_analysis``.

    It searches over every extracted CRF form's summary, every variable's
    description, and every variable's section_context (the narrative text
    that appears above the field on the paper form). Results come back
    ranked, each with a form citation so the user can verify.

    Note: This currently searches CRF-level text only. Protocol-level
    definitions (e.g. 'TB relapse' vs 'treatment failure') are NOT included
    unless the protocol PDF has been added to the extraction pipeline. If the
    top result's confidence is low, say so and suggest the user provide the
    protocol document.

    Args:
        query: Natural-language question or keywords.
        k: Number of snippets to return. Default 5. Clamped to [1, 15].

    Returns:
        JSON string with ``query``, ``count``, and ``snippets``. Each snippet
        has ``rank``, ``form_name``, ``source_pdf``, ``snippet_kind``
        ('form_summary' or 'variable'), ``variable_name`` (if applicable),
        ``text``, and a normalised ``score`` in [0, 1].

    Conversational / too-short inputs (greetings, <3-char strings) return
    an explicit refusal so the LLM does not surface noisy substring hits
    for small-talk against the PDF text corpus.
    """
    if _query_looks_conversational(query):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    k = max(1, min(int(k), 15))
    hit = tool_cache.get("search_pdf_context", query=query, k=k)
    if hit is not None:
        return hit

    snippets = _pdf_context_snippets()
    if not snippets:
        return "No extracted PDF context found. Run the PDF extraction pipeline first."

    stop = {"the", "a", "an", "of", "for", "and", "in", "is", "to", "or", "on", "what", "how", "does", "do"}
    words = [w for w in query.split() if w.lower() not in stop] or query.split()
    query_terms: list[str] = []
    for w in words:
        t = re.sub(r"[^a-z0-9]+", " ", w.lower()).strip()
        if t:
            query_terms.append(t)
    query_phrase = " ".join(query_terms).strip()

    scored: list[tuple[int, dict[str, str]]] = []
    for snip in snippets:
        s = _score_text(snip["text"], query_terms, query_phrase)
        if s > 0:
            scored.append((s, snip))

    if not scored:
        return json.dumps(
            {"query": query, "count": 0, "snippets": [], "note": "No matching CRF text."},
            indent=2,
            ensure_ascii=False,
        )

    scored.sort(key=lambda x: (-x[0], len(x[1]["text"]), x[1]["form_name"]))
    top = scored[:k]
    max_score = top[0][0]
    denom = max(max_score, 40)  # strong match threshold

    out: list[dict[str, Any]] = []
    for rank, (score, snip) in enumerate(top, start=1):
        text = snip["text"]
        if len(text) > 600:
            text = text[:600].rstrip() + "…"
        safe_text = sanitise_untrusted_snippet(
            text, source_label=f"PDF {snip['source_pdf']}"
        )
        out.append(
            {
                "rank": rank,
                "form_name": snip["form_name"],
                "source_pdf": snip["source_pdf"],
                "snippet_kind": snip["snippet_kind"],
                "variable_name": snip["variable_name"],
                "text": safe_text,
                "score": round(min(score / denom, 1.0), 3),
            }
        )

    payload = {
        "query": query,
        "count": len(out),
        "snippets": out,
        "low_confidence": bool(out and out[0]["score"] < 0.4),
    }
    result = json.dumps(payload, indent=2, ensure_ascii=False)
    tool_cache.put("search_pdf_context", result, query=query, k=k)
    return result


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
    search_pdf_context,
]
