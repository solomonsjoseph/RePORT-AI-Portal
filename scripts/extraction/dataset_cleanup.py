"""Dataset cleanup for the staging datasets directory.

Runs on the staging tree (``config.STAGING_DATASETS_DIR`` by default) **after**
raw-data extraction and **before** promotion to the trio bundle. Only clean,
unique datasets survive into the trio bundle.

Responsibilities:
    1. Remove known junk files (test/error artifacts).
    2. Detect structurally-duplicate dataset pairs via schema + row-count
       comparison.
    3. Merge confirmed duplicates — keep the file with more records (or
       union if complementary). Remove the duplicate.
    4. Serialize a unified audit report to ``config.AUDIT_DATASET_REPORT_PATH``
       that combines upstream extraction column-drop events with the
       junk/duplicate-file events produced here. Audit lives under
       ``output/{STUDY}/audit/`` and survives the run — it is authoritative.

All removals are logged. No raw-data access occurs — this module only
touches the staging tree (``tmp/{STUDY}/``) for its working files and the
output zone (``output/{STUDY}/audit/``) for its audit envelope.

Usage:
    >>> from scripts.extraction.dataset_cleanup import clean_trio_datasets
    >>> report = clean_trio_datasets(
    ...     datasets_dir,
    ...     extracted_drop_events=[...],
    ...     study_name="Indo-VAP",
    ... )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import config
from scripts.extraction.io import (
    atomic_write_dataframe_jsonl,
    atomic_write_json,
)
from scripts.security.secure_env import assert_output_zone, assert_write_zone

logger = logging.getLogger(__name__)

__all__ = ["clean_trio_datasets"]


# ── Configuration ───────────────────────────────────────────────────────────
# To add a new junk file pattern: add its stem to JUNK_PATTERNS.
# To register a new suspected duplicate pair: append a (stem_a, stem_b) tuple
# to SUSPECTED_DUPLICATE_PAIRS. Both constants are the canonical edit points —
# no other file needs to change.

# Files that are known test/error artifacts — always removed.
JUNK_PATTERNS: frozenset[str] = frozenset(
    {
        "Paste Errors",
        "TEST1EK",
    }
)

# Suspected duplicate pairs: (fileA_stem, fileB_stem).
# Each pair will be structurally compared; if schemas match, the smaller
# file is removed and the larger (or union) is kept.
SUSPECTED_DUPLICATE_PAIRS: list[tuple[str, str]] = [
    ("14_CaseControl", "14_Case_Control"),
    ("2A_ICBaseline", "2A_ICBaseline_1"),
    ("101_HHC_Recontact", "101_HHC_Recontact_1"),
    ("21_DSTISO", "21_DSTIsolate"),
]


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class CleanupReport:
    """Summary of dataset cleanup actions."""

    junk_removed: list[str] = field(default_factory=list)
    duplicates_merged: list[dict[str, str]] = field(default_factory=list)
    duplicates_skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_actions(self) -> int:
        return len(self.junk_removed) + len(self.duplicates_merged)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _read_jsonl_df(path: Path) -> pd.DataFrame:
    """Read a JSONL file into a DataFrame."""
    return pd.read_json(path, lines=True)


def _schemas_match(df_a: pd.DataFrame, df_b: pd.DataFrame) -> bool:
    """Check if two DataFrames have identical column sets (order-independent)."""
    return set(df_a.columns) == set(df_b.columns)


def _is_subset(df_small: pd.DataFrame, df_large: pd.DataFrame) -> bool:
    """Check if the smaller DataFrame's rows are a subset of the larger one.

    Uses column intersection and checks if all rows in df_small exist in
    df_large (after dedup).
    """
    shared_cols = sorted(set(df_small.columns) & set(df_large.columns))
    if not shared_cols:
        return False
    try:
        merged = df_small[shared_cols].merge(
            df_large[shared_cols],
            how="left",
            indicator=True,
        )
        return bool((merged["_merge"] == "both").all())
    except (KeyError, ValueError, TypeError, MemoryError) as exc:
        logger.debug(
            "_is_subset merge failed for shared_cols=%s: %s — treating as non-subset",
            shared_cols,
            exc,
        )
        return False


# ── Core ────────────────────────────────────────────────────────────────────


def _remove_junk(datasets_dir: Path, report: CleanupReport) -> None:
    """Remove known junk/test files from the datasets directory."""
    for jsonl_file in sorted(datasets_dir.glob("*.jsonl")):
        if jsonl_file.stem in JUNK_PATTERNS:
            try:
                jsonl_file.unlink()
                report.junk_removed.append(jsonl_file.name)
                logger.info("Removed junk file: %s", jsonl_file.name)
            except OSError as exc:
                msg = f"Failed to remove junk file {jsonl_file.name}: {exc}"
                report.errors.append(msg)
                logger.warning(msg)


def _merge_duplicate_pair(
    datasets_dir: Path,
    stem_a: str,
    stem_b: str,
    report: CleanupReport,
) -> None:
    """Compare two suspected duplicates and merge if structurally equivalent."""
    file_a = datasets_dir / f"{stem_a}.jsonl"
    file_b = datasets_dir / f"{stem_b}.jsonl"

    # Both must exist
    if not file_a.is_file() or not file_b.is_file():
        logger.debug(
            "Duplicate pair (%s, %s): one or both files missing — skipped",
            stem_a,
            stem_b,
        )
        return

    try:
        df_a = _read_jsonl_df(file_a)
        df_b = _read_jsonl_df(file_b)
    except Exception as exc:
        msg = f"Failed to read duplicate pair ({stem_a}, {stem_b}): {exc}"
        report.errors.append(msg)
        logger.warning(msg)
        return

    # Schemas must match (or at least overlap substantially)
    if not _schemas_match(df_a, df_b):
        report.duplicates_skipped.append(
            {
                "pair": f"{stem_a} / {stem_b}",
                "reason": "schemas differ",
                "cols_a": str(sorted(df_a.columns.tolist())),
                "cols_b": str(sorted(df_b.columns.tolist())),
            }
        )
        logger.info(
            "Duplicate pair (%s, %s): schemas differ — kept both",
            stem_a,
            stem_b,
        )
        return

    # Determine which to keep: the one with more records
    keep_stem, keep_df, drop_stem, drop_file, drop_df = (
        (stem_a, df_a, stem_b, file_b, df_b)
        if len(df_a) >= len(df_b)
        else (stem_b, df_b, stem_a, file_a, df_a)
    )

    # Check if the smaller is a subset of the larger
    is_sub = _is_subset(drop_df, keep_df)

    if is_sub or len(df_a) == len(df_b):
        # Subset or identical row count with same schema → drop the smaller
        try:
            drop_file.unlink()
            report.duplicates_merged.append(
                {
                    "kept": f"{keep_stem}.jsonl",
                    "removed": f"{drop_stem}.jsonl",
                    "kept_rows": str(len(keep_df)),
                    "removed_rows": str(len(drop_df)),
                    "reason": "subset" if is_sub else "same_schema_same_count",
                }
            )
            logger.info(
                "Merged duplicate: kept %s (%d rows), removed %s (%d rows)",
                keep_stem,
                len(keep_df),
                drop_stem,
                len(drop_df),
            )
        except OSError as exc:
            msg = f"Failed to remove duplicate {drop_stem}: {exc}"
            report.errors.append(msg)
            logger.warning(msg)
    else:
        # Same schema but not a subset — union them into the keep file
        try:
            combined = pd.concat([keep_df, drop_df], ignore_index=True).drop_duplicates()
            atomic_write_dataframe_jsonl(
                datasets_dir / f"{keep_stem}.jsonl",
                combined,
                prefix=config.TEMP_PREFIX_DATASET,
            )
            drop_file.unlink()
            report.duplicates_merged.append(
                {
                    "kept": f"{keep_stem}.jsonl",
                    "removed": f"{drop_stem}.jsonl",
                    "kept_rows": str(len(combined)),
                    "original_rows_a": str(len(df_a)),
                    "original_rows_b": str(len(df_b)),
                    "reason": "union_merge",
                }
            )
            logger.info(
                "Union-merged duplicates: %s + %s → %s (%d rows)",
                stem_a,
                stem_b,
                keep_stem,
                len(combined),
            )
        except Exception as exc:
            msg = f"Failed to union-merge ({stem_a}, {stem_b}): {exc}"
            report.errors.append(msg)
            logger.warning(msg)


def _serialize_audit(
    report: CleanupReport,
    extraction_drops: list[dict[str, Any]],
    study_name: str,
    out_path: Path,
) -> None:
    """Write the unified audit report for this cleanup leg.

    Flat schema (per cleanup-propagation plan):
        {
          "study": str,
          "generated_utc": "YYYY-MM-DDTHH:MM:SSZ",
          "leg": "dataset",
          "removed": [
            {scope, name, file, sheet, reason, kept}, ...
          ]
        }

    ``extraction_drops`` (upstream column-level drops from the extraction
    leg) pass through verbatim. Junk-file and duplicate-file removals are
    appended with ``scope=dataset-junk-file`` and
    ``scope=dataset-duplicate-file`` respectively.
    """
    removed: list[dict[str, Any]] = list(extraction_drops)  # pass-through first

    # Junk files → dataset-junk-file events
    for filename in report.junk_removed:
        stem = Path(filename).stem
        removed.append(
            {
                "scope": "dataset-junk-file",
                "name": stem,
                "file": filename,
                "sheet": None,
                "reason": "known junk artifact",
                "kept": None,
            }
        )

    # Duplicate-pair merges → dataset-duplicate-file events
    for dup in report.duplicates_merged:
        removed_name = dup.get("removed", "")
        kept_name = dup.get("kept", "")
        removed.append(
            {
                "scope": "dataset-duplicate-file",
                "name": Path(removed_name).stem if removed_name else "",
                "file": removed_name,
                "sheet": None,
                "reason": dup.get("reason", ""),
                "kept": kept_name or None,
            }
        )

    payload: dict[str, Any] = {
        "study": study_name,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "leg": "dataset",
        "removed": removed,
        "skipped": report.duplicates_skipped,
        "errors": report.errors,
    }

    assert_output_zone(out_path.parent)
    atomic_write_json(out_path, payload)


def clean_trio_datasets(
    datasets_dir: Path | None = None,
    *,
    extracted_drop_events: list[dict[str, Any]] | None = None,
    study_name: str | None = None,
    audit_path: Path | None = None,
) -> CleanupReport:
    """Clean the staging datasets directory and emit a unified audit report.

    Removes junk files and merges confirmed structural duplicates from the
    staging tree, then writes ``{study, generated_utc, leg, removed[]}``
    atomically to the audit path — combining upstream extraction column
    drops with this leg's file-level removals.

    Args:
        datasets_dir: Path to the datasets directory. Defaults to
            ``config.STAGING_DATASETS_DIR`` (junk/duplicate scans operate on
            the staging tree, not the promoted trio bundle).
        extracted_drop_events: Upstream column-drop events from the
            extraction leg, each shaped like the unified-audit schema row
            (``{scope, name, file, sheet, reason, kept}``). Passed through
            verbatim into the audit. Defaults to ``[]``.
        study_name: Study identifier for the audit envelope. Defaults to
            ``config.STUDY_NAME``.
        audit_path: Destination for the unified audit JSON. Defaults to
            ``config.AUDIT_DATASET_REPORT_PATH``.

    Returns:
        CleanupReport with details of junk/duplicate actions taken here.
        The audit file is always written — even when ``datasets_dir`` is
        missing or empty — to guarantee a stable envelope downstream.
    """
    if datasets_dir is None:
        datasets_dir = config.STAGING_DATASETS_DIR
    if extracted_drop_events is None:
        extracted_drop_events = []
    if study_name is None:
        study_name = config.STUDY_NAME
    if audit_path is None:
        audit_path = config.AUDIT_DATASET_REPORT_PATH

    assert_write_zone(datasets_dir)

    report = CleanupReport()

    if datasets_dir.is_dir():
        existing = sorted(f.stem for f in datasets_dir.glob("*.jsonl"))
        logger.info(
            "Dataset cleanup: %d JSONL files in %s",
            len(existing),
            datasets_dir,
        )

        # Phase 1: Remove junk
        _remove_junk(datasets_dir, report)

        # Phase 2: Merge duplicates
        for stem_a, stem_b in SUSPECTED_DUPLICATE_PAIRS:
            _merge_duplicate_pair(datasets_dir, stem_a, stem_b, report)

        # Summary
        remaining = sorted(f.stem for f in datasets_dir.glob("*.jsonl"))
        logger.info(
            "Dataset cleanup complete: %d files remaining (removed %d junk, merged %d duplicates, %d skipped, %d errors)",
            len(remaining),
            len(report.junk_removed),
            len(report.duplicates_merged),
            len(report.duplicates_skipped),
            len(report.errors),
        )
    else:
        logger.info(
            "Datasets directory does not exist — skipping scan, emitting empty audit: %s",
            datasets_dir,
        )

    # Phase 3: Always emit unified audit (even on empty/missing input)
    _serialize_audit(report, extracted_drop_events, study_name, audit_path)

    return report
