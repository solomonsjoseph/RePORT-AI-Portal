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
1.  list_llm_source — browse the PHI-scrubbed ``llm_source/`` tree
2.  search_llm_source — full-text search across ``llm_source/`` (protocol/definitions)
3.  read_llm_source_file — read a specific ``llm_source/`` file (e.g. a policy YAML)
4.  search_variables — dataset column search (dictionary fallback)
5.  query_dataset — structural query on a JSONL dataset
6.  list_available_datasets — list available PHI-scrubbed datasets
7.  get_dataset_stats — summary statistics for a dataset (record counts, columns)
8.  run_python_analysis — sandboxed code execution for statistical analysis (primary)
9.  answer_catalog_question — variable metadata lookup via policy SoT YAMLs
10. cite_source — deterministic (file, line, snippet) citation for form fields

The agent resolves variables and protocol facts through the ``llm_source``
retrieval tools and performs all statistical analysis through the sandboxed
``run_python_analysis`` tool.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

import config
from scripts.ai_assistant.file_access import (
    validate_agent_read,
)
from scripts.ai_assistant.phi_safe import (
    phi_safe_return,
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
                    record_count += 1
                    if not columns:
                        try:
                            rec = json.loads(line)
                            if isinstance(rec, dict):
                                columns.update(str(k) for k in rec if k not in _INTERNAL_COLUMNS)
                        except json.JSONDecodeError:
                            pass
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

_CATALOG_MATCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "about",
        "and",
        "are",
        "can",
        "column",
        "columns",
        "field",
        "fields",
        "for",
        "form",
        "from",
        "has",
        "have",
        "how",
        "into",
        "is",
        "its",
        "me",
        "metadata",
        "of",
        "on",
        "question",
        "show",
        "study",
        "tell",
        "the",
        "this",
        "variable",
        "variables",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)


def _catalog_query_identifier_tokens(question: str) -> set[str]:
    """Return possible case-insensitive variable-id tokens from a question."""
    return {
        token.upper() for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", question) if len(token) >= 3
    }


def _catalog_meaningful_tokens(text: str) -> set[str]:
    """Tokenize prose for fuzzy catalog matching without common question words."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _CATALOG_MATCH_STOPWORDS
    }


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

    identifier_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]*", query) if len(t) >= 3
    }
    exact_matches = [
        var for var in variables if str(var.get("variable_name") or "").lower() in identifier_tokens
    ]
    if exact_matches:
        matches = [
            {
                "variable_name": var.get("variable_name", ""),
                "form_name": var.get("form_name", ""),
                "dataset": var.get("dataset", ""),
                "description": var.get("description", ""),
                "source": var.get("source", ""),
            }
            for var in exact_matches
        ]
        result = json.dumps(matches[:12], indent=2, ensure_ascii=False)
        tool_cache.put("search_variables", result, query=query)
        return result

    tokens = _catalog_meaningful_tokens(query)
    if not tokens:
        return f"No variables found matching '{query}'."

    matches = []
    for var in variables:
        name = (var.get("variable_name") or "").lower()
        desc = (var.get("description") or "").lower()
        if any(t in name or t in desc for t in tokens):
            matches.append(
                {
                    "variable_name": var.get("variable_name", ""),
                    "form_name": var.get("form_name", ""),
                    "dataset": var.get("dataset", ""),
                    "description": var.get("description", ""),
                    "source": var.get("source", ""),
                }
            )

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

    # Run permission check first to ensure security boundary is not bypassed by cache
    try:
        validate_agent_read(matched_file)
    except PermissionError as exc:
        return f"Access denied: {exc}"

    dataset_key = matched_file.name
    hit = tool_cache.get(
        "query_dataset",
        dataset_name=dataset_key,
        columns=columns,
        filter_column=filter_column,
        filter_value=filter_value,
        limit=limit,
    )
    if hit is not None:
        return hit

    limit = min(max(limit, 1), 100)

    # Read records with optimization when no filter is applied
    if not filter_column:
        all_records = _read_jsonl(matched_file, max_records=limit)
        try:
            with open(validate_agent_read(matched_file), encoding="utf-8") as fh:
                real_total = sum(1 for line in fh if line.strip())
        except OSError:
            real_total = len(all_records)
    else:
        all_records = _read_jsonl(matched_file)
        real_total = len(all_records)

    if not all_records:
        res = f"Dataset '{matched_file.name}' is empty."
        tool_cache.put(
            "query_dataset",
            res,
            dataset_name=dataset_key,
            columns=columns,
            filter_column=filter_column,
            filter_value=filter_value,
            limit=limit,
        )
        return res

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

    gating_rows = [{k: v for k, v in rec.items() if k not in _INTERNAL_COLUMNS} for rec in filtered]
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

    res = json.dumps(
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
    tool_cache.put(
        "query_dataset",
        res,
        dataset_name=dataset_key,
        columns=columns,
        filter_column=filter_column,
        filter_value=filter_value,
        limit=limit,
    )
    return res


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
            logger.warning("list_available_datasets: skipping out-of-zone file %s", jsonl_path)
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
                record_count += 1
                if not all_columns:
                    try:
                        rec = json.loads(line)
                        all_columns.update(rec.keys())
                    except json.JSONDecodeError:
                        pass
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
        re.search(rf"\b{re.escape(marker)}\b", text, re.IGNORECASE) for marker in row_level_markers
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


# Thread-safe in-memory cache for policy summaries to avoid repeated disk reads and safe_load parsing
_POLICY_SUMMARIES_CACHE: dict[Path, dict[str, Any]] = {}


@tool
@phi_safe_return
def answer_catalog_question(question: str) -> str:
    """Answer a study-variable metadata question through published policy SoT YAMLs.

    Use this for ordinary questions about retained study variables: their
    label, dataset column, form, options, and provenance. The plugin-published
    Source Truth set under ``llm_source/SoT/<pair>/`` is the canonical metadata
    layer; older ``llm_source/source_truth`` files are compatibility-only.
    Prefer this tool over ``search_variables`` for boundary-sensitive questions
    about whether a variable is analysable, source-only, or dropped.

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
    """Answer a study-variable metadata question by searching policy SoT YAMLs."""
    from scripts.ai_assistant.sot_joined_view import (
        build_joined_query_view,
        find_dataset_schema_for_policy,
    )
    from scripts.ai_assistant.sot_loader import (
        find_policy_yaml,
        load_policy_yaml,
        summarize_policy,
    )

    if _query_looks_conversational(question):
        return _CONVERSATIONAL_REFUSAL_MESSAGE

    hit = tool_cache.get("answer_catalog_question", question=question)
    if hit is not None:
        return hit

    repo_root = Path(config.REPO_ROOT) if hasattr(config, "REPO_ROOT") else Path(".")
    query_identifiers = _catalog_query_identifier_tokens(question)
    query_tokens = _catalog_meaningful_tokens(question)

    # Try to identify a study from known output dirs; fall back to searching all.
    output_dir = repo_root / "output"
    study_dirs = sorted(output_dir.iterdir()) if output_dir.is_dir() else []
    study_dirs = [d for d in study_dirs if d.is_dir()]

    matches: list[dict[str, Any]] = []
    for study_dir in study_dirs:
        all_paths = find_policy_yaml(study_dir.name, None, repo_root)
        for path in all_paths:
            # Check the in-memory cache for policy summaries first
            summary = _POLICY_SUMMARIES_CACHE.get(path)
            if summary is None:
                try:
                    data = load_policy_yaml(path)
                    summary = summarize_policy(data)
                    _POLICY_SUMMARIES_CACHE[path] = summary
                except ValueError:
                    continue
            # Prefer exact variable-id matches, then require meaningful token
            # overlap. A single generic question word like "what" must never
            # decide the catalog answer.
            for var_name, var_meta in summary["variables"].items():
                if var_name.upper() in query_identifiers:
                    matches.append(
                        {
                            "variable_id": var_name,
                            "summary": summary,
                            "source": str(path),
                            "priority": 0,
                            "score": 100,
                        }
                    )
                    continue
                if isinstance(var_meta, dict):
                    question_text = str(var_meta.get("pdf_question") or "")
                    question_overlap = query_tokens & _catalog_meaningful_tokens(question_text)
                    name_overlap = query_tokens & _catalog_meaningful_tokens(
                        var_name.replace("_", " ")
                    )
                    score = len(question_overlap) * 2 + len(name_overlap)
                    if (
                        len(question_overlap) >= 2
                        or (question_overlap and name_overlap)
                        or len(name_overlap) >= 2
                    ):
                        matches.append(
                            {
                                "variable_id": var_name,
                                "summary": summary,
                                "source": str(path),
                                "priority": 1,
                                "score": score,
                            }
                        )

    if not matches:
        # Return all available SoT metadata across studies as a catalog dump.
        all_summaries = []
        for study_dir in study_dirs:
            for path in find_policy_yaml(study_dir.name, None, repo_root):
                summary = _POLICY_SUMMARIES_CACHE.get(path)
                if summary is None:
                    try:
                        data = load_policy_yaml(path)
                        summary = summarize_policy(data)
                        _POLICY_SUMMARIES_CACHE[path] = summary
                    except ValueError:
                        continue
                all_summaries.append(summary)
        if not all_summaries:
            res = json.dumps(
                {
                    "question": question,
                    "answer": (
                        "No policy SoT YAMLs found. Run Load Study to activate "
                        "the report-ai-study-pipeline plugin, or run the "
                        "`sot-lean-generator` phase for the affected form and publish "
                        "the result under `llm_source/SoT/<pair>/pdf/`."
                    ),
                    "variable_ids": [],
                    "audit_only": False,
                    "analysis_queryable": False,
                    "needs_clarification": True,
                },
                indent=2,
            )
            tool_cache.put("answer_catalog_question", res, question=question)
            return res
        res = json.dumps(
            {
                "question": question,
                "answer": "No exact variable match found. Available SoT catalog below.",
                "variable_ids": [],
                "audit_only": False,
                "analysis_queryable": False,
                "needs_clarification": True,
                "catalog": all_summaries,
            },
            indent=2,
        )
        tool_cache.put("answer_catalog_question", res, question=question)
        return res

    best = sorted(
        matches,
        key=lambda item: (
            int(item.get("priority", 99)),
            -int(item.get("score", 0)),
            str(item.get("source", "")),
            str(item.get("variable_id", "")),
        ),
    )[0]
    summary = best["summary"]
    var_id = best["variable_id"]
    var_meta = summary["variables"].get(var_id, {})
    phi_flag = var_meta.get("phi") if isinstance(var_meta, dict) else None
    analysis_queryable = phi_flag not in ("drop",)
    metadata: Any = var_meta
    source_path = Path(str(best["source"]))

    # Try loading pre-compiled joined query view from file first to save dynamic parsing and join CPU/IO
    form_id = source_path.name
    for suffix in ("_policy.yaml", "_policy.lean.yaml", ".lean.yaml", ".yaml"):
        if form_id.endswith(suffix):
            form_id = form_id[: -len(suffix)]
            break
    joined_view_path = source_path.parent.parent / "joined" / f"{form_id}_joined_query_view.yaml"

    loaded_from_file = False
    if joined_view_path.is_file():
        try:
            validate_agent_read(joined_view_path)
            import yaml

            with open(joined_view_path, encoding="utf-8") as fh:
                joined_view = yaml.safe_load(fh)
            joined_variables = joined_view.get("variables")
            if isinstance(joined_variables, Mapping):
                joined_meta = joined_variables.get(var_id)
                if isinstance(joined_meta, Mapping):
                    metadata = dict(joined_meta)
                    loaded_from_file = True
        except Exception:
            logger.debug(
                "joined-view metadata load failed for %s; falling back to schema",
                var_id,
                exc_info=True,
            )

    if not loaded_from_file:
        schema_path = find_dataset_schema_for_policy(source_path)
        if schema_path is not None:
            try:
                joined_view = build_joined_query_view(source_path, schema_path)
                joined_variables = joined_view.get("variables")
                if isinstance(joined_variables, Mapping):
                    joined_meta = joined_variables.get(var_id)
                    if isinstance(joined_meta, Mapping):
                        metadata = dict(joined_meta)
            except ValueError:
                metadata = var_meta
    answer_text = json.dumps(
        {
            "variable_id": var_id,
            "metadata": metadata,
            "form": summary["form"],
            "study": summary["study"],
        },
        indent=2,
    )
    res = json.dumps(
        {
            "question": question,
            "answer": answer_text,
            "variable_ids": [var_id],
            "audit_only": False,
            "analysis_queryable": analysis_queryable,
            "needs_clarification": False,
            "source": best["source"],
        },
        indent=2,
    )
    tool_cache.put("answer_catalog_question", res, question=question)
    return res


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
        CitationNotFoundError,
        cite_variable,
    )

    form = (form_id or "").strip()
    field = (field_id or "").strip()
    if not form or not field:
        return json.dumps({"error": "form_id and field_id are both required"}, indent=2)

    hit = tool_cache.get("cite_source", form_id=form, field_id=field)
    if hit is not None:
        return hit

    try:
        citation = cite_variable(form, field)
    except CitationNotFoundError as exc:
        res = json.dumps(
            {
                "error": "no citation",
                "form_id": form,
                "field_id": field,
                "detail": str(exc),
            },
            indent=2,
        )
        tool_cache.put("cite_source", res, form_id=form, field_id=field)
        return res
    res = json.dumps(
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
    tool_cache.put("cite_source", res, form_id=form, field_id=field)
    return res


# ============================================================================
# Tool 11-13: explore the PHI-scrubbed llm_source tree (list / search / read)
# ============================================================================
#
# These three tools give the LLM first-class, bounded access to the published
# ``output/{STUDY}/llm_source/`` tree — the only place the agent may read study
# content. Every path resolves through ``validate_agent_read``, which admits
# llm_source/ (+ the agent's own state) and hard-denies the audit zone, raw
# data, and staging. The tree is already PHI-scrubbed at publish time (HIPAA
# Safe Harbor + India DPDPA/ICMR/Aadhaar), so the agent is free to search and
# read it; ``@phi_safe_return`` is a fail-closed backstop on every return.

_LLM_SOURCE_TEXT_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".jsonl", ".md", ".txt", ".csv"})
_LLM_SOURCE_MAX_READ_BYTES = 100_000
_LLM_SOURCE_MAX_SEARCH_HITS = 60
_LLM_SOURCE_SNIPPET_CHARS = 240


def _redact_blocking_phi(text: str) -> str:
    """Neutralise only *blocking-tier* PHI shapes so the fail-closed PHI gate
    passes, without mangling warn-tier clinical text.

    ``llm_source`` is already PHI-scrubbed at publish time, but its metadata
    carries jittered ISO dates that ``phi_gate_check`` blocks on. We tag those
    (and any residual government / subject identifiers) here so useful protocol
    text flows through. Crucially we do NOT apply the warn-tier name heuristic —
    that would rewrite legitimate clinical phrases like "Tuberculosis Treatment"
    as ``<PERSON_NAME_GENERIC>``.
    """
    from scripts.security.phi_patterns import BLOCKING_PATTERNS, SUBJECT_ID_PATTERNS

    out = text
    for label, pattern in BLOCKING_PATTERNS:
        out = pattern.sub(f"<{label}>", out)
    for pattern in SUBJECT_ID_PATTERNS:
        out = pattern.sub("<SUBJ>", out)
    return out


def _llm_source_rel(path: Path) -> str:
    """Render *path* relative to llm_source/ for user-friendly output."""
    try:
        return str(path.resolve().relative_to(Path(config.STUDY_LLM_SOURCE_DIR).resolve()))
    except ValueError:
        return path.name


def _resolve_within_llm_source(relative: str) -> Path:
    """Resolve a user-supplied relative path inside llm_source/, rejecting escapes.

    ``validate_agent_read`` enforces the zone via realpath/commonpath, so a
    traversal like ``../audit`` or an absolute path outside the tree is denied.
    """
    root = Path(config.STUDY_LLM_SOURCE_DIR)
    candidate = (root / relative.lstrip("/")).resolve() if relative else root.resolve()
    return validate_agent_read(candidate)


@tool
@phi_safe_return
def list_llm_source(subdir: str = "") -> str:
    """List files and folders in the study's PHI-scrubbed ``llm_source/`` tree.

    Use this to discover what is available before searching or reading. The
    tree holds the canonical study content the assistant may read:

    * ``SoT/<form>/`` — Source-Truth policy YAMLs: form questions, variable
      labels, coded options, definitions, inclusion/exclusion text, schedules.
    * ``dataset_schema/files/`` — the de-identified per-form ``.jsonl``
      datasets (use ``run_python_analysis`` to compute over these).
    * ``dataset_schema/`` and ``dictionary_mapping/`` — column dictionaries.

    Args:
        subdir: Optional path relative to ``llm_source/`` (e.g. ``"SoT/6_HIV"``).
            Empty lists the top level.

    Returns:
        JSON listing of immediate child files (with byte sizes) and subfolders.
    """
    try:
        target = _resolve_within_llm_source(subdir)
    except PermissionError as exc:
        return f"Access denied: {exc}"

    rel_path = _llm_source_rel(target)
    hit = tool_cache.get("list_llm_source", subdir=rel_path)
    if hit is not None:
        return hit

    if not target.exists():
        res = (
            f"No such path in llm_source/: {subdir!r}. "
            "Call list_llm_source() with no argument to see the top level."
        )
        tool_cache.put("list_llm_source", res, subdir=rel_path)
        return res
    if target.is_file():
        res = _redact_blocking_phi(
            json.dumps({"file": _llm_source_rel(target), "bytes": target.stat().st_size}, indent=2)
        )
        tool_cache.put("list_llm_source", res, subdir=rel_path)
        return res
    folders: list[str] = []
    files: list[dict[str, Any]] = []
    for child in sorted(target.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            folders.append(_llm_source_rel(child) + "/")
        else:
            files.append({"path": _llm_source_rel(child), "bytes": child.stat().st_size})
    res = _redact_blocking_phi(
        json.dumps(
            {"dir": _llm_source_rel(target) or ".", "folders": folders, "files": files},
            indent=2,
            ensure_ascii=False,
        )
    )
    tool_cache.put("list_llm_source", res, subdir=rel_path)
    return res


@tool
@phi_safe_return
def search_llm_source(query: str, subdir: str = "", max_results: int = 40) -> str:
    """Full-text search across the PHI-scrubbed ``llm_source/`` tree.

    Returns ``path:line: snippet`` hits for files whose text contains the
    query terms (case-insensitive). This is the primary way to answer
    protocol/definition questions — e.g. household-contact definition, TB
    relapse vs treatment failure, index-case inclusion/exclusion, follow-up
    schedule and specimens, drug-susceptibility tests and timing — and to
    locate which form/field a clinical concept maps to before analysis.

    Args:
        query: One or more search terms. Multi-word queries match lines
            containing any term; ranking favours lines matching more terms.
        subdir: Optional path relative to ``llm_source/`` to scope the search
            (e.g. ``"SoT"`` to search only Source-Truth policy YAMLs).
        max_results: Cap on returned hits (default 40, max 60).

    Returns:
        JSON list of ``{path, line, snippet}`` hits, most-relevant first.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return "Provide at least one search term of two or more characters."
    try:
        base = _resolve_within_llm_source(subdir)
    except PermissionError as exc:
        return f"Access denied: {exc}"
    if not base.exists():
        return f"No such path in llm_source/: {subdir!r}"

    rel_subdir = _llm_source_rel(base)
    hit = tool_cache.get(
        "search_llm_source",
        query=query,
        subdir=rel_subdir,
        max_results=max_results,
    )
    if hit is not None:
        return hit

    cap = max(1, min(int(max_results or 40), _LLM_SOURCE_MAX_SEARCH_HITS))
    pool_limit = cap * 8
    candidates = [base] if base.is_file() else sorted(base.rglob("*"))
    hits: list[dict[str, Any]] = []
    for path in candidates:
        if len(hits) >= pool_limit:
            break
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in _LLM_SOURCE_TEXT_SUFFIXES:
            continue
        try:
            validate_agent_read(path)
        except PermissionError:
            continue

        # Performance optimization: read content in bulk first to check for tokens
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            content_lower = content.lower()
            if not any(t in content_lower for t in tokens):
                continue
        except OSError:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            low = line.lower()
            score = sum(1 for t in tokens if t in low)
            if score:
                hits.append(
                    {
                        "path": _llm_source_rel(path),
                        "line": lineno,
                        "score": score,
                        # llm_source is already PHI-scrubbed, but its
                        # metadata carries jittered ISO dates that the
                        # fail-closed PHI gate treats as blocking. Tag
                        # them here so useful protocol text still flows.
                        "snippet": _redact_blocking_phi(line.strip()[:_LLM_SOURCE_SNIPPET_CHARS]),
                    }
                )
                if len(hits) >= pool_limit:
                    break

    if not hits:
        res = json.dumps(
            {"query": query, "hits": [], "note": "No matches in llm_source/."}, indent=2
        )
        tool_cache.put(
            "search_llm_source",
            res,
            query=query,
            subdir=rel_subdir,
            max_results=max_results,
        )
        return res
    hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
    top = [{"path": h["path"], "line": h["line"], "snippet": h["snippet"]} for h in hits[:cap]]
    res = json.dumps({"query": query, "hits": top}, indent=2, ensure_ascii=False)
    tool_cache.put(
        "search_llm_source",
        res,
        query=query,
        subdir=rel_subdir,
        max_results=max_results,
    )
    return res


@tool
@phi_safe_return
def read_llm_source_file(relative_path: str, max_bytes: int = 24000) -> str:
    """Read a single file from the PHI-scrubbed ``llm_source/`` tree.

    Use after ``list_llm_source`` / ``search_llm_source`` to read a specific
    SoT policy YAML or dictionary file in full. For large ``.jsonl`` datasets,
    prefer ``run_python_analysis`` — this returns only the first ``max_bytes``.

    Args:
        relative_path: Path relative to ``llm_source/`` (e.g.
            ``"SoT/6_HIV/6_HIV_policy.yaml"``).
        max_bytes: Maximum bytes to return (default 24000; capped at 100000).

    Returns:
        The file's UTF-8 text, truncated with a marker if longer than the cap.
    """
    if not relative_path or not relative_path.strip():
        return "Provide a file path relative to llm_source/ (see list_llm_source)."
    try:
        target = _resolve_within_llm_source(relative_path)
    except PermissionError as exc:
        return f"Access denied: {exc}"
    if not target.exists() or not target.is_file():
        return f"No such file in llm_source/: {relative_path!r}"

    rel_path = _llm_source_rel(target)
    hit = tool_cache.get(
        "read_llm_source_file",
        relative_path=rel_path,
        max_bytes=max_bytes,
    )
    if hit is not None:
        return hit

    limit = max(1, min(int(max_bytes or 24000), _LLM_SOURCE_MAX_READ_BYTES))
    try:
        raw = target.read_bytes()
    except OSError as exc:
        res = f"Could not read {relative_path!r}: {exc}"
        tool_cache.put(
            "read_llm_source_file",
            res,
            relative_path=rel_path,
            max_bytes=max_bytes,
        )
        return res
    # Tag jittered ISO dates / residual PHI shapes so the fail-closed PHI gate
    # does not withhold the whole (already-scrubbed) file over a date string.
    text = _redact_blocking_phi(raw[:limit].decode("utf-8", errors="replace"))
    if len(raw) > limit:
        text += (
            f"\n\n…[truncated at {limit} bytes; file is {len(raw)} bytes — "
            "narrow with run_python_analysis or read a more specific path]"
        )
    tool_cache.put(
        "read_llm_source_file",
        text,
        relative_path=rel_path,
        max_bytes=max_bytes,
    )
    return text


# ============================================================================
# Tool registry
# ============================================================================

ALL_TOOLS = [
    list_llm_source,
    search_llm_source,
    read_llm_source_file,
    search_variables,
    query_dataset,
    list_available_datasets,
    get_dataset_stats,
    run_python_analysis,
    answer_catalog_question,
    cite_source,
]
