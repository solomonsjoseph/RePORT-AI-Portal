"""raw-data-intake (skill 0): sort an unorganized study delivery into the
canonical data/raw/<study>/ layout. Classification is filename + extension
ONLY — no workbook is ever opened (GR-1). Standalone prep, not a DAG phase.
"""

from __future__ import annotations

ANNOTATED_PDFS = "annotated_pdfs"
DATA_DICTIONARY = "data_dictionary"
DATASETS = "datasets"
UNCLASSIFIED = "_unclassified"

#: substrings (case-insensitive) that mark a spreadsheet as the data dictionary.
DICTIONARY_NAME_HINTS = ("mapping", "dictionary", "deb", "codebook")

_DATASET_EXTS = (".xlsx", ".csv")


def classify(filename: str) -> str:
    """Return the target bucket for *filename* by name + extension only."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return ANNOTATED_PDFS
    if lower.endswith(_DATASET_EXTS):
        if any(hint in lower for hint in DICTIONARY_NAME_HINTS):
            return DATA_DICTIONARY
        return DATASETS
    return UNCLASSIFIED
