# Skill 0 — `raw-data-intake` Design Spec

> Historical implementation plan is no longer current documentation. Durable architecture lives in `docs/sphinx`; this was a working design spec for the raw-data-intake plugin skill.

**Date:** 2026-06-26
**Status:** Approved design, pending implementation plan
**Branch:** PHI_handing_review

## 1. Purpose

Turn an unorganized study delivery (a flat dump of mixed files and/or zip
archives) into the canonical `data/raw/{STUDY}/` layout plus a draft
`_forms_manifest.yaml`, so the existing 10-phase pipeline runs unchanged.

This is **skill 0**: a setup act that happens *before* `make study`, producing
the organized inputs the rest of the pipeline already assumes exist.

## 2. Target structure (what every downstream skill expects)

```
data/raw/{STUDY}/
├── annotated_pdfs/     # printed CRF forms, one .pdf per form
├── datasets/           # one .xlsx/.csv per form (e.g. 12A_FUA.xlsx)
├── data_dictionary/    # the DEB→tables mapping workbook
└── _unclassified/      # quarantine for files that can't be confidently placed
config/{STUDY}/
└── _forms_manifest.yaml   # DRAFT required/optional/reject list
```

## 3. Placement decision

**Standalone prep skill** (like `study-setup`), NOT an orchestrator phase. It
leaves the locked publish DAG and per-study lock untouched. Run once; the
10-phase orchestrator then consumes the organized tree normally.

## 4. Invocation

```bash
make organize STUDY=<name> SRC=<dir-or-zip>
make organize STUDY=<name> SRC=<dir-or-zip> FORCE=1   # overwrite existing organized tree
```

`make organize` delegates to
`plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py`,
following the same subprocess + `RPLN_SKILL_RESULT:` marker convention
(`scripts/utils/skill_protocol.py`) as the other skills.

## 5. Flow (deterministic — filename + extension only, never opens a workbook)

1. **Stage.** Copy `SRC` into a temp working dir. If any `*.zip`, extract in
   place. Non-destructive: source files are never moved or modified.
2. **Classify** each staged file by name + extension (case-insensitive):
   - `*.pdf` → `annotated_pdfs/`
   - `*.xlsx`/`*.csv` whose name matches any of `{*mapping*, *dictionary*,
     *DEB*, *codebook*}` → `data_dictionary/`
   - other `*.xlsx`/`*.csv` → `datasets/`
   - everything else, or a dictionary-pattern collision that can't be resolved
     to exactly one file → `_unclassified/`
3. **Place.** First check whether `data/raw/{STUDY}/` is already organized — i.e.
   the bucket dirs exist and at least `datasets/` is non-empty. If so, **leave it
   as is and no-op** (log `already organized — skipping`), unless `FORCE=1` is
   given to rebuild. Otherwise copy each file into `data/raw/{STUDY}/{bucket}/`.
4. **Draft manifest.** Write `config/{STUDY}/_forms_manifest.yaml` listing every
   `datasets/` file under `required:`, with empty `optional:`/`reject:` and a
   header comment: `# DRAFT — operator must confirm before make study.`
   Skip entirely if a manifest already exists (never clobber a hand-tuned one).
5. **Review note.** If `_unclassified/` is non-empty, write one count-only note
   to `output/{STUDY}/audit/human_review/intake/` carrying filename +
   bucket-guess + reason code only — never file contents.

## 6. Outputs

- Organized `data/raw/{STUDY}/` tree.
- Draft `config/{STUDY}/_forms_manifest.yaml` (unless one exists).
- Optional `output/{STUDY}/audit/human_review/intake/` review note.
- `RPLN_SKILL_RESULT:` summary with per-bucket counts.

## 7. PHI / security boundary

- **GR-1 honored:** never reads dataset row values. Classification is on
  filenames + extensions only; no workbook is opened.
- Writes only to `data/raw/`, `config/`, and `output/*/audit/`. **Never** writes
  `llm_source/`.
- One-way dependency rule honored: `plugins/ → scripts/` only.
- Idempotent + fail-closed: an already-organized tree is left untouched (no-op
  without `FORCE=1`); unclassifiable files quarantine rather than land silently
  in `datasets/`.

## 8. Out of scope (YAGNI — owned by existing skills)

- Duplicate / collision-pair resolution → `dataset-deduplication` (skill 2).
- Fuzzy filename → canonical-form-stem matching (filenames assumed reasonable).
- Reading row values or headers for classification.
- Manifest `reject:` decisions (operator + skill 2 own these).

## 9. Files to create / touch

- `skills/raw-data-intake/SKILL.md` — platform-neutral spec.
- `skills/raw-data-intake/scripts/run.py` — subprocess entry, emits marker.
- `skills/raw-data-intake/scripts/intake.py` — the deterministic organizer.
- `skills/raw-data-intake/agents/llm.yaml` — adapter metadata (mirrors siblings).
- `Makefile` — add the `organize` target.
- `plugins/report-ai-study-pipeline/plugin.yaml` + README skill list — register.

## 10. Acceptance

- `make organize STUDY=X SRC=<flat dump>` produces the four-bucket tree + draft
  manifest; re-running on an already-organized tree no-ops and leaves it
  untouched; `FORCE=1` rebuilds.
- A zip-only `SRC` is extracted then classified identically.
- An unrecognized file lands in `_unclassified/` and produces a count-only note.
- No workbook is opened (no PHI read); the deterministic test suite stays green.
