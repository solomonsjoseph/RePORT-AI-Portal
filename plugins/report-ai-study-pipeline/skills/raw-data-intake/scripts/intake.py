"""raw-data-intake (skill 0): sort an unorganized study delivery into the
canonical data/raw/<study>/ layout. Classification is filename + extension
ONLY — no workbook is ever opened (GR-1). Standalone prep, not a DAG phase.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
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


def _safe_dest(workdir: Path, name: str) -> tuple[Path, bool]:
    """Return a collision-free destination path and whether a collision occurred."""
    dest = workdir / name
    if not dest.exists():
        return dest, False
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 1
    while True:
        candidate = workdir / f"{stem}.collid{n}{suffix}"
        if not candidate.exists():
            return candidate, True
        n += 1


# Subdirectories that are pipeline-managed / VCS / junk — never part of a
# delivery. The destination raw tree is excluded separately via exclude_under so
# pointing SRC at data/ (which contains data/raw/) never re-ingests the study.
_IGNORED_DIR_NAMES = frozenset(
    {"snapshots", "output", "tmp", ".git", "__pycache__", "node_modules"}
)
_IGNORED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _is_ignored_source(path: Path, src_root: Path, exclude_under: list[Path]) -> bool:
    """True if *path* is junk, hidden, in a managed subdir, or under a dest tree."""
    if path.name in _IGNORED_FILE_NAMES or path.name.startswith("."):
        return True
    rel_parents = path.relative_to(src_root).parts[:-1]
    if any(part in _IGNORED_DIR_NAMES or part.startswith(".") for part in rel_parents):
        return True
    resolved = path.resolve()
    for ex in exclude_under:
        try:
            resolved.relative_to(Path(ex).resolve())
            return True  # lives under a destination/managed root
        except ValueError:
            continue
    return False


def stage_source(
    src: Path,
    workdir: Path,
    collisions: list | None = None,
    *,
    exclude_under: list[Path] | None = None,
) -> list[Path]:
    """Copy SRC (file or dir) into WORKDIR and extract any .zip. Non-destructive.

    Returns the flat list of staged regular files (zips extracted, not returned).

    When SRC is a directory it is walked recursively, but pipeline-managed
    subdirs (``_IGNORED_DIR_NAMES``), hidden/junk files, and anything under a
    path in *exclude_under* (e.g. the destination ``data/raw`` tree) are skipped
    — so pointing SRC at ``data/`` files only the loose new files and never
    re-ingests the study's own organized tree or snapshots.

    When two source files share a basename the later file is written under a
    disambiguated name (``stem.collidN.ext``) and its original basename is
    appended to *collisions* (if provided), so no data is silently lost.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"intake source not found: {src}")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    exclude_under = exclude_under or []

    if src.is_file():
        sources = [src]
    else:
        sources = sorted(
            p
            for p in src.rglob("*")
            if p.is_file() and not _is_ignored_source(p, src, exclude_under)
        )
    for item in sources:
        dest, collided = _safe_dest(workdir, item.name)
        shutil.copy2(item, dest)
        if collided and collisions is not None:
            collisions.append(item.name)

    # Extract any staged zips (one level; extracted zips themselves are dropped).
    for zpath in sorted(workdir.glob("*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                orig_name = Path(member).name  # flatten; name + ext is all we need
                dest, collided = _safe_dest(workdir, orig_name)
                with zf.open(member) as fh, open(dest, "wb") as out:
                    shutil.copyfileobj(fh, out)
                if collided and collisions is not None:
                    collisions.append(orig_name)
        zpath.unlink()

    return sorted(p for p in workdir.glob("*") if p.is_file() and p.suffix.lower() != ".zip")


_REAL_BUCKETS = (ANNOTATED_PDFS, DATA_DICTIONARY, DATASETS)
_ALL_BUCKETS = (*_REAL_BUCKETS, UNCLASSIFIED)


@dataclass
class IntakeResult:
    counts: dict = field(default_factory=dict)
    unclassified: list = field(default_factory=list)
    manifest_written: bool = False
    skipped: bool = False
    review_note: str | None = None
    already_present: list = field(default_factory=list)


def is_already_organized(raw_study_dir: Path) -> bool:
    """True iff the bucket dirs exist and datasets/ already holds files."""
    raw_study_dir = Path(raw_study_dir)
    if not all((raw_study_dir / b).is_dir() for b in _REAL_BUCKETS):
        return False
    return any((raw_study_dir / DATASETS).iterdir())


def draft_manifest(dataset_names: list, manifest_path: Path) -> bool:
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        return False  # never clobber a hand-tuned manifest
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# DRAFT — operator must confirm before make study.", "required:"]
    lines += [f"  - {name}" for name in sorted(dataset_names)]
    lines += ["optional: []", "reject: []", ""]
    manifest_path.write_text("\n".join(lines))
    return True


def write_review_note(audit_dir: Path, unclassified: list[tuple[str, str]]) -> str | None:
    """unclassified: list[(filename, reason_code)]. Count-only; no contents."""
    if not unclassified:
        return None

    from scripts.audit.review_paths import intake_review_path

    note_path = intake_review_path(Path(audit_dir))
    note_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# raw-data-intake review — items needing attention",
        "",
        f"count: {len(unclassified)}",
        "",
        "| file | reason |",
        "| --- | --- |",
    ]
    lines += [f"| {name} | {reason} |" for name, reason in unclassified]
    lines.append("")
    note_path.write_text("\n".join(lines))
    return str(note_path)


def organize(
    study: str,
    src: Path,
    *,
    force: bool = False,
    add: bool = False,
    raw_root: Path | None = None,
    config_root: Path | None = None,
    audit_dir: Path | None = None,
) -> IntakeResult:
    import config

    raw_root = Path(raw_root) if raw_root is not None else Path(config.RAW_DATA_DIR)
    config_root = Path(config_root) if config_root is not None else Path(config.CONFIG_DIR)
    audit_dir = Path(audit_dir) if audit_dir is not None else Path(config.STUDY_AUDIT_DIR)

    raw_study_dir = raw_root / study
    # ponytail: force bypasses the no-op guard and re-sorts additively (same-named files overwritten); a destructive clean is out of scope — dedup is skill 2
    # add mode files NEW files into an already-organized study without the force
    # rebuild semantics: it never overwrites an existing file (records it as
    # already_present instead) and never re-ingests the study's own buckets
    # (SRC is the inbox). Manifest-gap surfacing is deferred (future work).
    if not force and not add and is_already_organized(raw_study_dir):
        return IntakeResult(skipped=True)

    # Pre-create the canonical buckets so the layout is complete even when a
    # bucket gets no files (e.g. a delivery with no data dictionary). This also
    # makes a re-run correctly no-op via is_already_organized.
    for bucket in _ALL_BUCKETS:
        (raw_study_dir / bucket).mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        collisions: list[str] = []
        # Exclude the destination raw tree so SRC=data (which contains data/raw)
        # never re-ingests the study's own organized files.
        staged = stage_source(Path(src), Path(tmp), collisions=collisions, exclude_under=[raw_root])
        counts = dict.fromkeys(_ALL_BUCKETS, 0)
        unclassified: list = []
        already_present: list = []
        for path in staged:
            bucket = classify(path.name)
            dest_dir = raw_study_dir / bucket
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if add and dest.exists():
                already_present.append(path.name)  # never overwrite in add mode
                continue
            shutil.copy2(path, dest)
            counts[bucket] += 1
            if bucket == UNCLASSIFIED:
                unclassified.append((path.name, "unrecognized_name_or_extension"))
        # Record collisions so a human can verify no data was lost.
        collision_entries = [(name, "name_collision") for name in collisions]

    dataset_names = [p.name for p in (raw_study_dir / DATASETS).glob("*") if p.is_file()]
    manifest_path = config_root / study / "_forms_manifest.yaml"
    manifest_written = draft_manifest(dataset_names, manifest_path)
    review_note = write_review_note(audit_dir, unclassified + collision_entries)

    return IntakeResult(
        counts=counts,
        unclassified=[name for name, _ in unclassified],
        manifest_written=manifest_written,
        skipped=False,
        review_note=review_note,
        already_present=already_present,
    )
