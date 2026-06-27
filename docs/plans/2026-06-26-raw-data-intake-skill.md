# Skill 0 — `raw-data-intake` Implementation Plan

> Historical implementation plan is no longer current documentation. Durable architecture lives in `docs/sphinx`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone prep skill that sorts an unorganized study delivery (flat dump and/or zips) into the canonical `data/raw/{STUDY}/` four-bucket layout plus a draft `_forms_manifest.yaml`, idempotently, classifying on filename + extension only.

**Architecture:** A pure classifier (`classify`) + a non-destructive stager (`stage_source`) + an idempotent orchestrator (`organize`) live in `scripts/intake.py`. A thin `run.py` wraps `organize` in the `RPLN_SKILL_RESULT:` subprocess contract, exactly like sibling `study-setup`. The skill is NOT an orchestrator phase and never touches the per-study publish lock. Invoked via `make organize STUDY=<name> SRC=<dir-or-zip>`.

**Tech Stack:** Python 3.11+, stdlib only (`pathlib`, `shutil`, `zipfile`, `tempfile`), `pyyaml` (already a dep), pytest. Run everything via `uv run --all-groups`.

## Global Constraints

- **Package manager:** `uv` required; always `uv run --all-groups python …`. Never bare `python`/`pip`. The repo `.venv` is Python 3.9 — the floor is 3.11+.
- **GR-1 PHI rule:** never read dataset row values. Classification is on filename + extension only; no workbook is ever opened. Any value-free report carries name + bucket-guess + reason code only.
- **Write zones:** the skill writes only under `data/raw/`, `config/`, and `output/*/audit/`. **Never** writes `llm_source/`.
- **One-way dependency rule:** `plugins/ → scripts/` is allowed; `scripts/ → plugins/` is forbidden. The skill's `scripts/intake.py` may `import config` and `from scripts.audit.review_paths import …`, never `import plugins`.
- **Subprocess contract:** `run.py` emits exactly one `RPLN_SKILL_RESULT:` line via `scripts.utils.skill_protocol.emit_skill_result`; `summary`/`data` carry counts/names/codes only.
- **Idempotent + fail-closed:** an already-organized tree is left untouched (no-op) unless `FORCE=1`; an unclassifiable file quarantines to `_unclassified/` rather than landing silently in `datasets/`.
- **Commit messages:** no "Generated with Claude Code" / `Co-Authored-By: Claude` trailers.
- **Markdown:** committed `.md` outside the allowlist needs `git add -f`; this plan + the SKILL.md live under exempt trees (`docs/plans/`, `skills/`).

---

## File Structure

| Path | Responsibility |
|---|---|
| `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py` | Pure classifier + stager + orchestrator (`classify`, `stage_source`, `is_already_organized`, `draft_manifest`, `write_review_note`, `organize`, `IntakeResult`). |
| `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py` | Subprocess entry: parse args, call `organize`, emit `RPLN_SKILL_RESULT:`. |
| `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/__init__.py` | Empty package marker (mirrors siblings). |
| `plugins/report-ai-study-pipeline/skills/raw-data-intake/SKILL.md` | Platform-neutral spec + GR-1 banner + CLI. |
| `plugins/report-ai-study-pipeline/skills/raw-data-intake/agents/llm.yaml` | Adapter metadata (display name, prompt). |
| `scripts/audit/review_paths.py` | Add `intake_review_path` helper (single source of truth for the note path). |
| `Makefile` | Add the `organize` target. |
| `plugins/report-ai-study-pipeline/plugin.yaml` | Register the skill under `skills:` (role: setup, non-DAG). |
| `tests/test_raw_data_intake.py` | Unit + integration tests. |

Tasks are ordered so each builds on a tested predecessor: classifier → stager → orchestrator → wiring.

---

## Task 1: Pure filename classifier

**Files:**
- Create: `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py`
- Create: `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/__init__.py` (empty)
- Test: `tests/test_raw_data_intake.py`

**Interfaces:**
- Produces: `classify(filename: str) -> str` returning exactly one of the bucket constants `ANNOTATED_PDFS = "annotated_pdfs"`, `DATA_DICTIONARY = "data_dictionary"`, `DATASETS = "datasets"`, `UNCLASSIFIED = "_unclassified"`. Also produces the module-level tuple `DICTIONARY_NAME_HINTS = ("mapping", "dictionary", "deb", "codebook")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_raw_data_intake.py
import importlib.util
from pathlib import Path

_INTAKE = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py"
)
_spec = importlib.util.spec_from_file_location("intake", _INTAKE)
intake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(intake)


def test_classify_pdf_to_annotated():
    assert intake.classify("12A_FUA_annotated.PDF") == "annotated_pdfs"


def test_classify_dictionary_by_name_hint():
    assert intake.classify("Indo_VAP_DEB_mapping.xlsx") == "data_dictionary"
    assert intake.classify("study_codebook.csv") == "data_dictionary"
    assert intake.classify("DEB.xlsx") == "data_dictionary"


def test_classify_plain_dataset():
    assert intake.classify("14_Case_Control.xlsx") == "datasets"
    assert intake.classify("labs.csv") == "datasets"


def test_classify_unknown_extension_quarantines():
    assert intake.classify("readme.txt") == "_unclassified"
    assert intake.classify("notes.docx") == "_unclassified"
    assert intake.classify("nodotextension") == "_unclassified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -v`
Expected: FAIL (`ModuleNotFoundError` / `exec_module` fails — `intake.py` does not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py
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
```

Also create the empty package marker:

```python
# plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py \
        plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/__init__.py \
        tests/test_raw_data_intake.py
git commit -m "feat(intake): filename+extension classifier for skill 0"
```

---

## Task 2: Non-destructive stager (copy + zip extraction)

**Files:**
- Modify: `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py`
- Test: `tests/test_raw_data_intake.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `stage_source(src: Path, workdir: Path) -> list[Path]`. Copies a file `src`, or every file under a directory `src`, into `workdir`; extracts any `*.zip` (top-level and nested-once) into `workdir` and does not return the `.zip` itself. Returns the flat list of staged regular-file paths (no directories, no `.zip`). Source is never modified or moved. Raises `FileNotFoundError` if `src` does not exist.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raw_data_intake.py
import zipfile


def _touch(path: Path, content: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_stage_flat_dir_copies_all(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    _touch(src / "b.pdf")
    work = tmp_path / "work"
    staged = intake.stage_source(src, work)
    names = sorted(p.name for p in staged)
    assert names == ["a.xlsx", "b.pdf"]
    # source untouched
    assert (src / "a.xlsx").exists()


def test_stage_extracts_zip(tmp_path):
    src = tmp_path / "delivery"
    src.mkdir()
    payload = tmp_path / "payload.xlsx"
    _touch(payload, "data")
    zpath = src / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(payload, arcname="inside.xlsx")
    work = tmp_path / "work"
    staged = intake.stage_source(src, work)
    names = sorted(p.name for p in staged)
    assert names == ["inside.xlsx"]  # the .zip itself is not returned


def test_stage_single_file(tmp_path):
    f = tmp_path / "lone.csv"
    _touch(f)
    work = tmp_path / "work"
    staged = intake.stage_source(f, work)
    assert [p.name for p in staged] == ["lone.csv"]


def test_stage_missing_src_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        intake.stage_source(tmp_path / "nope", tmp_path / "work")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k stage -v`
Expected: FAIL with `AttributeError: module 'intake' has no attribute 'stage_source'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add imports at top of intake.py
import shutil
import zipfile
from pathlib import Path
```

```python
# append to intake.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k stage -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py tests/test_raw_data_intake.py
git commit -m "feat(intake): non-destructive stager with zip extraction"
```

---

## Task 3: Review-note path helper

**Files:**
- Modify: `scripts/audit/review_paths.py`
- Test: `tests/test_raw_data_intake.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `intake_review_path(audit_dir: Path) -> Path` returning `audit_dir / "human_review" / "intake" / "intake_review.md"` (via the existing `form_review_dir(audit_dir, "intake")`). Add `"intake_review_path"` to `__all__`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raw_data_intake.py
from pathlib import Path as _P
from scripts.audit.review_paths import intake_review_path


def test_intake_review_path():
    p = intake_review_path(_P("/out/STUDY/audit"))
    assert p == _P("/out/STUDY/audit/human_review/intake/intake_review.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k review_path -v`
Expected: FAIL with `ImportError: cannot import name 'intake_review_path'`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/audit/review_paths.py`, add `"intake_review_path",` to the `__all__` list (keep it alphabetically near the other helpers), and append this function after `form_review_dir`:

```python
def intake_review_path(audit_dir: Path) -> Path:
    """Count-only note for skill-0 quarantined files (Note 22, key='intake')."""
    return form_review_dir(audit_dir, "intake") / "intake_review.md"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k review_path -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit/review_paths.py tests/test_raw_data_intake.py
git commit -m "feat(intake): canonical intake review-note path (Note 22)"
```

---

## Task 4: Idempotent orchestrator (`organize`) + manifest draft + review note

**Files:**
- Modify: `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py`
- Test: `tests/test_raw_data_intake.py`

**Interfaces:**
- Consumes: `classify` (Task 1), `stage_source` (Task 2), `intake_review_path` (Task 3).
- Produces:
  - `IntakeResult` dataclass: `counts: dict[str, int]` (bucket → placed count), `unclassified: list[str]` (filenames), `manifest_written: bool`, `skipped: bool` (True when already-organized no-op), `review_note: str | None` (path or None).
  - `is_already_organized(raw_study_dir: Path) -> bool` — True iff the three real bucket dirs exist and `datasets/` is non-empty.
  - `draft_manifest(dataset_names: list[str], manifest_path: Path) -> bool` — writes a DRAFT `_forms_manifest.yaml` (required = sorted dataset names, empty optional/reject) only if absent; returns whether it wrote.
  - `write_review_note(audit_dir: Path, unclassified: list[tuple[str, str]]) -> str | None` — writes the count-only note (name + bucket-guess + reason code only) when the list is non-empty; returns the path or None.
  - `organize(study: str, src: Path, *, force: bool = False, raw_root: Path | None = None, config_root: Path | None = None, audit_dir: Path | None = None) -> IntakeResult` — the full flow. `raw_root`/`config_root`/`audit_dir` default to `config.RAW_DATA_DIR` / `config.CONFIG_DIR` / `config.STUDY_AUDIT_DIR` and are injectable for tests.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raw_data_intake.py
def test_organize_sorts_and_drafts_manifest(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "14_Case_Control.xlsx")
    _touch(src / "labs.csv")
    _touch(src / "DEB_mapping.xlsx")
    _touch(src / "form12.pdf")
    _touch(src / "readme.txt")
    raw_root = tmp_path / "raw"
    cfg_root = tmp_path / "config"
    audit = tmp_path / "audit"

    res = intake.organize(
        "STUDY", src, raw_root=raw_root, config_root=cfg_root, audit_dir=audit
    )

    base = raw_root / "STUDY"
    assert sorted(p.name for p in (base / "datasets").iterdir()) == ["14_Case_Control.xlsx", "labs.csv"]
    assert [p.name for p in (base / "data_dictionary").iterdir()] == ["DEB_mapping.xlsx"]
    assert [p.name for p in (base / "annotated_pdfs").iterdir()] == ["form12.pdf"]
    assert [p.name for p in (base / "_unclassified").iterdir()] == ["readme.txt"]
    assert res.counts["datasets"] == 2
    assert res.unclassified == ["readme.txt"]
    # manifest drafted with both datasets as required
    manifest = (cfg_root / "STUDY" / "_forms_manifest.yaml").read_text()
    assert "DRAFT" in manifest
    assert "14_Case_Control.xlsx" in manifest and "labs.csv" in manifest
    assert res.manifest_written is True
    # review note written for the quarantined file
    assert res.review_note is not None
    note = (audit / "human_review" / "intake" / "intake_review.md").read_text()
    assert "readme.txt" in note
    assert res.skipped is False


def test_organize_noop_on_already_organized(tmp_path):
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    for b in ("annotated_pdfs", "data_dictionary", "datasets", "_unclassified"):
        (base / b).mkdir(parents=True)
    _touch(base / "datasets" / "existing.xlsx")  # non-empty datasets => organized
    src = tmp_path / "delivery"
    _touch(src / "new.xlsx")

    res = intake.organize(
        "STUDY", src, raw_root=raw_root, config_root=tmp_path / "config",
        audit_dir=tmp_path / "audit",
    )
    assert res.skipped is True
    # untouched: the new file was NOT copied in
    assert [p.name for p in (base / "datasets").iterdir()] == ["existing.xlsx"]


def test_organize_force_rebuilds(tmp_path):
    raw_root = tmp_path / "raw"
    base = raw_root / "STUDY"
    (base / "datasets").mkdir(parents=True)
    _touch(base / "datasets" / "existing.xlsx")
    src = tmp_path / "delivery"
    _touch(src / "new.xlsx")

    res = intake.organize(
        "STUDY", src, force=True, raw_root=raw_root,
        config_root=tmp_path / "config", audit_dir=tmp_path / "audit",
    )
    assert res.skipped is False
    assert "new.xlsx" in [p.name for p in (base / "datasets").iterdir()]


def test_organize_preserves_existing_manifest(tmp_path):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    cfg_root = tmp_path / "config"
    mpath = cfg_root / "STUDY" / "_forms_manifest.yaml"
    mpath.parent.mkdir(parents=True)
    mpath.write_text("# hand-tuned\nrequired:\n  - a.xlsx\n")
    res = intake.organize(
        "STUDY", src, raw_root=tmp_path / "raw", config_root=cfg_root,
        audit_dir=tmp_path / "audit",
    )
    assert res.manifest_written is False
    assert "hand-tuned" in mpath.read_text()  # never clobbered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k organize -v`
Expected: FAIL with `AttributeError: module 'intake' has no attribute 'organize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to imports at top of intake.py
import tempfile
from dataclasses import dataclass, field
```

```python
# append to intake.py
_REAL_BUCKETS = (ANNOTATED_PDFS, DATA_DICTIONARY, DATASETS)
_ALL_BUCKETS = (*_REAL_BUCKETS, UNCLASSIFIED)


@dataclass
class IntakeResult:
    counts: dict = field(default_factory=dict)
    unclassified: list = field(default_factory=list)
    manifest_written: bool = False
    skipped: bool = False
    review_note: str | None = None


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


def write_review_note(audit_dir: Path, unclassified: list) -> str | None:
    """unclassified: list[(filename, reason_code)]. Count-only; no contents."""
    if not unclassified:
        return None
    from scripts.audit.review_paths import intake_review_path

    note_path = intake_review_path(Path(audit_dir))
    note_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# raw-data-intake review — unclassified files",
        "",
        f"count: {len(unclassified)}",
        "",
        "| file | bucket_guess | reason |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {name} | {UNCLASSIFIED} | {reason} |" for name, reason in unclassified]
    lines.append("")
    note_path.write_text("\n".join(lines))
    return str(note_path)


def organize(
    study: str,
    src: Path,
    *,
    force: bool = False,
    raw_root: Path | None = None,
    config_root: Path | None = None,
    audit_dir: Path | None = None,
) -> IntakeResult:
    import config

    raw_root = Path(raw_root) if raw_root is not None else Path(config.RAW_DATA_DIR)
    config_root = Path(config_root) if config_root is not None else Path(config.CONFIG_DIR)
    audit_dir = Path(audit_dir) if audit_dir is not None else Path(config.STUDY_AUDIT_DIR)

    raw_study_dir = raw_root / study
    if not force and is_already_organized(raw_study_dir):
        return IntakeResult(skipped=True)

    with tempfile.TemporaryDirectory() as tmp:
        staged = stage_source(Path(src), Path(tmp))
        counts = {b: 0 for b in _ALL_BUCKETS}
        unclassified: list = []
        for path in staged:
            bucket = classify(path.name)
            dest_dir = raw_study_dir / bucket
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest_dir / path.name)
            counts[bucket] += 1
            if bucket == UNCLASSIFIED:
                unclassified.append((path.name, "unrecognized_name_or_extension"))

    dataset_names = [p.name for p in (raw_study_dir / DATASETS).glob("*") if p.is_file()]
    manifest_path = config_root / study / "_forms_manifest.yaml"
    manifest_written = draft_manifest(dataset_names, manifest_path)
    review_note = write_review_note(audit_dir, unclassified)

    return IntakeResult(
        counts=counts,
        unclassified=[name for name, _ in unclassified],
        manifest_written=manifest_written,
        skipped=False,
        review_note=review_note,
    )
```

Note: confirm `config.CONFIG_DIR` exists. If the constant is named differently, check with `grep -n "^CONFIG_DIR\|CONFIG_DIR =" config.py` and use the actual name; the per-study config dir is the parent that `study_config_path` joins `study` onto.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -v`
Expected: PASS (all tests across Tasks 1–4).

- [ ] **Step 5: Commit**

```bash
git add plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/intake.py tests/test_raw_data_intake.py
git commit -m "feat(intake): idempotent organize() + draft manifest + review note"
```

---

## Task 5: Subprocess entry, Makefile target, SKILL.md, llm.yaml, plugin.yaml

**Files:**
- Create: `plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py`
- Create: `plugins/report-ai-study-pipeline/skills/raw-data-intake/SKILL.md`
- Create: `plugins/report-ai-study-pipeline/skills/raw-data-intake/agents/llm.yaml`
- Modify: `Makefile`
- Modify: `plugins/report-ai-study-pipeline/plugin.yaml`
- Test: `tests/test_raw_data_intake.py`

**Interfaces:**
- Consumes: `organize`, `IntakeResult` (Task 4); `scripts.utils.skill_protocol.{SkillResult, add_common_skill_args, emit_skill_result}`.
- Produces: `run.py::main(argv) -> int` emitting one `RPLN_SKILL_RESULT:` line; exit 0 on success (including a no-op skip), non-zero only on a real error (e.g. missing SRC).

- [ ] **Step 1: Write the failing test (run.py end-to-end via subprocess)**

```python
# append to tests/test_raw_data_intake.py
import json
import subprocess
import sys

_RUN_PY = (
    Path(__file__).resolve().parents[1]
    / "plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py"
)


def test_run_py_emits_marker(tmp_path, monkeypatch):
    src = tmp_path / "delivery"
    _touch(src / "a.xlsx")
    _touch(src / "junk.txt")
    raw_root = tmp_path / "raw"
    monkeypatch.setenv("RPLN_INTAKE_RAW_ROOT", str(raw_root))
    monkeypatch.setenv("RPLN_INTAKE_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("RPLN_INTAKE_AUDIT_DIR", str(tmp_path / "audit"))
    proc = subprocess.run(
        [sys.executable, str(_RUN_PY), "--study", "STUDY", "--src", str(src)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("RPLN_SKILL_RESULT:")][-1]
    payload = json.loads(marker[len("RPLN_SKILL_RESULT:"):])
    assert payload["skill"] == "raw-data-intake"
    assert payload["ok"] is True
    assert payload["data"]["counts"]["datasets"] == 1
    assert payload["data"]["unclassified"] == ["junk.txt"]


def test_run_py_missing_src_fails(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_RUN_PY), "--study", "STUDY", "--src", str(tmp_path / "nope")],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("RPLN_SKILL_RESULT:")][-1]
    payload = json.loads(marker[len("RPLN_SKILL_RESULT:"):])
    assert payload["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k run_py -v`
Expected: FAIL (run.py does not exist → non-zero exit, no marker).

- [ ] **Step 3: Write `run.py`**

```python
#!/usr/bin/env python3
"""Skill entrypoint: raw-data-intake (skill 0, setup — NOT an orchestrator phase).

Sorts an unorganized study delivery (flat dump and/or zips) into the canonical
data/raw/<study>/ four-bucket layout and drafts config/<study>/_forms_manifest.yaml.
Classification is filename + extension ONLY (GR-1: no workbook is opened).
Idempotent: a no-op on an already-organized tree unless --force.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import intake  # noqa: E402

from scripts.utils.skill_protocol import (  # noqa: E402
    SkillResult,
    add_common_skill_args,
    emit_skill_result,
)


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val) if val else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sort an unorganized study delivery (skill 0).")
    add_common_skill_args(parser)
    parser.add_argument("--src", required=True, help="dir or zip of the unorganized delivery")
    parser.add_argument("--force", action="store_true", help="rebuild an already-organized tree")
    args = parser.parse_args(argv)

    try:
        result = intake.organize(
            args.study,
            Path(args.src),
            force=args.force,
            raw_root=_env_path("RPLN_INTAKE_RAW_ROOT"),
            config_root=_env_path("RPLN_INTAKE_CONFIG_ROOT"),
            audit_dir=_env_path("RPLN_INTAKE_AUDIT_DIR"),
        )
    except (FileNotFoundError, ValueError) as exc:
        emit_skill_result(
            SkillResult(
                skill="raw-data-intake",
                ok=False,
                exit_code=2,
                summary=f"intake failed: {exc}",
                data={"study": args.study},
            )
        )
        return 2

    if result.skipped:
        summary = "already organized — skipping"
    else:
        summary = "; ".join(f"{b}={n}" for b, n in sorted(result.counts.items()) if n)
    emit_skill_result(
        SkillResult(
            skill="raw-data-intake",
            ok=True,
            exit_code=0,
            summary=summary or "no files staged",
            data={
                "study": args.study,
                "skipped": result.skipped,
                "counts": result.counts,
                "unclassified": result.unclassified,
                "manifest_written": result.manifest_written,
                "review_note": result.review_note,
            },
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the run.py tests**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -k run_py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Create `SKILL.md`**

```markdown
---
name: raw-data-intake
description: Skill 0 (setup, NOT a publish phase) — sort an unorganized study delivery (flat dump and/or zips) into the canonical data/raw/<study>/ four-bucket layout (annotated_pdfs, datasets, data_dictionary, _unclassified) and draft config/<study>/_forms_manifest.yaml. Classification is filename + extension ONLY; no workbook is ever opened. Idempotent: a no-op on an already-organized tree unless forced.
---

# Raw Data Intake (Skill 0)

> **Global Rule (GR-1):** No LLM may read dataset row values at any time. This skill classifies on filenames + extensions only — it never opens a workbook. The review note carries file name + bucket-guess + reason code only, never file contents.

## Core Rule

Intake is **not** a publish phase and never touches the per-study pipeline lock.
It runs once, *before* `make study`, turning an unorganized delivery into the
inputs the 10-phase orchestrator already assumes exist. It is **non-destructive**
(copies, never moves the source) and **idempotent** (a no-op on an
already-organized tree unless `FORCE=1`).

## What This Skill Does

1. **Stage** — copy `SRC` (a dir or a `.zip`) into a temp working dir; extract
   any zips. The source is never modified.
2. **Classify** each staged file by name + extension (case-insensitive):
   - `*.pdf` → `annotated_pdfs/`
   - `*.xlsx`/`*.csv` whose name contains `mapping`/`dictionary`/`deb`/`codebook` → `data_dictionary/`
   - other `*.xlsx`/`*.csv` → `datasets/`
   - everything else → `_unclassified/`
3. **Place** into `data/raw/<study>/<bucket>/` — unless the tree is already
   organized (bucket dirs present and `datasets/` non-empty), in which case it
   no-ops, unless `--force`.
4. **Draft** `config/<study>/_forms_manifest.yaml` listing every `datasets/`
   file under `required:` (empty `optional:`/`reject:`), only if no manifest
   exists (a hand-tuned one is never clobbered).
5. **Review note** — if any file landed in `_unclassified/`, write one count-only
   note to `output/<study>/audit/human_review/intake/` (Note 22).

Duplicate / collision-pair resolution stays with `dataset-deduplication` (skill 2).

## CLI

```bash
make organize STUDY=<name> SRC=<dir-or-zip>
make organize STUDY=<name> SRC=<dir-or-zip> FORCE=1   # rebuild an organized tree

python plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py \
  --study <STUDY> --src <dir-or-zip> [--force]
```

Emits a value-free `RPLN_SKILL_RESULT:` line with per-bucket counts.
```

- [ ] **Step 6: Create `agents/llm.yaml`**

```yaml
interface:
  display_name: "Raw Data Intake"
  short_description: "Sort an unorganized study delivery into the canonical data/raw/<study>/ layout (skill 0)"
  default_prompt: "Use $raw-data-intake to sort an unorganized study delivery (flat dump and/or zips) into data/raw/<study>/{annotated_pdfs,datasets,data_dictionary,_unclassified} and draft the forms manifest. Filename + extension only — never open a workbook. Idempotent: no-op on an already-organized tree unless forced. Setup only, not a publish phase."
```

- [ ] **Step 7: Add the Makefile `organize` target**

Add after the `rebuild-llm-source` target (around Makefile line 214). Use the same `$(UV) run --all-groups` + `FFLAG` convention as `study`:

```makefile
INTAKE := plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py

organize: ## Skill 0: sort an unorganized study delivery into data/raw/<study>/ (SRC=dir-or-zip)
	@printf "$(C)Organizing raw delivery for STUDY=$(STUDY) from SRC=$(SRC)...$(N)\n"
	@STUDY_NAME=$(STUDY) $(UV) run --all-groups python $(INTAKE) \
		--study $(STUDY) --src $(SRC) $(FFLAG)
	@printf "$(G)✓ Intake complete for $(STUDY)$(N)\n"
```

Confirm `FFLAG` is the existing `FORCE=1`→`--force` mapping used by `study` (grep `FFLAG` in the Makefile). If `study` maps it to a different flag name, add a local `FFLAG := $(if $(FORCE),--force,)` just above the target. Also add `organize` to the `.PHONY` list (the line near Makefile:91 that lists `sot-*` targets).

- [ ] **Step 8: Register the skill in `plugin.yaml`**

Under the top-level `skills:` list, add an entry mirroring `study-setup`'s shape (role: setup, not a DAG node). Place it first (it runs before everything):

```yaml
  - skill: raw-data-intake
    path: skills/raw-data-intake/SKILL.md
    run: skills/raw-data-intake/scripts/run.py
    role: setup
    scope: raw_delivery
    note: >
      Skill 0 — standalone prep run BEFORE the 10-phase orchestrator. Sorts an
      unorganized delivery into data/raw/<study>/ and drafts the forms manifest.
      Filename + extension only (GR-1); idempotent; never a DAG phase.
```

(Find `study-setup`'s entry in `plugin.yaml` to match indentation and the exact key set; copy its structure.)

- [ ] **Step 9: Full intake test run**

Run: `uv run --all-groups python -m pytest tests/test_raw_data_intake.py -v`
Expected: PASS (all tests).

- [ ] **Step 10: Smoke-test the Makefile target end-to-end**

```bash
mkdir -p /tmp/intake_demo && : > /tmp/intake_demo/a.xlsx && : > /tmp/intake_demo/DEB_mapping.xlsx && : > /tmp/intake_demo/junk.txt
make organize STUDY=Intake-Smoke SRC=/tmp/intake_demo
ls -R data/raw/Intake-Smoke && cat config/Intake-Smoke/_forms_manifest.yaml
# cleanup
rm -rf data/raw/Intake-Smoke config/Intake-Smoke output/Intake-Smoke /tmp/intake_demo
```

Expected: `a.xlsx` under `datasets/`, `DEB_mapping.xlsx` under `data_dictionary/`, `junk.txt` under `_unclassified/`, a DRAFT manifest listing `a.xlsx`, and a `RPLN_SKILL_RESULT:` line with `ok: true`.

- [ ] **Step 11: Lint + doc-freshness + commit**

```bash
uv run ruff check plugins/report-ai-study-pipeline/skills/raw-data-intake scripts/audit/review_paths.py --fix
uv run ruff format plugins/report-ai-study-pipeline/skills/raw-data-intake scripts/audit/review_paths.py
make doc-freshness
git add plugins/report-ai-study-pipeline/skills/raw-data-intake \
        plugins/report-ai-study-pipeline/plugin.yaml Makefile tests/test_raw_data_intake.py
git commit -m "feat(intake): wire skill 0 run.py + make organize + plugin registration"
```

---

## Self-Review

**Spec coverage (against `docs/plans/raw_data_intake_skill_design.md`):**
- §2 target structure → Task 4 `organize` creates all four buckets. ✓
- §3 standalone prep (not a DAG phase, no lock) → Task 5 plugin.yaml `role: setup`; run.py never imports the orchestrator/lock. ✓
- §4 invocation `make organize … FORCE=1` → Task 5 Step 7. ✓
- §5 flow (stage → classify → place → manifest → review note), filename+ext only → Tasks 1, 2, 4. ✓
- §5.3 idempotency no-op + FORCE rebuild → Task 4 `is_already_organized`, tests `test_organize_noop…`/`…force_rebuilds`. ✓
- §5.4 never clobber existing manifest → Task 4 `draft_manifest`, test `…preserves_existing_manifest`. ✓
- §5.5 count-only review note → Task 3 path helper + Task 4 `write_review_note`. ✓
- §6 outputs incl. `RPLN_SKILL_RESULT:` per-bucket counts → Task 5 run.py. ✓
- §7 PHI/security boundary (GR-1, write zones, one-way deps, fail-into-quarantine) → enforced throughout; no workbook opened anywhere. ✓
- §8 out of scope (dedup, fuzzy matching, reject decisions) → not implemented; dedup deferred to skill 2 (noted in SKILL.md). ✓
- §9 files to create/touch → all covered across Tasks 1–5. ✓
- §10 acceptance → Task 5 Step 10 smoke test exercises the flat-dump path; zip path covered by `test_stage_extracts_zip`. ✓

**Placeholder scan:** none — every code step shows complete code; the two "confirm the constant name" notes (`config.CONFIG_DIR`, `FFLAG`) are explicit verification steps with the grep to run, not deferred work.

**Type consistency:** `classify` returns the bucket-string constants used as dict keys in `organize`'s `counts`; `IntakeResult` field names (`counts`, `unclassified`, `manifest_written`, `skipped`, `review_note`) match exactly between Task 4's definition, the run.py consumer, and the tests. `stage_source(src, workdir)` and `organize(study, src, …)` signatures match their call sites.
