"""Shared I/O primitives for the RePORT AI Portal extraction and processing pipeline.

This sub-package provides canonical, crash-safe file-writing utilities and
file-discovery helpers used across extraction, intake, variable-building,
and vector-DB modules.  By centralizing these primitives every module in the
pipeline uses the same battle-tested atomic-write strategy (write to a
``NamedTemporaryFile`` sibling → ``Path.replace()`` on success) and the
same junk-file filtering logic.

Modules in this package:

``clinical_dates``
    Date-field detection and parsing helpers (``parse_date``,
    ``is_dmy_variable``, ``value_looks_like_date``, ``ParsedDate``).

``file_io``
    Atomic file-write helpers for JSONL, JSON, and
    ``pandas.DataFrame``-to-JSONL conversion.

``file_discovery``
    File-discovery helpers that skip hidden files, OS junk, and Excel
    lock files, then return a sorted, deterministic list of paths.

``jsonl_reader``
    JSONL line-parsing helper shared across the pipeline.

Example usage::

    from scripts.extraction.io import (
        atomic_write_jsonl,
        atomic_write_json,
        atomic_write_dataframe_jsonl,
        discover_files,
    )
"""

from __future__ import annotations

from scripts.extraction.io.clinical_dates import (
    ParsedDate,
    is_dmy_variable,
    parse_date,
    value_looks_like_date,
)
from scripts.extraction.io.file_discovery import (
    DEFAULT_JUNK_FILENAMES,
    SUPPORTED_TABULAR_EXTENSIONS,
    discover_files,
)
from scripts.extraction.io.file_io import (
    ATOMIC_WRITE_SUFFIX,
    FILE_ENCODING,
    JSONL_EXT,
    atomic_write_dataframe_jsonl,
    atomic_write_json,
    atomic_write_jsonl,
)
from scripts.extraction.io.jsonl_reader import (
    JSONLParseError,
    load_json_object_line,
)

__all__ = [
    "ATOMIC_WRITE_SUFFIX",
    "DEFAULT_JUNK_FILENAMES",
    "FILE_ENCODING",
    "JSONL_EXT",
    "SUPPORTED_TABULAR_EXTENSIONS",
    "JSONLParseError",
    "ParsedDate",
    "atomic_write_dataframe_jsonl",
    "atomic_write_json",
    "atomic_write_jsonl",
    "discover_files",
    "is_dmy_variable",
    "load_json_object_line",
    "parse_date",
    "value_looks_like_date",
]
