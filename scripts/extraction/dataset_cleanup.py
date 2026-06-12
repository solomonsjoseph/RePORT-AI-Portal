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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import config
from scripts.audit.ledger import (
    CLEANUP_LEDGER_FILENAME,
    LedgerWriter,
    dataset_cleanup_ledger_path,
    ensure_no_llm_sentinel,
    remove_dataset_no_llm_sentinels,
)
from scripts.extraction.io import (
    atomic_write_json,
)
from scripts.security.secure_env import assert_output_zone, assert_write_zone
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)

__all__ = ["UnscrubbedDatasetError", "clean_trio_datasets"]


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


class UnscrubbedDatasetError(Exception):
    """Raised when clean_trio_datasets is asked to process a staging dataset that
    has not been PHI-scrubbed. Fail-closed: the cleanup step reads row values
    during duplicate detection and must never touch unredacted PHI. Run
    scripts.security.phi_scrub.run_scrub (Step 1.6) before clean_trio_datasets
    (Step 1.7)."""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _assert_scrubbed(df: pd.DataFrame, file: Path) -> None:
    """Raise UnscrubbedDatasetError if any row lacks the PHI-scrub marker
    `_phi_scrubbed == "v3"`. A missing/old marker means phi_scrub (Step 1.6)
    did not run on this file."""
    marker_field = "_phi_scrubbed"  # mirrors phi_scrub.py _SCRUB_MARKER_FIELD
    marker_value = "v3"  # mirrors phi_scrub.py _SCRUB_VERSION
    if df.empty:
        return  # genuinely empty file has no rows that could carry PHI
    if marker_field not in df.columns:
        raise UnscrubbedDatasetError(
            f"{file.name}: '_phi_scrubbed' marker absent — run Step 1.6 "
            f"(phi_scrub) before Step 1.7 (dataset_cleanup)."
        )
    bad = df[marker_field] != marker_value
    n_bad = int(bad.sum())
    if n_bad:
        raise UnscrubbedDatasetError(
            f"{file.name}: {n_bad} row(s) not scrubbed to v3 — run Step 1.6 "
            f"(phi_scrub) before Step 1.7 (dataset_cleanup)."
        )


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


# ── Human-review note writer ────────────────────────────────────────────────


def _write_jsonl_union_review_note(
    datasets_dir: Path,
    stem_a: str,
    stem_b: str,
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
) -> None:
    """Write a COUNT-ONLY human-review note for a value-divergent duplicate pair.

    The note goes to ``output/{STUDY}/audit/human_review/{stem_a}/jsonl_union_review.md``
    — inside the audit (no-LLM) zone, using config.STUDY_AUDIT_DIR so the path
    is always correct regardless of staging layout.  NEVER writes row values.
    """
    note_dir = Path(config.STUDY_AUDIT_DIR) / "human_review" / stem_a
    note_dir.mkdir(parents=True, exist_ok=True)
    assert_output_zone(note_dir)

    cols_a = sorted(df_a.columns.tolist())
    note = (
        f"# Human Review Required: Value-Divergent JSONL Pair\n\n"
        f"## Files\n"
        f"- `{stem_a}.jsonl` — {len(df_a):,} rows\n"
        f"- `{stem_b}.jsonl` — {len(df_b):,} rows\n\n"
        f"## Schema (identical)\n"
        f"{cols_a}\n\n"
        f"## What was tried\n"
        f"Schema match was confirmed (identical column sets).\n"
        f"Subset check was performed: neither file's rows are a strict subset "
        f"of the other.\n"
        f"Row counts differ (`{stem_a}`: {len(df_a):,}, `{stem_b}`: {len(df_b):,}).\n"
        f"Automated union-concat was NOT performed — this would risk silently "
        f"discarding real clinical rows or creating duplicate subject records.\n\n"
        f"## What is ambiguous\n"
        f"Some rows are present in one file but absent from the other.  These "
        f"may be: (a) legitimately separate subject records, (b) corrected "
        f"re-entries, or (c) a true superset/subset relationship hidden by "
        f"minor value differences.  Only a domain expert can determine the "
        f"authoritative source.\n\n"
        f"## What would resolve it\n"
        f"1. Open both staging JSONL files and compare by subject ID.\n"
        f"2. Identify which file is the authoritative source (or confirm they "
        f"   should both be kept as separate datasets).\n"
        f"3. Once resolved, update `SUSPECTED_DUPLICATE_PAIRS` in "
        f"   `scripts/extraction/dataset_cleanup.py` to remove this pair "
        f"   (if they are not duplicates) or manually consolidate them before "
        f"   re-running the pipeline.\n\n"
        f"*Note: this file contains column NAMES and row COUNTS only — "
        f"no row values are recorded here.*\n"
    )
    (note_dir / "jsonl_union_review.md").write_text(note, encoding="utf-8")
    logger.info(
        "Human-review note written for value-divergent pair (%s, %s): %s",
        stem_a,
        stem_b,
        note_dir / "jsonl_union_review.md",
    )


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
        # Same schema but row counts differ and neither is a subset of the other.
        # This means the files contain value-divergent rows that cannot be
        # safely merged automatically — a union concat could silently discard
        # real clinical data or produce duplicate subject records.  Route to
        # human review instead of auto-merging, leaving BOTH files in staging.
        # Mirror the fail-closed spirit of merge_excel_duplicates.MergeNotSafeError.
        _write_jsonl_union_review_note(
            datasets_dir=datasets_dir,
            stem_a=stem_a,
            stem_b=stem_b,
            df_a=df_a,
            df_b=df_b,
        )
        report.duplicates_skipped.append(
            {
                "pair": f"{stem_a} / {stem_b}",
                "reason": "value_divergent_needs_human_review",
                "rows_a": str(len(df_a)),
                "rows_b": str(len(df_b)),
                "schema": str(sorted(df_a.columns.tolist())),
                "what_was_tried": (
                    "schema match confirmed; subset check passed (neither is a subset of the other); "
                    "row counts differ — automated union not safe"
                ),
                "what_was_ambiguous": (
                    "rows present in one file but absent from the other "
                    "(value-divergent, not provably duplicate)"
                ),
                "what_would_resolve_it": (
                    "human review of the two files to determine the correct superset "
                    "or authoritative source; update SUSPECTED_DUPLICATE_PAIRS with "
                    "the confirmed action"
                ),
            }
        )
        logger.warning(
            "Duplicate pair (%s, %s): same schema but value-divergent rows "
            "(rows_a=%d, rows_b=%d) — BOTH files left in staging; "
            "human review note written to audit zone",
            stem_a,
            stem_b,
            len(df_a),
            len(df_b),
        )


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


def _emit_as_written_ledger(
    *,
    extracted_drop_events: list[dict[str, Any]],
    report: CleanupReport,
    audit_path: Path,
    study_name: str | None,
    dataset_files: list[str],
) -> None:
    """Write one cleanup as-written ledger under each dataset audit folder.

    Dual-write only — does not modify the legacy audit report.
    """
    audit_dir = audit_path.parent
    ensure_no_llm_sentinel(audit_dir)
    remove_dataset_no_llm_sentinels(audit_dir)
    (audit_dir / CLEANUP_LEDGER_FILENAME).unlink(missing_ok=True)

    display_names = {Path(name).stem: name for name in dataset_files}
    grouped_events: dict[str, list[dict[str, Any]]] = {}

    def _append_event(dataset_file: str, event: dict[str, Any]) -> None:
        stem = Path(dataset_file).stem
        display_names.setdefault(stem, dataset_file)
        grouped_events.setdefault(stem, []).append(event)

    # Column-drop events (scope == "dataset-column" only)
    for event in extracted_drop_events:
        if event.get("scope") != "dataset-column":
            continue
        _append_event(
            event["file"],
            {
                "variable_id": event["name"],
                "action": "dataset_column_drop",
                "rationale": event.get("reason", ""),
                "dataset_file": event["file"],
            },
        )

    # Junk file removals
    for filename in report.junk_removed:
        stem = Path(filename).stem
        _append_event(
            filename,
            {
                "variable_id": stem,
                "action": "dataset_junk_file",
                "rationale": "known junk artifact",
                "dataset_file": filename,
            },
        )

    # Duplicate-pair merges
    for dup in report.duplicates_merged:
        removed_file = dup.get("removed", "")
        stem = Path(removed_file).stem
        _append_event(
            removed_file,
            {
                "variable_id": stem,
                "action": "dataset_duplicate_file",
                "rationale": dup.get("reason", ""),
                "dataset_file": removed_file,
            },
        )

    for stem in sorted(display_names):
        writer = LedgerWriter(
            output_path=dataset_cleanup_ledger_path(audit_dir, display_names[stem]),
            study=study_name,
            leg="dataset",
            sentinel_dir=audit_dir,
        )
        for event in grouped_events.get(stem, []):
            writer.add_cleanup_event(
                form=stem,
                variable_id=event["variable_id"],
                action=event["action"],
                rule_project_category="cleanup",
                rationale=event["rationale"],
                dataset_file=event["dataset_file"],
                count=None,
            )
        writer.flush()


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

    # ── Fail-closed scrub-first guard ───────────────────────────────────────
    # Cleanup reads row VALUES during duplicate detection, so the entire staging
    # tree MUST already be PHI-scrubbed. Refuse to proceed otherwise.
    if datasets_dir is not None and datasets_dir.is_dir():
        for _jsonl in sorted(datasets_dir.glob("*.jsonl")):
            _assert_scrubbed(_read_jsonl_df(_jsonl), _jsonl)
    # ────────────────────────────────────────────────────────────────────────

    report = CleanupReport()
    dataset_files: list[str] = []

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
        dataset_files = sorted(f.name for f in datasets_dir.glob("*.jsonl"))
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

    # Phase 4: Dual-write new as_written ledger (additive, does not modify legacy audit)
    _emit_as_written_ledger(
        extracted_drop_events=extracted_drop_events,
        report=report,
        audit_path=audit_path,
        study_name=study_name,
        dataset_files=dataset_files,
    )

    return report
