"""raw-data-intake (skill 0): sort an unorganized study delivery into the
canonical data/raw/<study>/ layout. Classification is filename + extension
ONLY — no workbook is ever opened (GR-1). Standalone prep, not a DAG phase.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

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


def stage_source(src: Path, workdir: Path) -> list[Path]:
    """Copy SRC (file or dir) into WORKDIR and extract any .zip. Non-destructive.

    Returns the flat list of staged regular files (zips extracted, not returned).
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"intake source not found: {src}")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    sources = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for item in sources:
        dest = workdir / item.name
        shutil.copy2(item, dest)

    # Extract any staged zips (one level; extracted zips themselves are dropped).
    for zpath in sorted(workdir.glob("*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                target = workdir / Path(member).name  # flatten; name + ext is all we need
                with zf.open(member) as fh, open(target, "wb") as out:
                    shutil.copyfileobj(fh, out)
        zpath.unlink()

    return sorted(p for p in workdir.glob("*") if p.is_file() and p.suffix.lower() != ".zip")
