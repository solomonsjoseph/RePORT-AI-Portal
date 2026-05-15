"""Structured tool registry for the RePORT AI Portal AI Assistant system.

All read-side tools resolve every path through
``scripts.ai_assistant.file_access.validate_agent_read`` — the unified
agent-zone chokepoint. The permitted read zone is
``output/{STUDY}/llm_source/`` (PHI-scrubbed artifacts) plus
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
1.  search_variables — dataset column search (dictionary fallback when catalog has no answer)
2.  query_dataset — structural query on a JSONL dataset
3.  get_dataset_stats — summary statistics for a dataset (record counts, columns)
4.  list_available_datasets — list available PHI-scrubbed datasets
5.  run_python_analysis — sandboxed code execution for statistical analysis
6.  run_study_analysis — deterministic epidemiological analysis
7.  answer_catalog_question — primary variable metadata lookup via SourceTruthRetriever
8.  produce_evidence_report — structured PHI-safe analysis report for canonical questions
9.  produce_custom_evidence_report — parameterised analysis report for custom questions
10. cite_source — deterministic (file, line, snippet) citation for form fields
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from collections.abc import Mapping
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



def _dataset_label(stem: str) -> str:
    return _expanded_form_text(stem).title()


def _load_dataset_column_variables() -> list[dict[str, Any]]:
    """Expose published dataset columns as retrieval candidates.

    The per-form evidence packs may not enumerate every published column. The
    agent still needs to discover columns that are demonstrably present in the
    published ``llm_source`` files, so we build a metadata-only reference from JSONL
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
    # evidence packs and study_metadata/catalog.json are consumed by the
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

_ROW_IDENTIFIER_COLUMNS: tuple[str, ...] = (
    "SUBJID",
    "USUBJID",
    "SUBJECT_ID",
    "PARTICIPANT_ID",
    "PATIENT_ID",
    "FID",
    "FIDNO",
)


def _present_columns(rows: list[dict[str, Any]], candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of *candidates* that actually appears in any row."""
    seen: set[str] = set()
    candidate_set = frozenset(candidates)
    for row in rows[:200]:  # column set is stable across rows; sample is sufficient
        seen.update(row.keys())
        if candidate_set.issubset(seen):
            break  # all candidates found; no need to scan further
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

    Published ``llm_source`` datasets store SANT-shifted clinical dates, but the generic PHI
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
    "No study lookup is needed for this turn. Answer directly if you can, "
    "then invite the user back to a concrete study question about a variable, "
    "form, dataset, cohort, or analysis."
)


# ============================================================================
# Tool 1: search_variables
# ============================================================================


@tool
@phi_safe_return
def search_variables(query: str) -> str:
    """Search dataset column names as a fallback when answer_catalog_question has no result.

    Scans the published JSONL dataset column headers (the raw dataset schema) for columns
    whose name contains any token from the query. Use this ONLY after answer_catalog_question
    returns no result for a variable question.

    Args:
        query: Search term — matched against dataset column names by plain token intersection.
    """
    if _query_looks_conversational(query):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    hit = tool_cache.get("search_variables", query=query)
    if hit is not None:
        return hit

    variables = _load_dataset_column_variables()
    if not variables:
        return "No variables reference found. Ensure llm_source/dataset_schema/files/ is populated."

    tokens = {t.lower() for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 2}
    if not tokens:
        return f"No variables found matching '{query}'."

    matches = []
    for var in variables:
        name = (var.get("variable_name") or "").lower()
        desc = (var.get("description") or "").lower()
        if any(t in name or t in desc for t in tokens):
            matches.append({
                "variable_name": var.get("variable_name", ""),
                "form_name": var.get("form_name", ""),
                "dataset": var.get("dataset", ""),
                "description": var.get("description", ""),
            })

    if not matches:
        return f"No variables found matching '{query}'."

    result = json.dumps(matches[:12], indent=2, ensure_ascii=False)
    tool_cache.put("search_variables", result, query=query)
    return result





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
    governance and regulatory anonymization protocol. Operates on the
    PHI-scrubbed ``llm_source/`` view; row data never leaves the sandbox.
    Compose with :func:`list_available_datasets`, :func:`get_dataset_stats`,
    and :func:`run_python_analysis` to plan ad-hoc analyses instead of
    forcing the question into a canonical slot.

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

    # k-anonymity + l-diversity gate (Phase 3.A + 3.B). Run on the filtered
    # full rows, not the projected result rows: otherwise a caller could
    # filter down to a single subject/class, project away the quasi-
    # identifiers, and receive the sensitive value anyway.
    from scripts.ai_assistant.phi_safe import guard_rows_with_kanon_and_ldiv
    from scripts.security.kanon_gate import mask_small_cell

    gating_rows = [
        {k: v for k, v in rec.items() if k not in _INTERNAL_COLUMNS} for rec in filtered
    ]
    qi_present = _present_columns(gating_rows, _DEFAULT_QUASI_IDENTIFIERS)
    sens_present = _present_columns(gating_rows, _DEFAULT_SENSITIVE_ATTRIBUTES)
    filter_col_upper = (filter_column or "").upper()
    subject_identifier_filter = filter_col_upper in _ROW_IDENTIFIER_COLUMNS

    safe_records: list[Mapping[str, Any]] = list(results)
    kanon_violation: dict[str, Any] | None = None

    if subject_identifier_filter:
        safe_records = []
        kanon_violation = {
            "gate": "subject_identifier_filter",
            "k": 5,
            "l": None,
            "smallest_class_size": None,
            "smallest_diversity": None,
            "quasi_identifiers": [filter_column] if filter_column else [],
            "sensitive_attributes": list(sens_present),
            "message": (
                "Row-level surface suppressed: exact subject identifier filters are "
                "not exposed through query_dataset. Use aggregate tools or broaden "
                "the cohort definition."
            ),
        }
    elif filter_column and len(filtered) < 5:
        safe_records = []
        kanon_violation = {
            "gate": "small_filter_cell",
            "k": 5,
            "l": None,
            "smallest_class_size": len(filtered),
            "smallest_diversity": None,
            "quasi_identifiers": [filter_column],
            "sensitive_attributes": list(sens_present),
            "message": (
                "Row-level surface suppressed: the filter matches fewer than 5 "
                "records. Re-query with broader bins or aggregate via "
                "get_dataset_stats / cross_reference_variables."
            ),
        }
    elif qi_present and gating_rows:
        _gated, kanon_res, ldiv_res = guard_rows_with_kanon_and_ldiv(
            gating_rows,
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
            # ``gated`` is the full-row safety check. Surface only the caller's
            # projected records after the gate passes.
            safe_records = list(results)

    safe_records, date_values_redacted = _surface_safe_records(safe_records)

    return json.dumps(
        {
            "dataset": matched_file.stem,
            "total_records": real_total,
            "rows_matching_filter": (
                mask_small_cell(len(filtered), k=5) if filter_column else real_total
            ),
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
# Tool 5b: list_available_datasets
# ============================================================================


# Defense-in-depth PHI filter: free-text narrative columns whose values are
# upstream-scrubbed under ``llm_source/`` but which we re-drop here so the
# discovery hop never advertises a column that could leak narrative PHI even
# under a partial upstream regression.
_PHI_COLUMN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:COMMENT|REMARK|NOTE|SPECIFY)$", re.IGNORECASE),
    re.compile(r"^WITHDRAWEXPLAIN$", re.IGNORECASE),
)


def _column_is_phi(column_name: str) -> bool:
    """Return True when *column_name* matches a defense-in-depth PHI pattern."""
    return any(pat.search(column_name) for pat in _PHI_COLUMN_PATTERNS)


def _list_available_datasets_impl(*, include_columns: bool = False) -> list[dict[str, Any]]:
    """Pure implementation backing :func:`list_available_datasets`.

    Walks the published ``llm_source/dataset_schema/files/`` zone through
    :func:`validate_agent_read` and returns one record per JSONL with row
    counts and inferred column schema. Free-text narrative columns are
    dropped as defense in depth; the count is reported on each record.
    """
    datasets_dir_raw = config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    # Gate: ensures the dataset zone is inside the agent read allowlist
    # (``llm_source/`` or ``agent/``). Never bypass.
    try:
        datasets_dir = validate_agent_read(datasets_dir_raw)
    except PermissionError:
        logger.warning(
            "list_available_datasets: dataset directory %s outside agent read zone",
            datasets_dir_raw,
        )
        return []

    if not datasets_dir.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for jsonl_path in sorted(datasets_dir.glob("*.jsonl")):
        # Per-file zone validation — defense in depth against symlink escape.
        try:
            resolved = validate_agent_read(jsonl_path)
        except PermissionError:
            logger.warning(
                "list_available_datasets: skipping out-of-zone file %s", jsonl_path
            )
            continue

        # Stream the row count without loading the file into memory.
        try:
            with open(resolved, encoding="utf-8") as fh:
                n_rows = sum(1 for line in fh if line.strip())
        except OSError:
            logger.warning("list_available_datasets: unreadable file %s", resolved)
            continue

        # Internal storage paths are intentionally NOT included in the
        # returned record — the agent's chat replies must not surface
        # filesystem locations. The form stem (e.g. "101_HHC_Recontact")
        # is the natural identifier downstream tools accept.
        record: dict[str, Any] = {
            "form": resolved.stem,
            "n_rows": n_rows,
            "phi_filtered": 0,
        }

        if include_columns:
            columns: list[dict[str, str]] = []
            phi_filtered = 0
            first_record: dict[str, Any] | None = None
            try:
                with open(resolved, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            first_record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(first_record, dict):
                            break
                        first_record = None
            except OSError:
                first_record = None

            if isinstance(first_record, dict):
                for col_name, value in first_record.items():
                    if col_name in _INTERNAL_COLUMNS:
                        continue
                    if _column_is_phi(col_name):
                        phi_filtered += 1
                        logger.warning(
                            "PHI defense-in-depth tripped: dataset=%s column=%s",
                            resolved.stem,
                            col_name,
                        )
                        continue
                    columns.append({"name": col_name, "dtype": type(value).__name__})
            record["columns"] = columns
            record["phi_filtered"] = phi_filtered

        out.append(record)

    out.sort(key=lambda r: r["form"])
    return out


@tool
@phi_safe_return
def list_available_datasets(include_columns: bool = False) -> str:
    """Discovery hop: enumerate every PHI-scrubbed dataset the agent can read.

    Returns one record per JSONL under the published ``llm_source/datasets/``
    path. Each record exposes schema + row counts only — never row contents —
    so you can plan a custom analysis in one tool call instead of probing
    forms one at a time. Operates strictly on the PHI-scrubbed view; free-
    text narrative columns (``*COMMENT``, ``*REMARK``, ``*NOTE``,
    ``*SPECIFY``, ``WITHDRAWEXPLAIN``) are already dropped upstream and
    re-filtered here as defense in depth.

    Use this together with :func:`query_dataset`, :func:`get_dataset_stats`,
    and :func:`run_python_analysis` to compose ad-hoc analyses over the
    scrubbed view without forcing your question into one of the canonical
    question slots.

    Args:
        include_columns: When False (default), return only ``form``,
            ``n_rows`` (plus ``phi_filtered``) — a compact response intended
            as the first hop. Set True only when you need each dataset's
            column schema; the full-column response is large and meant to be
            requested on demand, per-form, via ``get_dataset_stats``.

    Returns:
        JSON-encoded list of ``{form, n_rows, columns, phi_filtered}``
        records sorted by form name. ``form`` is the form stem (e.g.
        ``"101_HHC_Recontact"``) — the natural identifier accepted by
        downstream tools such as :func:`query_dataset` and
        :func:`get_dataset_stats`. ``columns`` is ``[{name, dtype}]`` where
        ``dtype`` is the Python type-name of the first non-null value seen.
        ``phi_filtered`` is the count of columns dropped by the
        defense-in-depth PHI filter.

    Note:
        By design this tool returns no filesystem paths — the agent's chat
        replies must not expose internal storage locations.
    """
    records = _list_available_datasets_impl(include_columns=include_columns)
    return json.dumps(records, indent=2, ensure_ascii=False)


# ============================================================================
# Tool 6: get_dataset_stats
# ============================================================================


@tool
@phi_safe_return
def get_dataset_stats(dataset_name: str | None = None) -> str:
    """Get summary statistics for study datasets.

    Returns record counts, column counts, and column names for each dataset.
    If dataset_name is provided, returns stats for that dataset only.
    Otherwise returns stats for all datasets. Operates on the PHI-scrubbed
    ``llm_source/`` view; the agent is expected to compose this with
    :func:`list_available_datasets`, :func:`query_dataset`, and
    :func:`run_python_analysis` for ad-hoc analyses rather than forcing the
    question into a canonical slot. Row data never leaves the sandbox.

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


def _unsafe_sandbox_stdout_reason(stdout: str) -> str | None:
    """Return a security reason when stdout appears to expose row-level data."""
    text = stdout.strip()
    if not text:
        return None

    from scripts.security.phi_patterns import SUBJECT_ID_PATTERNS

    if any(pattern.search(text) for pattern in SUBJECT_ID_PATTERNS):
        return "sandbox stdout contained a subject identifier"

    row_level_markers = (
        "SUBJID",
        "USUBJID",
        "SUBJECT_ID",
        "PARTICIPANT_ID",
        "PATIENT_ID",
        "_provenance",
        "_source_row",
    )
    if any(
        re.search(rf"\b{re.escape(marker)}\b", text, re.IGNORECASE)
        for marker in row_level_markers
    ):
        return "sandbox stdout contained row-level identifier columns"

    table_like_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\s+\S+(?:\s+\S+){2,}$", stripped):
            table_like_lines += 1
    if table_like_lines >= 3:
        return "sandbox stdout looked like a row-level table dump"

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

    Operates over the PHI-scrubbed ``llm_source/`` view; row data never
    leaves the sandbox — only printed summaries and rendered figures
    return to the caller. The agent is expected to compose this tool
    with :func:`list_available_datasets`, :func:`query_dataset`, and
    :func:`get_dataset_stats` to plan an ad-hoc analysis rather than
    forcing the question into a canonical slot.

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

    unsafe_reason = _unsafe_sandbox_stdout_reason(result.stdout)
    if unsafe_reason:
        logger.warning("run_python_analysis stdout suppressed: %s", unsafe_reason)
        return (
            "**Security Error:** Row-level sandbox output was suppressed. "
            "Use aggregate summaries, model coefficients, confidence intervals, "
            "or figures that do not print subject-level rows."
        )

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


def _load_catalog_binding_artifacts() -> tuple[dict[str, Any], dict[str, Any]] | str:
    """Load published catalog + Dataset Schema artifacts for the hard-cutover path."""
    catalog = _load_catalog_artifact()
    if catalog is None:
        return "The study metadata catalog is not available. Run the pipeline to publish llm_source first."

    schema_path = config.STUDY_LLM_SOURCE_DIR / "dataset_schema.json"
    if not schema_path.is_file():
        return (
            "The Dataset Schema binding artifact is not available at "
            f"{schema_path}. Run the Source Truth build/verify pipeline first."
        )
    try:
        validate_agent_read(schema_path)
        with schema_path.open("r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, PermissionError, json.JSONDecodeError) as exc:
        return f"Could not load Dataset Schema binding artifact: {exc}"
    if not isinstance(schema, dict):
        return "The Dataset Schema binding artifact is malformed: expected a JSON object."

    catalog_dict = dict(catalog)
    if "records" not in catalog_dict and isinstance(catalog_dict.get("compact_records"), list):
        catalog_dict["records"] = catalog_dict["compact_records"]
    return catalog_dict, schema


def _schema_variable_ids(schema: Mapping[str, Any]) -> set[str]:
    entries = schema.get("entries")
    if not isinstance(entries, list):
        return set()
    return {
        str(entry.get("variable_id"))
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(entry.get("variable_id"), str)
        and entry.get("analysis_queryable") is True
    }


def _catalog_records_for_resolution(catalog: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = catalog.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def _resolve_catalog_variable_token(
    token: str,
    *,
    catalog: Mapping[str, Any],
    schema_ids: set[str],
) -> tuple[str | None, str | None]:
    cleaned = token.strip()
    if not cleaned:
        return None, None
    upper = cleaned.upper()
    by_upper = {vid.upper(): vid for vid in schema_ids}
    if upper in by_upper:
        return by_upper[upper], None

    term = _normalise_search_text(cleaned)
    matches: list[tuple[str, str, str]] = []
    for record in _catalog_records_for_resolution(catalog):
        variable_id = record.get("variable_id")
        if not isinstance(variable_id, str) or variable_id not in schema_ids:
            continue
        if record.get("audit_only") is True or record.get("analysis_queryable") is False:
            continue
        label = str(record.get("label") or record.get("display_label") or "")
        haystack = _normalise_search_text(f"{variable_id} {label}")
        if term and term in haystack:
            matches.append((variable_id, str(record.get("form") or ""), label))

    if len(matches) == 1:
        return matches[0][0], None
    if matches:
        preview = ", ".join(
            f"{variable_id} ({label or form})" for variable_id, form, label in matches[:8]
        )
        return None, f"{cleaned!r} is ambiguous. Candidate variable IDs: {preview}."
    return None, f"{cleaned!r} did not match an analysis-queryable catalog variable ID."


def _resolve_catalog_variable_list(
    value: str,
    *,
    catalog: Mapping[str, Any],
    schema_ids: set[str],
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    issues: list[str] = []
    for token in [part.strip() for part in value.split(",") if part.strip()]:
        variable_id, issue = _resolve_catalog_variable_token(
            token,
            catalog=catalog,
            schema_ids=schema_ids,
        )
        if variable_id is not None:
            resolved.append(variable_id)
        elif issue:
            issues.append(issue)
    return resolved, issues


def _find_dataset_with_columns(columns: list[str]) -> tuple[Path | None, list[dict[str, Any]]]:
    datasets_dir = config.TRIO_DATASETS_DIR
    if not datasets_dir.is_dir():
        return None, []
    needed = set(columns)
    for path in sorted(datasets_dir.glob("*.jsonl")):
        rows = _read_jsonl(path)
        if not rows:
            continue
        present = {key for row in rows[:20] for key in row}
        if needed.issubset(present):
            return path, rows
    return None, []


def _count_non_missing(rows: list[Mapping[str, Any]], column: str) -> int:
    return sum(1 for row in rows if row.get(column) not in (None, "", "nan", "NaN"))


def _suppressed_counts(rows: list[Mapping[str, Any]], column: str) -> dict[str, Any]:
    from scripts.security.kanon_gate import suppress_small_cells

    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(column)
        key = "<MISSING>" if value in (None, "", "nan", "NaN") else str(value)
        counts[key] = counts.get(key, 0) + 1
    return suppress_small_cells(counts, k=5)


def _persist_catalog_analysis_code(
    *,
    dataset_name: str,
    outcome_id: str,
    predictor_ids: list[str],
) -> Path:
    output_dir = config.AGENT_OUTPUT_DIR / "code"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = validate_agent_write(
        output_dir / f"catalog_descriptive_{uuid.uuid4().hex[:12]}.py"
    )
    predictors_literal = ", ".join(repr(value) for value in predictor_ids)
    code = f'''"""Catalog-bound descriptive analysis generated by RePORT AI Portal.

Dataset: {dataset_name}
Outcome variable: {outcome_id}
Predictor variables: {", ".join(predictor_ids) if predictor_ids else "(none)"}
"""

import json
from pathlib import Path

import config

dataset_path = config.TRIO_DATASETS_DIR / {dataset_name + ".jsonl"!r}
rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
outcome = {outcome_id!r}
predictors = [{predictors_literal}]

def count_non_missing(column):
    return sum(1 for row in rows if row.get(column) not in (None, "", "nan", "NaN"))

print("N", len(rows))
print(outcome, "non-missing", count_non_missing(outcome))
for predictor in predictors:
    print(predictor, "non-missing", count_non_missing(predictor))
'''
    path.write_text(code, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def _run_catalog_bound_study_analysis(
    *,
    cohort: str,
    outcome: str,
    predictors: str,
) -> str:
    # Task 6a: scripts.source_truth.analysis_binding removed (doomed module).
    # The new pipeline produces no AnalysisBinding objects; this code path
    # is retired — see docs/runbook_sot_build.md.
    return (
        "Catalog-bound analysis via AnalysisBinding is no longer available. "
        "The Source Truth pipeline has been updated; please re-run "
        "`python -m scripts.source_truth.study_intake` to rebuild the catalog, "
        "then use answer_catalog_question for variable metadata."
    )


@tool
@phi_safe_return
def run_study_analysis(
    cohort: str,
    outcome: str = "",
    predictors: str = "",
    analysis_types: str = "",
    plot_types: str = "",
) -> str:
    """Run a catalog-bound epidemiological analysis on study data.

    In the current catalog-binding runtime, ``outcome`` and ``predictors``
    should be exact analysis-queryable variable IDs from the published
    catalog/Dataset Schema. The tool validates those bindings and emits a
    descriptive result plus a reproducible code file. Custom regression
    execution should use ``run_python_analysis`` after metadata resolution.

    The legacy StudyKnowledge regression runner is still reachable only when
    ``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE=1`` is explicitly set.

    Args:
        cohort: Which cohort to analyze — "cohort_a" (index cases) or "cohort_b" (household contacts).
        outcome: Exact analysis-queryable outcome variable ID in the catalog
            binding runtime. Legacy aliases such as "recurrence" are accepted
            only behind the legacy StudyKnowledge override.
        predictors: Comma-separated exact analysis-queryable predictor
            variable IDs in the catalog binding runtime.
        analysis_types: Comma-separated analysis types from: univariate, multivariate, interaction, descriptive. Default: all.
        plot_types: Comma-separated plot types from: violin, scatter, interaction_violin, interaction_scatter. Default: all.
    """
    import traceback

    if not cohort:
        return "Missing required parameter 'cohort'. Use 'cohort_a' (index cases) or 'cohort_b' (household contacts)."

    from scripts.ai_assistant.analytical_engine import is_catalog_binding_enabled

    if is_catalog_binding_enabled():
        return _run_catalog_bound_study_analysis(
            cohort=cohort,
            outcome=outcome,
            predictors=predictors,
        )

    from scripts.ai_assistant.analytical_engine import run_full_analysis
    from scripts.ai_assistant.study_knowledge import StudyKnowledge

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
        catalog_path = getattr(config, "LLM_SOURCE_STUDY_METADATA_CATALOG_PATH", None)
        if isinstance(catalog_path, Path):
            candidates.append(catalog_path)
        llm_source_dir = getattr(config, "STUDY_LLM_SOURCE_DIR", None)
        if isinstance(llm_source_dir, Path):
            candidates.append(llm_source_dir / "study_metadata" / "catalog.json")
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


def _load_study_metadata_evidence_pack(variable_id: str) -> Mapping[str, Any] | None:
    """Load one variable's public evidence record from per-form evidence packs."""

    packs_dir = getattr(config, "LLM_SOURCE_EVIDENCE_PACKS_DIR", None)
    if not isinstance(packs_dir, Path) or not packs_dir.is_dir():
        return None
    for path in sorted(packs_dir.glob("*.json")):
        try:
            validate_agent_read(path)
            with path.open("r", encoding="utf-8") as fh:
                body = json.load(fh)
        except (OSError, ValueError, PermissionError, json.JSONDecodeError):
            continue
        if not isinstance(body, Mapping):
            continue
        if body.get("variable_id") == variable_id:
            return body
        variables = body.get("variables")
        if not isinstance(variables, list):
            continue
        for item in variables:
            if isinstance(item, Mapping) and item.get("variable_id") == variable_id:
                enriched = dict(item)
                enriched.setdefault("form", body.get("form"))
                enriched.setdefault("study", body.get("study"))
                return enriched
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
    # Task 6a: scripts.source_truth.retrieval.SourceTruthRetriever retired pending
    # Task 6 relocation — see docs/runbook_sot_build.md.
    raise NotImplementedError(
        "SourceTruthRetriever retired pending Task 6 relocation — see docs/runbook_sot_build.md"
    )


# ============================================================================
# Evidence-report + citation tools
# ============================================================================


_CANONICAL_QUESTION_HINTS: dict[str, tuple[str, ...]] = {
    "q01_cohort_a_univariate": (
        "cohort a", "univariate", "tb recurrence predictors", "single-variable",
    ),
    "q02_cohort_a_multivariate_interactions": (
        "cohort a", "multivariate", "backward selection", "interactions",
        "smoking age", "alcohol smoking",
    ),
    "q03_cohort_b_univariate": (
        "cohort b", "univariate", "household contact", "predictors",
    ),
    "q04_cohort_b_multivariate_interactions": (
        "cohort b", "multivariate", "interactions",
    ),
    "q05_hiv_test_result_distribution": (
        "hiv", "test result", "distribution", "serostatus",
    ),
    "q06_cohort_a_index_case_inclusion_exclusion": (
        "index case", "inclusion", "exclusion", "cohort a eligibility",
    ),
    "q07_tb_relapse_vs_treatment_failure": (
        "relapse", "treatment failure", "definition difference",
    ),
    "q08_household_contact_definition": (
        "household contact", "definition", "shared household",
    ),
    "q09_drug_susceptibility_tests_and_timing": (
        "drug susceptibility", "dst", "timing", "first-line",
    ),
    "q10_household_contact_followup_schedule_specimens": (
        "household contact", "follow-up schedule", "specimens",
    ),
    "q11_variables_available_for_relapse": (
        "variables for relapse", "relapse variables", "what variables",
        "fields for relapse",
    ),
}


_FIGURE_EXTS = (".png", ".jpg", ".jpeg", ".svg", ".webp")


def _persist_evidence_report_code(question_id: str) -> Path | None:
    """Write a PHI-safe reproducer script for a canonical evidence report.

    The script imports ``answer_question`` and prints the resulting markdown,
    matching what the chat surface rendered. Path is a hex-digest filename
    under ``AGENT_OUTPUT_DIR / "code"`` so the PHI gate never sees a
    human-readable token that could trip a pattern. Returns ``None`` if the
    write path cannot be validated.
    """
    try:
        output_dir = config.AGENT_OUTPUT_DIR / "code"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = validate_agent_write(
            output_dir / f"evidence_{uuid.uuid4().hex[:12]}.py"
        )
    except Exception:
        return None
    code = (
        '"""Reproducer for a canonical evidence report.\n\n'
        "Run this script to regenerate the same markdown the chat surface\n"
        "displayed (figures land in tmp2/figures by default).\n"
        '"""\n\n'
        "from scripts.ai_assistant.report_engine import answer_question\n\n"
        f"question_id = {question_id!r}\n"
        "bundle = answer_question(question_id)\n"
        "print(bundle.markdown)\n"
        'print("Figures:", [str(f) for f in bundle.figures])\n'
    )
    path.write_text(code, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def _persist_custom_evidence_report_code(
    *,
    outcome_form: str,
    outcome_field: str,
    cohort_id: str,
    predictor_ids: list[str],
    analysis_type: str,
    outcome_positive_values: list[str] | None,
) -> Path | None:
    """Write a PHI-safe reproducer script for a custom evidence report.

    Mirrors ``_persist_evidence_report_code`` but pins all six call-args of
    ``answer_custom_analysis`` as literal arguments. Hex-digest filename
    keeps the PHI gate happy.
    """
    try:
        output_dir = config.AGENT_OUTPUT_DIR / "code"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = validate_agent_write(
            output_dir / f"custom_evidence_{uuid.uuid4().hex[:12]}.py"
        )
    except Exception:
        return None
    code = (
        '"""Reproducer for a custom evidence report.\n\n'
        "Re-runs the same answer_custom_analysis call the chat surface used.\n"
        '"""\n\n'
        "from scripts.ai_assistant.report_engine import answer_custom_analysis\n\n"
        f"outcome_form = {outcome_form!r}\n"
        f"outcome_field = {outcome_field!r}\n"
        f"cohort_id = {cohort_id!r}\n"
        f"predictor_ids = {list(predictor_ids)!r}\n"
        f"analysis_type = {analysis_type!r}\n"
        f"outcome_positive_values = {outcome_positive_values!r}\n\n"
        "bundle = answer_custom_analysis(\n"
        "    outcome_form=outcome_form,\n"
        "    outcome_field=outcome_field,\n"
        "    outcome_positive_values=outcome_positive_values,\n"
        "    cohort_id=cohort_id,\n"
        "    predictor_ids=predictor_ids,\n"
        "    analysis_type=analysis_type,\n"
        ")\n"
        "print(bundle.markdown)\n"
        'print("Figures:", [str(f) for f in bundle.figures])\n'
    )
    path.write_text(code, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def _rewrite_figure_paths_for_streaming(markdown: str, figures: list[Path]) -> str:
    """Convert figure references to ``<RPLN_FIGURE:abspath>`` markers so the
    chat streaming layer renders them via ``st.image`` instead of leaving
    broken relative links in the markdown.

    Three patterns are handled:
      1. Markdown image syntax: ``![alt](relative/path.png)``
      2. Bulleted backtick-wrapped path: ``- `relative/path.png` ``
      3. Bare backtick-wrapped path inline: `` `relative/path.png` ``
    """
    if not figures:
        return markdown
    abs_by_name = {fig.name: str(fig.resolve()) for fig in figures if fig.exists()}
    if not abs_by_name:
        return markdown

    image_md = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    bullet_path = re.compile(
        r"^[ \t]*[-*][ \t]+`([^`]+\.(?:png|jpg|jpeg|svg|webp))`[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    inline_path = re.compile(
        r"`([^`\n]+\.(?:png|jpg|jpeg|svg|webp))`",
        re.IGNORECASE,
    )

    def _resolve(path_str: str) -> str | None:
        return abs_by_name.get(Path(path_str.strip()).name)

    def _replace_image_md(match: re.Match[str]) -> str:
        abspath = _resolve(match.group(1))
        return f"<RPLN_FIGURE:{abspath}>" if abspath else match.group(0)

    def _replace_bullet(match: re.Match[str]) -> str:
        abspath = _resolve(match.group(1))
        return f"<RPLN_FIGURE:{abspath}>" if abspath else match.group(0)

    def _replace_inline(match: re.Match[str]) -> str:
        abspath = _resolve(match.group(1))
        return f"<RPLN_FIGURE:{abspath}>" if abspath else match.group(0)

    rewritten = image_md.sub(_replace_image_md, markdown)
    rewritten = bullet_path.sub(_replace_bullet, rewritten)
    rewritten = inline_path.sub(_replace_inline, rewritten)
    return rewritten


@tool
@phi_safe_return
def produce_evidence_report(question_id: str) -> str:
    """Produce a structured, PHI-safe evidence report for a canonical study question.

    Call this tool when the user asks one of the canonical RePORT study questions —
    e.g. "what are the univariate predictors of TB recurrence in cohort A",
    "household contact follow-up schedule", or "variables available for relapse".
    The tool runs the offline-validated ``report_engine`` pipeline: it loads only
    PHI-scrubbed source files, builds the cohort, runs the deterministic
    statsmodels regression (where applicable), generates figures, suppresses any
    cell below k=5, and returns chat-ready markdown.

    Supported ``question_id`` values (returned by ``list_questions`` in the
    report_engine):

    * ``q01_cohort_a_univariate`` — Cohort A univariate predictors of TB recurrence
    * ``q02_cohort_a_multivariate_interactions`` — Cohort A multivariate (backward
      selection) + interaction tests (smoking x age, alcohol x smoking)
    * ``q03_cohort_b_univariate`` — Cohort B univariate predictors
    * ``q04_cohort_b_multivariate_interactions`` — Cohort B multivariate + interactions
    * ``q05_hiv_test_result_distribution`` — HIV test result distribution
    * ``q06_cohort_a_index_case_inclusion_exclusion`` — Cohort A index-case
      inclusion / exclusion (protocol knowledge)
    * ``q07_tb_relapse_vs_treatment_failure`` — TB relapse vs treatment failure
      definitions (protocol knowledge)
    * ``q08_household_contact_definition`` — household-contact definition
    * ``q09_drug_susceptibility_tests_and_timing`` — DST panels and timing
    * ``q10_household_contact_followup_schedule_specimens`` — household contact
      follow-up schedule and specimens
    * ``q11_variables_available_for_relapse`` — variables available for relapse
      (schema-derived reference)

    PHI guarantees:
        * No SUBJID / FID surfaced.
        * Dates are jittered per SANT before they reach this pipeline.
        * Cells below k=5 are suppressed.
        * The streaming layer runs an additional fail-closed PHI scrub on the
          final assembled response before it reaches the UI.

    Args:
        question_id: One of the canonical IDs above. Pass the exact string.

    Returns:
        Markdown ready for chat display. Figure references are rewritten to the
        ``<RPLN_FIGURE:abspath>`` marker the streaming layer renders via
        ``st.image``. If the live cohort cannot be built (missing source-of-truth
        files, dependency error), returns a short blocked-status message rather
        than partial results.
    """
    from scripts.ai_assistant.report_engine import QUESTION_IDS, answer_question

    qid = (question_id or "").strip()
    if qid not in QUESTION_IDS:
        return json.dumps(
            {
                "error": "unknown question_id",
                "received": question_id,
                "valid_ids": list(QUESTION_IDS),
            },
            indent=2,
        )

    try:
        bundle = answer_question(qid)
    except Exception as exc:
        return json.dumps(
            {
                "error": "report_engine failure",
                "question_id": qid,
                "detail": sanitise_traceback(str(exc)),
            },
            indent=2,
        )

    if bundle.phi_status == "blocked":
        return (
            f"**Report blocked for `{qid}`.** The evidence engine could not produce a "
            "PHI-safe response. This usually means the source-of-truth files for the "
            "study have not been built yet. Please run the data pipeline before "
            "re-asking, or contact the maintainer.\n\n"
            f"{bundle.markdown}"
        )

    rendered = _rewrite_figure_paths_for_streaming(bundle.markdown, bundle.figures)
    code_path = _persist_evidence_report_code(qid)
    if code_path is not None:
        rendered = f"{rendered}\n\n<RPLN_CODE:{code_path}>"
    return rendered


@tool
@phi_safe_return
def produce_custom_evidence_report(
    outcome_form: str,
    outcome_field: str,
    cohort_id: str,
    predictor_ids: list[str],
    analysis_type: str = "univariate",
    outcome_positive_values: list[str] | None = None,
) -> str:
    """Produce a tmp2-style PHI-safe report for an arbitrary outcome/cohort/predictor combo.

    Use when the user asks for a study analysis that doesn't match one of the 11
    canonical ``produce_evidence_report`` IDs — for example "univariate
    predictors of HIV positivity", "cohort A stratified by sex", or
    "predictors of MDR-TB". This runs the same offline-validated
    ``report_engine`` pipeline (PHI-scrubbed source files, deterministic
    statsmodels regression, figure generation with k=5 small-cell suppression)
    used by the 11 canonical questions.

    Args:
        outcome_form: form id where the outcome lives (e.g. ``"6_HIV"``,
            ``"98A_FOA"``). Accepts either the form prefix or the full
            ``<form>.jsonl`` filename.
        outcome_field: exact field name (e.g. ``"HIV_HIV"``,
            ``"FOA_COHAOUT"``).
        cohort_id: ``"cohort_a"`` or ``"cohort_b"`` (same identifiers used by
            the existing 11 canonical handlers).
        predictor_ids: logical predictor keys
            (``"malnutrition"``, ``"diabetes"``, ``"alcohol"``, ``"smoking"``,
            ``"age"``, ``"sex"``, ``"bmi"``) or known field names. At least
            one is required.
        analysis_type: ``"univariate"`` | ``"multivariate"`` | ``"interactions"``.
        outcome_positive_values: when the outcome is not registered in
            ``study_knowledge.yaml`` (e.g. ``HIV_HIV``), pass the exact value
            strings that count as the positive class — for ``HIV_HIV`` this
            is ``["Positive"]``; for ``FOA_COHAOUT`` (TB recurrence) it is
            ``["Bacteriologic relapse","Bacteriologic failure","Clinical Relapse","Clinical Failure"]``.
            Omit (pass ``None``) for registered outcomes so the default
            mapping in ``study_knowledge.yaml`` is used.

    Returns:
        Markdown ready for chat display, with figure markers (``<RPLN_FIGURE:>``)
        and the PHI handling footer. On validation failure or insufficient data,
        returns a short blocked-status message rather than partial results.
    """
    from scripts.ai_assistant.report_engine import (
        CustomAnalysisError,
        answer_custom_analysis,
    )

    if not isinstance(predictor_ids, list) or not predictor_ids:
        return json.dumps(
            {
                "error": "predictor_ids must be a non-empty list of strings",
                "received": predictor_ids,
            },
            indent=2,
        )
    if analysis_type not in ("univariate", "multivariate", "interactions"):
        return json.dumps(
            {
                "error": "invalid analysis_type",
                "received": analysis_type,
                "valid": ["univariate", "multivariate", "interactions"],
            },
            indent=2,
        )
    if outcome_positive_values is not None and (
        not isinstance(outcome_positive_values, list)
        or not all(isinstance(v, str) for v in outcome_positive_values)
    ):
        return json.dumps(
            {
                "error": "outcome_positive_values must be a list of strings or null",
                "received": outcome_positive_values,
            },
            indent=2,
        )

    try:
        bundle = answer_custom_analysis(
            outcome_form=outcome_form,
            outcome_field=outcome_field,
            outcome_positive_values=outcome_positive_values,
            cohort_id=cohort_id,
            predictor_ids=list(predictor_ids),
            analysis_type=analysis_type,  # type: ignore[arg-type]
        )
    except CustomAnalysisError as exc:
        return json.dumps(
            {
                "error": "custom analysis rejected",
                "detail": str(exc),
                "outcome_form": outcome_form,
                "outcome_field": outcome_field,
                "cohort_id": cohort_id,
                "analysis_type": analysis_type,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "error": "report_engine failure",
                "detail": sanitise_traceback(str(exc)),
            },
            indent=2,
        )

    if bundle.phi_status == "blocked":
        return (
            f"**Report blocked.** The evidence engine could not produce a "
            "PHI-safe response for this outcome/cohort/predictor combination.\n\n"
            f"{bundle.markdown}"
        )

    rendered = _rewrite_figure_paths_for_streaming(bundle.markdown, bundle.figures)
    code_path = _persist_custom_evidence_report_code(
        outcome_form=outcome_form,
        outcome_field=outcome_field,
        cohort_id=cohort_id,
        predictor_ids=list(predictor_ids),
        analysis_type=analysis_type,
        outcome_positive_values=outcome_positive_values,
    )
    if code_path is not None:
        rendered = f"{rendered}\n\n<RPLN_CODE:{code_path}>"
    return rendered


@tool
@phi_safe_return
def cite_source(form_id: str, field_id: str) -> str:
    """Return a deterministic (file, line, snippet) citation for a study variable.

    Use this whenever you need to back a variable claim with a verifiable
    provenance reference. The citation is looked up in the indexed corpus of
    form-policy YAMLs, LLM source JSONL schemas, and study-config YAMLs — so
    the result is a real file location, never a fabricated string.

    Typical usage: when answering a question about a form field
    (e.g. ``FOA_COHAOUT``, ``FA_RLPSDAT``), call ``cite_source(form_id="98A",
    field_id="FOA_COHAOUT")`` and embed the returned ``file:line`` in your
    answer next to the claim.

    Args:
        form_id: The form identifier (e.g. ``"98A"``, ``"99A"``, ``"10"``).
            Short prefixes are accepted; the tool resolves to the matching
            policy YAML.
        field_id: The exact field name as it appears in the policy YAML
            (e.g. ``"FOA_COHAOUT"``, ``"FA_RLPSDAT"``).

    Returns:
        A JSON string with ``file`` (repo-relative path), ``line`` (1-indexed),
        ``snippet`` (~200 chars), ``matched_term`` (what the lookup matched),
        and ``source_kind`` (``form_policy`` | ``llm_jsonl`` | ``study_config``
        | ``dataset_schema``). If no citation is found, returns a JSON object
        with ``error: "no citation"`` — never a guessed location.
    """
    from scripts.ai_assistant.citations import (
        CitationNotFound,
        cite_variable,
    )

    form = (form_id or "").strip()
    field = (field_id or "").strip()
    if not form or not field:
        return json.dumps(
            {"error": "form_id and field_id are both required"}, indent=2
        )
    try:
        citation = cite_variable(form, field)
    except CitationNotFound as exc:
        return json.dumps(
            {
                "error": "no citation",
                "form_id": form,
                "field_id": field,
                "detail": str(exc),
            },
            indent=2,
        )
    return json.dumps(
        {
            "file": citation.file,
            "line": citation.line,
            "snippet": citation.snippet,
            "matched_term": citation.matched_term,
            "source_kind": citation.source_kind,
        },
        indent=2,
        ensure_ascii=False,
    )


# ============================================================================
# Tool registry
# ============================================================================

ALL_TOOLS = [
    search_variables,
    query_dataset,
    list_available_datasets,
    get_dataset_stats,
    run_python_analysis,
    run_study_analysis,
    answer_catalog_question,
    produce_evidence_report,
    produce_custom_evidence_report,
    cite_source,
]
