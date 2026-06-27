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
   - `*.pdf` -> `annotated_pdfs/`
   - `*.xlsx`/`*.csv` whose name contains `mapping`/`dictionary`/`deb`/`codebook` -> `data_dictionary/`
   - other `*.xlsx`/`*.csv` -> `datasets/`
   - everything else -> `_unclassified/`
3. **Place** into `data/raw/<study>/<bucket>/` -- unless the tree is already
   organized (bucket dirs present and `datasets/` non-empty), in which case it
   no-ops, unless `--force`.
4. **Draft** `config/<study>/_forms_manifest.yaml` listing every `datasets/`
   file under `required:` (empty `optional:`/`reject:`), only if no manifest
   exists (a hand-tuned one is never clobbered).
5. **Review note** -- if any file landed in `_unclassified/`, write one count-only
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

## Result Contract

`RPLN_SKILL_RESULT:` JSON with:

- `ok` — `true` on success (including a no-op skip), `false` on error (missing SRC, etc.)
- `exit_code` — 0 on success, 2 on error
- `summary` — per-bucket counts (`datasets=N; annotated_pdfs=N; ...`) or `"already organized — skipping"`
- `data.counts` — `{datasets, annotated_pdfs, data_dictionary, _unclassified}` (all int)
- `data.unclassified` — list of filenames that landed in `_unclassified/`
- `data.manifest_written` — `true` if a new draft manifest was created
- `data.skipped` — `true` if the tree was already organized and `--force` was not passed
- `data.review_note` — absolute path to the intake review note, or `null` if no unclassified files

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (organized, or no-op skip) |
| 2 | Error — missing SRC dir or invalid arguments |

## Portability

- Python 3.11+; no external dependencies beyond the stdlib and the repo's `scripts/` package.
- Path roots (`data/raw/`, `config/`, `output/`) may be overridden via env vars for testing:
  `RPLN_INTAKE_RAW_ROOT`, `RPLN_INTAKE_CONFIG_ROOT`, `RPLN_INTAKE_AUDIT_DIR`.

## What This Skill Does NOT Do

- Does NOT open any workbook or read dataset row values (GR-1).
- Does NOT resolve duplicate files — that is `dataset-deduplication` (skill 2).
- Does NOT fuzzy-match filenames or make reject decisions.
- Does NOT touch the per-study pipeline lock (never a DAG phase).
- Does NOT clobber a hand-tuned `_forms_manifest.yaml`.
