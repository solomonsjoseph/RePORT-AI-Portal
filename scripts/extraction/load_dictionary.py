"""Extract study data dictionaries into structured JSONL mappings.

Reads dictionary files from ``data/raw/{STUDY_NAME}/data_dictionary/``
and writes structured JSONL under
``output/{STUDY_NAME}/trio_bundle/dictionary/``.

Supports ``.xlsx``, ``.xls``, and ``.csv`` inputs. Detects multiple
logical tables inside Excel sheets, enriches records with provenance
metadata, and exports deterministic JSONL files.

Three-stage pipeline: Discovery → Parsing → Export.

Tables after the "ignore below" marker are saved to an ``extras/``
subdirectory (still as ``.jsonl``).
"""

from __future__ import annotations

__all__ = [
    "discover_dictionary_files",
    "load_study_dictionary",
    "process_csv_file",
    "process_excel_file",
]

import sys
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from tqdm import tqdm

import config
from scripts.extraction.io import (
    atomic_write_dataframe_jsonl,
    discover_files,
)
from scripts.extraction.io.file_discovery import SUPPORTED_TABULAR_EXTENSIONS
from scripts.security.secure_env import assert_not_raw
from scripts.utils import logging_system as log

# Constants for table processing
IGNORE_BELOW_MARKER = "ignore below"
EXTRA_TABLES_DIR = "extras"
UNNAMED_COLUMN_PREFIX = "Unnamed"
METADATA_SHEET_KEY = "__sheet__"
METADATA_TABLE_KEY = "__table__"
METADATA_SOURCE_FILE_KEY = "__source_file__"

NAMED_TEMP_PREFIX = config.TEMP_PREFIX_DICT

# Supported dictionary file extensions (deterministic ordering)
SUPPORTED_EXTENSIONS: tuple[str, ...] = SUPPORTED_TABULAR_EXTENSIONS


def _deduplicate_columns(columns: Any) -> list[str]:
    """Make column names unique by appending numeric suffixes to duplicates.

    This function handles duplicate column names (common in Excel with merged cells
    or unnamed columns) by appending _1, _2, etc. to subsequent occurrences. It also
    converts NaN/null values to "Unnamed" prefix.

    Args:
        columns: Iterable of column names (can include None/NaN values).

    Returns:
        List of unique column names with numeric suffixes for duplicates.

    Example:
        >>> cols = ['Name', 'Name', 'Age', None, 'Name']
        >>> _deduplicate_columns(cols)
        ['Name', 'Name_1', 'Age', 'Unnamed', 'Name_2']

    Notes:
        - First occurrence keeps original name
        - Subsequent duplicates get _1, _2, ... suffixes
        - None/NaN values become "Unnamed" (or "Unnamed_1", etc.)
    """
    new_cols: list[str] = []
    counts: dict[str, int] = {}
    for col in columns:
        col_str = str(col) if pd.notna(col) else UNNAMED_COLUMN_PREFIX
        if col_str in counts:
            counts[col_str] += 1
            new_cols.append(f"{col_str}_{counts[col_str]}")
        else:
            new_cols.append(col_str)
            counts[col_str] = 0
    return new_cols


def _split_sheet_into_tables(df: pd.DataFrame) -> list[pd.DataFrame] | None:
    """Split DataFrame into multiple tables based on empty row/column boundaries.

    This implements a two-stage boundary detection algorithm:
    1. Find horizontal strips: contiguous row groups separated by fully empty rows
    2. Within each strip, find vertical segments separated by fully empty columns

    Each segment represents a separate table that can be independently processed.
    This handles Excel sheets with multiple tables laid out side-by-side or stacked.

    Args:
        df: Input DataFrame from Excel sheet (may contain multiple tables).

    Returns:
        List of DataFrames, each representing a detected table.
        Empty list ``[]`` if the sheet is genuinely empty.
        ``None`` if a parse error occurred (distinct from an empty sheet).
    """
    try:
        if df.empty:
            log.debug("Received empty DataFrame, returning empty table list")
            return []

        log.debug(f"Analyzing DataFrame with shape {df.shape} for table boundaries")

        empty_rows = df.index[df.isnull().all(axis=1)].tolist()
        row_boundaries: list[int] = [-1, *empty_rows, df.shape[0]]
        horizontal_strips = [
            df.iloc[row_boundaries[i] + 1 : row_boundaries[i + 1]]
            for i in range(len(row_boundaries) - 1)
            if row_boundaries[i] + 1 < row_boundaries[i + 1]
        ]

        log.debug(f"Found {len(horizontal_strips)} horizontal strip(s)")

        all_tables: list[pd.DataFrame] = []
        for strip in horizontal_strips:
            empty_col_indices = [
                i for i, col in enumerate(strip.columns) if strip[col].isnull().all()
            ]
            col_boundaries = [-1, *empty_col_indices, len(strip.columns)]
            for j in range(len(col_boundaries) - 1):
                start_col, end_col = col_boundaries[j] + 1, col_boundaries[j + 1]
                if start_col < end_col:
                    table_df = strip.iloc[:, start_col:end_col].copy()
                    table_df.dropna(how="all", inplace=True)
                    if not table_df.empty:
                        all_tables.append(table_df)

        log.debug(f"Detected {len(all_tables)} table(s) from DataFrame")
        return all_tables

    except (KeyError, IndexError) as e:
        log.error(f"DataFrame structure error during table splitting: {e}")
        log.debug("DataFrame info:", exc_info=True)
        return None  # signals parse error, distinct from empty sheet
    except Exception as e:
        log.error(f"Unexpected error splitting DataFrame into tables: {type(e).__name__}: {e}")
        log.debug("Full error details:", exc_info=True)
        return None  # signals parse error


def _process_and_save_tables(
    all_tables: list[pd.DataFrame],
    sheet_name: str,
    output_dir: Path | str,
    source_file: str = "",
) -> bool:
    """Process detected tables, add metadata, and save as JSONL.

    Tables before the "ignore below" marker go to the sheet directory.
    Tables after the marker go to ``extras/`` — all as ``.jsonl``.
    """
    output_dir = Path(output_dir)
    folder_name = "".join(c for c in sheet_name if c.isalnum() or c in "._- ").strip()
    sheet_dir = output_dir / folder_name

    sheet_dir.mkdir(parents=True, exist_ok=True)

    log.debug(f"Processing {len(all_tables)} tables from sheet '{sheet_name}'")
    ignore_mode = False
    all_ok = True

    for i, table_df in enumerate(all_tables):
        table_df.reset_index(drop=True, inplace=True)

        if len(table_df) == 0:
            log.warning(f"Table {i + 1} from sheet '{sheet_name}' is empty after reset. Skipping.")
            continue

        if not ignore_mode:
            for idx, col in enumerate(table_df.iloc[0]):
                if IGNORE_BELOW_MARKER in str(col).lower().strip():
                    log.info(
                        f"'{IGNORE_BELOW_MARKER}' found in table {i + 1}. "
                        f"Subsequent → '{EXTRA_TABLES_DIR}'."
                    )
                    ignore_mode = True
                    table_df = table_df.drop(table_df.columns[idx], axis=1)
                    break

        table_df.dropna(how="all", axis=1, inplace=True)
        table_df.dropna(how="all", inplace=True)
        if table_df.empty:
            log.debug(f"Table {i + 1} from sheet '{sheet_name}' is empty after cleanup. Skipping.")
            continue

        # Promote first row to column headers
        try:
            table_df.columns = _deduplicate_columns(table_df.iloc[0])
            table_df = table_df.iloc[1:].reset_index(drop=True)
        except IndexError as e:
            log.error(f"Cannot process table {i + 1} from sheet '{sheet_name}': {e}")
            all_ok = False
            continue

        if table_df.empty:
            log.debug(
                f"Table {i + 1} from sheet '{sheet_name}' is empty after header promotion. "
                "Skipping."
            )
            continue

        # Determine output path
        table_suffix = f"_table_{i + 1}" if len(all_tables) > 1 else "_table"

        if ignore_mode:
            extras_dir = sheet_dir / EXTRA_TABLES_DIR
            extras_dir.mkdir(parents=True, exist_ok=True)
            table_name = f"{EXTRA_TABLES_DIR}{table_suffix}"
            metadata_name = f"{folder_name}_{EXTRA_TABLES_DIR}{table_suffix}"
            output_path = extras_dir / f"{table_name}.jsonl"
        else:
            table_name = metadata_name = f"{folder_name}{table_suffix}"
            output_path = sheet_dir / f"{table_name}.jsonl"

        # Single-user CLI: TOCTOU between check and write is accepted.
        # atomic_write_dataframe_jsonl guarantees no partial files on concurrent race.
        if output_path.exists() and output_path.stat().st_size > 0:
            log.warning("File exists. Skipping: %s", output_path)
            continue

        try:
            table_df[METADATA_SHEET_KEY] = sheet_name
            table_df[METADATA_TABLE_KEY] = metadata_name
            if source_file:
                table_df[METADATA_SOURCE_FILE_KEY] = source_file
            atomic_write_dataframe_jsonl(output_path, table_df, prefix=NAMED_TEMP_PREFIX)
            log.info(f"Saved {len(table_df)} rows → '{output_path}'")
        except OSError as e:
            log.error(f"Failed to write table to '{output_path}': {e}")
            all_ok = False
            continue
        except Exception as e:
            log.error(f"Error saving table '{table_name}': {type(e).__name__}: {e}")
            all_ok = False
            continue

    return all_ok


def process_excel_file(excel_path: Path | str, output_dir: Path | str, preserve_na: bool = True) -> bool:
    """Extract all tables from an Excel file and save as JSONL files."""
    excel_path = Path(excel_path)
    output_dir = Path(output_dir)
    log.info(f"Processing: '{excel_path}'")
    log.info(f"Output → '{output_dir}'")

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        _ext = excel_path.suffix.lower()
        _engine: Literal["openpyxl", "xlrd"] = "openpyxl" if _ext == ".xlsx" else "xlrd"
        with pd.ExcelFile(excel_path, engine=_engine) as xls:
            log.debug(f"Excel file loaded. Found {len(xls.sheet_names)} sheets: {xls.sheet_names}")
            success = True

            for sheet_name in tqdm(
                xls.sheet_names,
                desc="Processing sheets",
                unit="sheet",
                file=sys.stdout,
                dynamic_ncols=True,
                leave=True,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
            ):
                try:
                    log.debug("--- Sheet: '%s' ---", sheet_name)
                    if preserve_na:
                        sheet_df = xls.parse(
                            sheet_name=sheet_name,
                            header=None,
                            keep_default_na=False,
                            na_values=[""],
                        )
                    else:
                        sheet_df = xls.parse(sheet_name=sheet_name, header=None)
                    all_tables = _split_sheet_into_tables(sheet_df)

                    if all_tables is None:
                        log.warning("Table-split error in sheet '%s' — skipping", sheet_name)
                        success = False
                    elif not all_tables:
                        log.info("No tables found in '%s'", sheet_name)
                    else:
                        log.info("Found %d table(s) in '%s'", len(all_tables), sheet_name)
                        result = _process_and_save_tables(
                            all_tables,
                            str(sheet_name),
                            output_dir,
                            source_file=excel_path.name,
                        )
                        if not result:
                            success = False
                except Exception as e:
                    log.error("Error on sheet '%s': %s", sheet_name, e, exc_info=True)
                    success = False
    except Exception as e:
        log.error(f"Error reading Excel file '{excel_path}': {e}")
        log.debug("Full error details:", exc_info=True)
        return False

    if success:
        log.success("Excel processing complete!")
    else:
        log.warning("Excel processing completed with some errors")

    return success


def discover_dictionary_files(dictionary_dir: Path | str) -> list[str]:
    """Discover all supported dictionary files in the given directory.

    Delegates to :func:`scripts.extraction.io.discover_files` and converts
    the returned ``Path`` objects to strings for backward compatibility.
    """
    dictionary_dir = Path(dictionary_dir)
    files = discover_files(
        dictionary_dir,
        extensions=SUPPORTED_EXTENSIONS,
        label="Data dictionary",
        not_found_label="dictionary",
    )
    found = [str(f) for f in files]

    log.info(f"Discovered {len(found)} dictionary file(s) in '{dictionary_dir}':")
    for f in found:
        log.info(f"  • {Path(f).name}")
    return found


def process_csv_file(csv_path: Path | str, output_dir: Path | str, preserve_na: bool = True) -> bool:
    """Parse a CSV dictionary file and save as JSONL with provenance metadata."""
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    stem = csv_path.stem
    log.info(f"Processing CSV: '{csv_path}'")
    log.info(f"Output → '{output_dir}'")

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if preserve_na:
            df = pd.read_csv(csv_path, keep_default_na=False, na_values=[""])
        else:
            df = pd.read_csv(csv_path)
    except Exception as e:
        log.error(f"Error reading CSV file '{csv_path}': {e}")
        log.debug("Full error details:", exc_info=True)
        return False

    if df.empty:
        log.warning(f"CSV file '{csv_path}' is empty (no data rows).")
        return True

    df.columns = _deduplicate_columns(df.columns)

    df.dropna(how="all", inplace=True)
    if df.empty:
        log.warning(f"CSV file '{csv_path}' contained only empty rows after cleanup.")
        return True

    sheet_label = stem
    table_label = f"{stem}_table"
    df[METADATA_SHEET_KEY] = sheet_label
    df[METADATA_TABLE_KEY] = table_label
    df[METADATA_SOURCE_FILE_KEY] = csv_path.name

    folder_name = "".join(c for c in stem if c.isalnum() or c in "._- ").strip()
    sheet_dir = output_dir / folder_name
    sheet_dir.mkdir(parents=True, exist_ok=True)

    output_path = sheet_dir / f"{table_label}.jsonl"
    # Single-user CLI: TOCTOU between check and write is accepted.
    # atomic_write_dataframe_jsonl guarantees no partial files on concurrent race.
    if output_path.exists() and output_path.stat().st_size > 0:
        log.warning("File exists. Skipping: %s", output_path)
        return True

    try:
        atomic_write_dataframe_jsonl(output_path, df, prefix=NAMED_TEMP_PREFIX)
        log.info(f"Saved {len(df)} rows → '{output_path}'")
    except Exception as e:
        log.error(f"Failed to write CSV output to '{output_path}': {e}")
        return False

    log.success(f"CSV processing complete for '{csv_path.name}'")
    return True


def load_study_dictionary(
    dictionary_dir: Path | str | None = None,
    json_output_dir: Path | str | None = None,
    preserve_na: bool = True,
) -> bool:
    """Load and process all study data dictionary files to JSONL format.

    When ``json_output_dir`` is not supplied the dictionary JSONL files are
    written to ``config.STAGING_DICTIONARY_DIR`` (``tmp/{STUDY}/dictionary/``);
    a subsequent publish step promotes them into ``trio_bundle/dictionary/``.
    """
    if json_output_dir:
        assert_not_raw(str(json_output_dir))
    if dictionary_dir is None:
        dictionary_dir = config.DATA_DICTIONARY_DIR

    output_dir: Path = Path(json_output_dir) if json_output_dir else config.STAGING_DICTIONARY_DIR

    log.info(f"Dictionary source: '{dictionary_dir}'")
    log.info(f"Output target:     '{output_dir}' (staging)")

    files = discover_dictionary_files(dictionary_dir)

    all_ok = True
    for fpath in files:
        ext = Path(fpath).suffix.lower()
        if ext in (".xlsx", ".xls"):
            ok = process_excel_file(
                excel_path=fpath, output_dir=output_dir, preserve_na=preserve_na
            )
        elif ext == ".csv":
            ok = process_csv_file(csv_path=fpath, output_dir=output_dir, preserve_na=preserve_na)
        else:
            log.warning(f"Unsupported dictionary format (skipped): {fpath}")
            continue
        if not ok:
            all_ok = False

    if all_ok:
        log.success("All dictionary files processed successfully.")
    else:
        log.warning("Dictionary processing completed with some errors.")
    return all_ok


if __name__ == "__main__":
    log.setup_logging(
        module_name="scripts.extraction.load_dictionary",
        log_level="INFO",
    )

    try:
        success = load_study_dictionary(preserve_na=True)
    except (FileNotFoundError, ValueError) as e:
        log.error(str(e))
        sys.exit(1)

    if success:
        log.success(f"Processing complete for data dictionaries from {config.DATA_DICTIONARY_DIR}")
    else:
        log.error(f"Processing failed for data dictionaries from {config.DATA_DICTIONARY_DIR}")
        sys.exit(1)
