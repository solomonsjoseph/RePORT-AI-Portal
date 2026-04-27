"""Canonical extraction package for RePORT AI Portal.

This package exposes the single supported extraction entry points for the
active single-study, local-first pipeline:

- ``load_study_dictionary``: parse dictionary/mapping files into structured JSONL
- ``extract_datasets``: extract tabular study data directly into
  ``output/{STUDY}/trio_bundle/datasets/``
- ``process_datasets``: unified entry point — wraps ``extract_datasets``
- ``clean_trio_datasets``: post-promotion cleanup — remove junk, merge duplicates
- ``extract_pdfs_to_jsonl``: extract annotated study PDFs into structured JSON files

This package is the only supported extraction namespace. Legacy root-level
module paths and deprecated compatibility shims are not part of the active
architecture.

Example:
    >>> from scripts.extraction import load_study_dictionary
    >>> from scripts.extraction import extract_datasets
    >>> from scripts.extraction import extract_pdfs_to_jsonl
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dataset_cleanup import clean_trio_datasets
    from .dataset_pipeline import extract_datasets, process_datasets
    from .dedup import (
        clean_cross_form_duplicates as clean_duplicate_variables,
    )
    from .dedup import (
        clean_duplicate_columns,
        remove_within_file_duplicates,
    )
    from .extract_pdf_data import (
        clean_existing_jsons,
        extract_pdfs_to_jsonl,
        validate_depends_on,
    )
    from .load_dictionary import load_study_dictionary


def __getattr__(name: str):
    """Lazy import for extraction functions."""
    if name == "load_study_dictionary":
        from .load_dictionary import load_study_dictionary

        return load_study_dictionary
    elif name == "extract_datasets":
        from .dataset_pipeline import extract_datasets

        return extract_datasets
    elif name == "process_datasets":
        from .dataset_pipeline import process_datasets

        return process_datasets
    elif name == "extract_pdfs_to_jsonl":
        from .extract_pdf_data import extract_pdfs_to_jsonl

        return extract_pdfs_to_jsonl
    elif name == "clean_duplicate_variables":
        from .dedup import clean_cross_form_duplicates as clean_duplicate_variables

        return clean_duplicate_variables
    elif name == "clean_existing_jsons":
        from .extract_pdf_data import clean_existing_jsons

        return clean_existing_jsons
    elif name == "clean_trio_datasets":
        from .dataset_cleanup import clean_trio_datasets

        return clean_trio_datasets
    elif name == "validate_depends_on":
        from .extract_pdf_data import validate_depends_on

        return validate_depends_on
    elif name == "clean_duplicate_columns":
        from .dedup import clean_duplicate_columns

        return clean_duplicate_columns
    elif name == "remove_within_file_duplicates":
        from .dedup import remove_within_file_duplicates

        return remove_within_file_duplicates
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "clean_duplicate_columns",
    "clean_duplicate_variables",
    "clean_existing_jsons",
    "clean_trio_datasets",
    "extract_datasets",
    "extract_pdfs_to_jsonl",
    "load_study_dictionary",
    "process_datasets",
    "remove_within_file_duplicates",
    "validate_depends_on",
]
