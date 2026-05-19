"""Unified deduplication helpers for the RePORT AI Portal extraction pipeline.

This module provides a single place for **all** duplicate-detection and
duplicate-removal logic across the three extraction legs:

- **Dataset / Dictionary (JSONL):** duplicate *columns* inside tabular data
  (e.g. ``SUBJID`` and ``SUBJID2`` that contain identical values).
- **PDF (JSON):** duplicate *variables* within a single form (case-insensitive
  collisions) and cross-form duplicate variables (the same abbreviation
  appearing in multiple ``*_variables.json`` files).

Most functions in this module are **stateless-filesystem helpers**: they accept
data, return cleaned data (or a report), and never touch the filesystem.  File
I/O remains in the caller so that atomic-write semantics are preserved.

Note: ``remove_within_file_duplicates`` mutates its input ``data`` dict in-place
when ``dry_run=False``; see its docstring for the mutation contract.

Usage:
    >>> from scripts.extraction.dedup import (
    ...     clean_duplicate_columns,          # for DataFrames (dataset / dict)
    ...     remove_within_file_duplicates,    # single form JSON
    ...     clean_cross_form_duplicates,      # across multiple form JSONs
    ...     variable_richness_score,          # scoring helper
    ... )
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

from tqdm import tqdm

import config
from scripts.utils import logging_system as log

vlog = log.get_verbose_logger()

__all__ = [
    "clean_cross_form_duplicates",
    "clean_duplicate_columns",
    "remove_within_file_duplicates",
    "variable_richness_score",
]


# ============================================================================
# Dataset / Dictionary — duplicate COLUMN removal (JSONL / DataFrame)
# ============================================================================


def _dtypes_match(base_series: pd.Series, dup_series: pd.Series) -> bool:  # type: ignore[name-defined]
    """Return True iff both series share the exact same pandas dtype.

    Strict equality (``base.dtype == dup.dtype``) is intentional: ``int64``
    and ``Int64`` (nullable integer) are considered different types because
    they have different NA semantics.  This mirrors the "strict" dtype
    comparison used elsewhere in the extraction pipeline.
    """
    return base_series.dtype == dup_series.dtype


def _positionally_adjacent(columns: list[str], base_col: str, dup_col: str) -> bool:
    """Return True iff *dup_col* is immediately next to *base_col* in *columns*.

    "Adjacent" means the absolute difference of their indexes is exactly 1
    (either base→dup or dup→base direction).  This guards against treating a
    legitimately independent column as an Excel-autocomplete artifact just
    because an earlier column happens to share the same prefix.
    """
    try:
        base_idx = columns.index(base_col)
        dup_idx = columns.index(dup_col)
    except ValueError:
        return False
    return abs(dup_idx - base_idx) == 1


def clean_duplicate_columns(
    df: pd.DataFrame,
    *,
    source_file: str,
    sheet: str | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Remove duplicate columns ending with numeric suffixes from a DataFrame.

    Implements intelligent duplicate detection for Excel-autocomplete artifacts.
    A column is removed only when **all four** conditions hold:

    1. Its name matches the pattern ``base_name + optional '_' + digits``
       (e.g. ``SUBJID2``, ``NAME_3``).
    2. Its pandas dtype is identical to the base column's dtype (strict
       equality — ``int64`` and ``Int64`` are treated as distinct).
    3. It is positionally adjacent to the base column (consecutive in the
       source column order, i.e. ``abs(index(dup) - index(base)) == 1``).
    4. Its values are 100% identical to the base column (element-wise, with
       NaN-equality).

    Entirely-null columns matching rule 1 are still removed unconditionally
    (the null-path bypasses the dtype and adjacency checks because a null
    column carries no clinical information regardless of position).

    Args:
        df: pandas DataFrame to clean.
        source_file: Name of the source file (e.g. ``"01_Demographics.jsonl"``).
            Recorded verbatim on each drop event.
        sheet: Sheet name for multi-sheet inputs, or ``None`` for single-sheet
            / non-Excel sources.  Recorded verbatim on each drop event.

    Returns:
        Tuple of ``(cleaned_df, drop_events)`` where:

        - ``cleaned_df`` is a copy of *df* with duplicate columns removed.
        - ``drop_events`` is a list of dicts — one per removed column — with the
          keys ``scope`` (always ``"dataset-column"``), ``name`` (the dropped
          column), ``file`` (``source_file``), ``sheet`` (``sheet``),
          ``reason`` (``"100% identical to '<base>'"`` or ``"entirely null"``),
          and ``kept`` (the base column name, or ``None`` for pure-null drops).
    """
    pattern = config.DUPLICATE_COLUMN_PATTERN
    col_list: list[str] = list(df.columns)

    columns_to_keep: list[str] = []
    columns_to_remove: list[str] = []
    removal_reasons: dict[str, str] = {}
    drop_events: list[dict[str, Any]] = []

    for col in df.columns:
        match = re.match(pattern, str(col))
        if match:
            base_name = match.group(1)
            if base_name in df.columns:
                try:
                    if df[col].isna().all():
                        columns_to_remove.append(col)
                        reason = "entirely null"
                        removal_reasons[col] = reason
                        drop_events.append(
                            {
                                "scope": "dataset-column",
                                "name": col,
                                "file": source_file,
                                "sheet": sheet,
                                "reason": reason,
                                "kept": None,
                            }
                        )
                        log.debug("Marking '%s' for removal (entirely null)", col)
                        vlog.detail(f"Marking '{col}' for removal (entirely null)")
                    else:
                        base_col = df[base_name]
                        dup_col = df[col]
                        both_na = base_col.isna() & dup_col.isna()
                        both_equal = base_col == dup_col
                        all_match = (both_na | both_equal).all()

                        if all_match:
                            # Extra guards: dtype must match AND columns must be adjacent.
                            if not _dtypes_match(base_col, dup_col):
                                columns_to_keep.append(col)
                                log.debug(
                                    "Keeping '%s' (candidate kept: dtype mismatch with '%s': "
                                    "%s vs %s)",
                                    col,
                                    base_name,
                                    dup_col.dtype,
                                    base_col.dtype,
                                )
                                vlog.detail(
                                    f"Keeping '{col}' (candidate kept: dtype mismatch with "
                                    f"'{base_name}': {dup_col.dtype} vs {base_col.dtype})"
                                )
                            elif not _positionally_adjacent(col_list, base_name, col):
                                columns_to_keep.append(col)
                                log.debug(
                                    "Keeping '%s' (candidate kept: not positionally adjacent "
                                    "to '%s')",
                                    col,
                                    base_name,
                                )
                                vlog.detail(
                                    f"Keeping '{col}' (candidate kept: not positionally "
                                    f"adjacent to '{base_name}')"
                                )
                            else:
                                columns_to_remove.append(col)
                                reason = f"100% identical to '{base_name}'"
                                removal_reasons[col] = reason
                                drop_events.append(
                                    {
                                        "scope": "dataset-column",
                                        "name": col,
                                        "file": source_file,
                                        "sheet": sheet,
                                        "reason": reason,
                                        "kept": base_name,
                                    }
                                )
                                log.debug(
                                    "Marking '%s' for removal (100%% identical to '%s')",
                                    col,
                                    base_name,
                                )
                                vlog.detail(
                                    f"Marking '{col}' for removal (100% identical to '{base_name}')"
                                )
                        else:
                            columns_to_keep.append(col)
                            match_count = (both_na | both_equal).sum()
                            match_pct = (match_count / len(df) * 100) if len(df) > 0 else 0
                            log.debug(
                                "Keeping '%s' (%.1f%% similar to '%s', not 100%%)",
                                col,
                                match_pct,
                                base_name,
                            )
                            vlog.detail(
                                f"Keeping '{col}' ({match_pct:.1f}% similar to '{base_name}')"
                            )
                except Exception as e:
                    columns_to_keep.append(col)
                    log.warning(
                        "Could not compare '%s' with '%s': %s. Keeping column for safety.",
                        col,
                        base_name,
                        e,
                    )
                    vlog.detail(f"Keeping '{col}' (comparison failed: {e})")
            else:
                columns_to_keep.append(col)
                log.debug("Keeping '%s' (base column '%s' not found)", col, base_name)
        else:
            columns_to_keep.append(col)

    if columns_to_remove:
        removal_summary = [f"{col} ({removal_reasons[col]})" for col in columns_to_remove]
        tqdm.write(
            f"    → Removing {len(columns_to_remove)} duplicate column(s): "
            f"{', '.join(columns_to_remove)}"
        )
        log.info(
            "Removed %d duplicate columns: %s", len(columns_to_remove), ", ".join(removal_summary)
        )
        vlog.detail(f"Duplicate columns removed: {', '.join(removal_summary)}")
    else:
        log.debug("No duplicate columns found to remove")
        vlog.detail("No duplicate columns found")

    return df[columns_to_keep].copy(), drop_events


# ============================================================================
# PDF — variable richness scoring
# ============================================================================


def variable_richness_score(
    var_data: dict[str, Any],
) -> tuple[int, int, str]:
    """Score a variable definition by completeness for dedup tie-breaking.

    Returns a tuple ``(fields_populated, description_length, description)``
    that sorts higher for richer definitions.  Used to pick the canonical
    definition when the same abbreviation appears in multiple forms.
    """
    fields_populated = 0
    desc = var_data.get("description", "") or ""
    if desc:
        fields_populated += 1
    if var_data.get("values"):
        fields_populated += 1
    if var_data.get("depends_on"):
        fields_populated += 1
    if var_data.get("condition"):
        fields_populated += 1
    if var_data.get("section_context"):
        fields_populated += 1
    return (fields_populated, len(desc), desc)


# ============================================================================
# PDF — within-file duplicate variable removal (single form JSON)
# ============================================================================


def remove_within_file_duplicates(
    data: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Check a single parsed form JSON for duplicate variable abbreviations.

    LLM extractions can sometimes produce the same abbreviation twice within
    a single form (e.g. repeated header fields on multi-page PDFs, or the
    model listing a variable under two sections).  When found, the richest
    definition (most fields populated) is kept and extras are removed.

    This does **not** touch cross-form duplicates (SUBJID appearing in
    Form 1A and Form 1B) — that dedup belongs to the registry builder.

    .. warning::
        **Mutation contract.** When ``dry_run=False``, this function mutates
        ``data["variables"]`` in-place via the reference obtained at
        ``variables = data.get("variables", {})``.  The ``cleaned_data`` key
        in the return value is the *same object* as the input ``data`` — not a
        copy.  Callers that depend on ``result["cleaned_data"] is data``
        aliasing are correct; do **not** insert ``copy.deepcopy`` here.  A
        caller that passes ``data`` expecting no side-effect will see silent
        in-place modification.

    Args:
        data: The parsed ``*_variables.json`` dict (must contain a
            ``"variables"`` key).
        dry_run: If True, report only — don't modify the data.

    Returns:
        Dict with ``duplicates_removed`` (int), ``details`` (list), and
        optionally ``cleaned_data`` (the modified dict, only when not dry_run
        and changes were made).  ``cleaned_data`` is the same object as the
        input ``data`` (see mutation contract above).
    """
    variables = data.get("variables", {})
    if not variables:
        return {"duplicates_removed": 0, "details": []}

    # JSON dicts can't have duplicate keys at the Python level — json.load
    # silently keeps the last one.  But we can detect case-insensitive
    # collisions (e.g. "subjid" vs "SUBJID" in the same file).
    seen: dict[str, str] = {}  # casefold → first-seen canonical name
    case_dupes: list[tuple[str, str]] = []  # (kept, removed)

    for var_name in list(variables.keys()):
        folded = var_name.casefold()
        if folded in seen:
            canonical = seen[folded]
            # Compare richness — keep the one with more populated fields
            existing_score = variable_richness_score(variables[canonical])
            new_score = variable_richness_score(variables[var_name])
            if new_score > existing_score:
                # New one is richer — swap
                case_dupes.append((var_name, canonical))
                if not dry_run:
                    variables[canonical] = variables[var_name]
                    del variables[var_name]
                    # Update sections
                    for sec_data in data.get("sections", {}).values():
                        sec_vars = sec_data.get("variables", [])
                        if canonical in sec_vars and var_name in sec_vars:
                            sec_vars.remove(var_name)
                        elif var_name in sec_vars:
                            idx = sec_vars.index(var_name)
                            sec_vars[idx] = canonical
            else:
                case_dupes.append((canonical, var_name))
                if not dry_run:
                    del variables[var_name]
                    for sec_data in data.get("sections", {}).values():
                        sec_vars = sec_data.get("variables", [])
                        if var_name in sec_vars:
                            sec_vars.remove(var_name)
        else:
            seen[folded] = var_name

    result: dict[str, Any] = {
        "duplicates_removed": len(case_dupes),
        "details": [{"kept": kept, "removed": removed} for kept, removed in case_dupes],
    }
    if case_dupes and not dry_run:
        result["cleaned_data"] = data

    return result


# ============================================================================
# PDF — cross-form duplicate variable removal (across multiple form JSONs)
# ============================================================================


def clean_cross_form_duplicates(
    form_data: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Remove cross-form duplicate variables from a set of per-form JSON dicts.

    Scans all extracted variable JSONs, identifies variables appearing in more
    than one form, keeps the richest definition, and strips the duplicates
    from every other form.

    Args:
        form_data: Mapping of ``filename → parsed JSON dict`` for each form.
            Every dict must have a ``"variables"`` key.

    Returns:
        Dict mapping each **modified** filename to its cleaned data dict.
        Only files that were actually changed are included.
    """
    if not form_data:
        return {}

    # ── Phase 1: Build cross-form variable index ──
    var_index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for filename, data in form_data.items():
        for abbr, vdata in data.get("variables", {}).items():
            var_index.setdefault(abbr, []).append((filename, vdata))

    # ── Phase 2: Identify duplicates and pick canonical definitions ──
    cross_dupes: dict[str, list[tuple[str, dict[str, Any]]]] = {
        abbr: entries for abbr, entries in var_index.items() if len(entries) > 1
    }

    if not cross_dupes:
        log.debug("No cross-form duplicate variables found")
        vlog.detail("No cross-form duplicate variables found")
        return {}

    # For each duplicate variable, pick the richest definition as canonical
    canonical: dict[str, str] = {}  # abbr → filename that keeps it
    for abbr, entries in cross_dupes.items():
        scored = sorted(
            entries,
            key=lambda e: variable_richness_score(e[1]),
            reverse=True,
        )
        canonical[abbr] = scored[0][0]

    # ── Phase 3: Remove duplicates from non-canonical forms ──
    modified_files: dict[str, dict[str, Any]] = {}
    removal_log: list[str] = []
    total_removals = 0

    for abbr, entries in cross_dupes.items():
        winner = canonical[abbr]
        for filename, _vdata in entries:
            if filename == winner:
                continue
            data = form_data[filename]
            if abbr in data.get("variables", {}):
                del data["variables"][abbr]
                modified_files[filename] = data
                total_removals += 1
                removal_log.append(f"{abbr} removed from {filename} (canonical in {winner})")
                for sec_data in data.get("sections", {}).values():
                    sec_vars = sec_data.get("variables", [])
                    if abbr in sec_vars:
                        sec_vars.remove(abbr)

    # ── Phase 4: Log summary ──
    if total_removals:
        log.info(
            "Removed %d cross-form duplicate variables across %d forms: %s",
            total_removals,
            len(modified_files),
            ", ".join(sorted(cross_dupes.keys())),
        )
        for entry in removal_log:
            vlog.detail(f"  dedup: {entry}")
    else:
        log.debug("No cross-form duplicate variables removed")
        vlog.detail("No cross-form duplicate variables removed")

    return modified_files
