---
name: excel-duplicate-handler
description: Handle duplicate RePORT-AI Excel or CSV dataset files using filename normalization, header-only comparison, and lossless main/branch merge rules. Use for same ordered headers, duplicate-numbered filenames, case/style filename variants, header supersets/subsets, append/union merges, duplicate rows, duplicate columns, duplicate headers, and PHI-safe dedup audits.
---

# Excel Duplicate Handler

## Core Rule

For RePORT-AI raw, staged, or clinical study workbooks, assume row values may contain PHI. Do not read raw row values into the agent context, do not paste samples into chat, and do not bypass the trusted pipeline to inspect data manually.

Allowed for agent reasoning:

- File names, stems, sheet names, and directory shape.
- Manifest entries and study privacy configuration.
- Row-1 headers when the repo's approved header-only paths read them.
- Counts, schema names, drop-event metadata, audit ledgers, verifier reports, and pass/fail statuses.
- Published `output/{STUDY}/llm_source/` artifacts only after the run passed verification.

If the user explicitly provides a non-PHI workbook outside the RePORT-AI raw/staged paths, you may inspect and edit that workbook directly. Still preserve the original file, report intended destructive actions first, and prefer auditable helper columns or review reports over silent overwrite.

## First Decision

Classify the duplicate problem before acting:

1. **Duplicate dataset files**: two or more workbook/CSV files may represent the same form because names differ only by copy number, case, spacing, underscores, hyphens, punctuation, or style.
2. **Same ordered header list**: files have exactly the same row-1 headers in the same order.
3. **Header superset/subset**: one file has all headers from another file plus additional headers.
4. **Partial header overlap**: files share many headers but each has unique columns, or the same headers appear in a different order.
5. **Duplicate row-1 headers inside one file**: same header appears more than once in a source workbook, especially during SoT policy creation.
6. **Duplicate columns inside one sheet**: Excel-autocomplete artifacts such as `SUBJID` and adjacent `SUBJID2`, or entirely-null duplicate-looking columns.
7. **Duplicate records/rows**: exact or near-identical rows within one sheet or JSONL output.
8. **Duplicate sheets/tables**: repeated tabs or split tables inside one workbook.
9. **Duplicate PDF annotations or field labels**: Source Truth problem, not a spreadsheet-cleaning problem.

When the type is unclear, inspect filenames, manifests, headers, and existing audit reports first. Do not open row values just to decide.

## Project Paths

Use the existing project surfaces:

- Dataset publish skill: `skills/dataset-to-llm-source/SKILL.md`
- Merge helper: `skills/excel-duplicate-handler/scripts/merge_excel_duplicates.py`
- Dataset CLI: `scripts/skills/extract_to_llm_source.py`
- Column dedup logic: `scripts/extraction/dedup.py`
- Duplicate-file cleanup: `scripts/extraction/dataset_cleanup.py`
- Dataset cleanup docs: `docs/sphinx/developer_guide/data_extraction_datasets.rst`
- SoT duplicate-header rules: `skills/sot-lean-generator/SKILL.md`

Prefer these surfaces over one-off workbook scripts for RePORT-AI study data.

## PHI-Safe Workflows

### Duplicate Dataset Files

This is the primary workflow for this skill. Use header-only and filename-only evidence to classify candidate duplicate files before any data-value comparison happens.

Build a review table with one row per candidate group:

- original filenames;
- normalized filename key;
- sheet names, if available without reading values;
- header count per file;
- header relationship;
- suggested action;
- confidence;
- reason for human review, if any.

Normalize filenames conservatively:

- lowercase;
- strip extension;
- trim surrounding whitespace;
- collapse spaces, underscores, hyphens, and repeated punctuation;
- remove obvious copy suffixes such as `_1`, `-1`, `(1)`, `copy`, `copy 2`, and Excel-generated duplicate markers;
- preserve meaningful form tokens such as numbers, letters, and clinical words.

Do not normalize away information that could distinguish forms. For example, `14_CaseControl` and `14_Case_Control` can share a normalized key, but `1A` and `1B` cannot.

Classify header relationships:

- `exact_ordered_headers`: row-1 headers are identical and in the same order.
- `same_header_set_different_order`: same headers, different order.
- `header_superset`: one file's headers contain every header from another file plus extra headers.
- `header_subset`: inverse of `header_superset`.
- `partial_overlap`: files share some headers but neither contains the other.
- `same_name_different_headers`: filename evidence says duplicate, but headers disagree.
- `different_name_exact_headers`: names differ, but ordered headers match.
- `mixed_duplicate_signals`: a combination of name variants, numbered copies, exact headers, supersets, and partial overlaps appears in one candidate group.

Action rules:

- Exact ordered headers plus normalized filename match: strong duplicate-file candidate. Register or verify through `SUSPECTED_DUPLICATE_PAIRS` and the trusted cleanup pipeline before removal.
- Exact ordered headers but different normalized filenames: possible duplicate or reused schema. Do not remove automatically; compare manifest/PDF/form context and report for review.
- Same header set in a different order: possible duplicate with column reordering. Do not remove automatically unless project code explicitly proves order is non-semantic for this source.
- Header superset/subset: do not treat the smaller file as junk by header evidence alone. The superset may be a newer revision, an expanded form, or a different extract. Preserve both until manifest/PDF context or pipeline audit proves what to keep.
- Same normalized filename but different headers: high-risk conflict. Preserve both and report as human review required.
- Mixed signals: split into the smallest explainable groups; do not force one canonical file for the whole group unless every member has a documented reason.

For RePORT-AI raw files, the agent may produce a candidate plan from names and headers only. Actual merge/drop behavior must be implemented or verified inside `scripts/extraction/dataset_cleanup.py` so any row-level comparison stays in staging and reports only counts, filenames, schemas, and audit events.

### Actual Merge Output

When the user asks to test or execute a merge, the skill must produce an actual merged workbook unless the candidate set is unsafe to merge.

Use:

```bash
uv run --all-groups python skills/excel-duplicate-handler/scripts/merge_excel_duplicates.py \
  --study <study> \
  --dataset <dataset> \
  --main data/raw/<study>/datasets/<main>.xlsx \
  --branch data/raw/<study>/datasets/<branch>.xlsx
```

For all Excel lock/temp siblings like `~$10_TST.xlsx` in a dataset directory:

```bash
uv run --all-groups python skills/excel-duplicate-handler/scripts/merge_excel_duplicates.py \
  --study <study> \
  --dataset-dir data/raw/<study>/datasets
```

By default, production merge output follows the project structure:

```text
data/raw/<study>/_dataset/
data/raw/<study>/datasets/<dataset>.xlsx
output/<study>/audit/datasets/<dataset>/merge_report.md
output/<study>/audit/datasets/<dataset>/merge_provenance.csv
output/<study>/audit/dataset_duplicate_merge_report.md
output/<study>/audit/human_review/excel/<candidate_group>/duplicate_review_report.md
```

The `_dataset` path is a full snapshot of the original raw `datasets/` folder. The active `datasets/` path contains the cleaned working set: unchanged non-duplicate files, safe merged main files, and no active copy of branch files that were safely merged or invalid lock/temp artifacts that were skipped. For scratch tests, use `--artifact-root tmp/excel_duplicate_handler_test/project` to create the same relative structure under a test root instead of writing to the real `data/raw/` or `output/` roots:

```text
tmp/excel_duplicate_handler_test/project/data/raw/<study>/_dataset/
tmp/excel_duplicate_handler_test/project/data/raw/<study>/datasets/<dataset>.xlsx
tmp/excel_duplicate_handler_test/project/output/<study>/audit/datasets/<dataset>/merge_report.md
tmp/excel_duplicate_handler_test/project/output/<study>/audit/datasets/<dataset>/merge_provenance.csv
tmp/excel_duplicate_handler_test/project/output/<study>/audit/dataset_duplicate_merge_report.md
tmp/excel_duplicate_handler_test/project/output/<study>/audit/human_review/excel/<candidate_group>/duplicate_review_report.md
```

Rules for the helper:

- It may copy row values internally only to create the merged workbook.
- It must not print, log, or write raw row values to chat, markdown reports, JSON summaries, or audit text.
- Reports and provenance must live under the audit folder, following the repo pattern `output/<study>/audit/datasets/<dataset>/`.
- Unsafe or ambiguous candidates must write a count/header-only report under `output/<study>/audit/human_review/excel/<candidate_group>/duplicate_review_report.md`.
- The full original raw `datasets/` folder must be copied to `data/raw/<study>/_dataset/` before any active dataset file is replaced or removed.
- Human-review cases must not create a raw dataset replacement, merge report, or provenance CSV; they may still rely on the run-level `_dataset/` snapshot.
- Never overwrite an existing `_dataset/` snapshot; create a numbered backup directory when `_dataset/` already exists.
- The merged workbook must not be written to `llm_source/`; keep it at the raw dataset path unless a later trusted pipeline promotes a PHI-clean derivative.
- Reports must be count/header/provenance-only.
- Invalid Excel lock/temp artifacts such as `~$*.xlsx` are skipped as non-mergeable branches and recorded in the per-dataset report.
- Invalid non-lock branch workbooks must route to `audit/human_review/` without creating a backup or merged workbook.
- Directory-level lock/temp processing must pair only `~$<dataset>.xlsx` with `<dataset>.xlsx`. If the main workbook is absent, skip the group and record that in `dataset_duplicate_merge_report.md`.
- The merged workbook must preserve the main workbook as the base file, including date values, number formats, formulas, widths, styles, workbook metadata, and sheets.
- After a safe merge, remove the active branch duplicate file from `datasets/`; the original branch file remains in `_dataset/` and its row provenance remains in audit.
- Remove invalid active Excel lock/temp branch artifacts such as `~$*.xlsx` after they are recorded; the original artifact remains in `_dataset/`.
- Do not put audit sheets in the merged workbook. Write `merge_report.md` and `merge_provenance.csv` under the audit folder instead.
- Validate header safety before creating any backup or output workbook. Build the merge in a temporary workbook first, and replace the raw dataset path only after the merge workbook has been created successfully.
- Valid branch rows must be appended after the existing main rows. Preserve branch cell values, number formats, formulas, styles, and comments when copying.
- Exact duplicate rows may be collapsed only when the helper proves the aligned full row is identical. The report must record counts, not values.
- If headers cannot be aligned safely, stop without writing a merged data workbook and write a human-review report instead.

### Main/Branch Merge Rules

When duplicate files are confirmed, treat the chosen file as **main** only as the merge target. Treat every other file as a **branch** whose information must be retained, appended, or explicitly quarantined for review. Main selection must never mean "overwrite branch data."

Choose main in this order:

- the original or manifest-declared canonical file, when one is clear;
- otherwise, the file with the maximum trusted entry/record count, computed inside the staging pipeline;
- otherwise, for header-only planning only, the file with the richest compatible header set;
- if those conflict, stop and ask for human review.

Lossless merge invariants:

- Never overwrite a non-empty main value with a branch value.
- Never drop a branch row, branch column, branch sheet, or branch-only field without an audit event and a verifier-backed reason.
- Exact duplicate rows may collapse to one retained row only when the trusted pipeline proves they are identical; record the branch provenance.
- Branch rows not already present in main must be appended to main.
- Branch-only columns must be added to the merged schema, not discarded. Existing main rows get null/blank values for those added columns unless the pipeline can safely populate them.
- For header superset/subset groups, prefer the original file as main when known; otherwise prefer the superset as main only when the extra headers are compatible. Append subset branch rows into the superset schema.
- For same headers in different order, align by header name, not by raw column position, only after duplicate header names have been resolved.
- If two records appear to represent the same entity/timepoint but have conflicting non-empty values, preserve both versions or quarantine the conflict. Do not guess which value is correct.
- If a branch has information that cannot be mapped safely to main, preserve the branch as a separate output/review item instead of forcing a merge.

The merge output must include enough provenance to reconstruct which source file, sheet, and row contributed each retained record. The user-facing report may show counts and filenames only, not raw values.

### Duplicate Columns

For project datasets, use the dataset extraction pipeline rather than direct workbook reads. The canonical column rule is `clean_duplicate_columns` in `scripts/extraction/dedup.py`.

The current safe auto-drop bar is intentionally strict:

- Name matches the configured duplicate-column pattern, such as `BASE2` or `BASE_2`.
- Base column exists.
- Candidate is entirely null, or all of these are true:
  - dtype matches the base column exactly;
  - candidate is positionally adjacent to the base column;
  - values are 100% identical with null-equality.

Report dropped columns from cleanup events and ledgers: dropped name, kept name, file, sheet, reason, and counts. Do not show raw values.

### Duplicate Row-1 Headers

For Source Truth work, duplicate headers are not routine cleanup. Use `$sot-lean-generator` and keep PDF clinical meaning separate from dataset binding.

Rules:

- Row-1 headers are allowed as binding input.
- Do not read row 2+ values.
- Do not silently merge duplicate headers.
- Combine duplicate source columns into one final policy variable only when source positions and PDF context prove they are the same concept.
- Document any safe merge as `dataset_duplicate_header_combined_binding`.
- Stop and ask the user when duplicate headers may have different meanings or the source positions are insufficient.

### Duplicate Records or Rows

For RePORT-AI raw or staged data, do not compute duplicate-row examples in the agent context. Use trusted pipeline outputs and cleanup ledgers. If the code needs enhancement, patch pipeline code and tests rather than writing an ad hoc reader that prints rows.

Acceptable reporting:

- number of duplicate rows or merge actions;
- source file and sheet;
- rule or suspected-pair identifier;
- output ledger paths;
- whether the run passed verification.

Not acceptable:

- sample rows;
- patient identifiers;
- cell values;
- before/after row dumps.

### Duplicate Sheets or Tables

If this is a project raw workbook, treat sheet names and schemas as safe metadata but avoid row values. If direct data comparison is required, implement it inside the pipeline with count-only/audit-only reporting.

For non-PHI workbooks, create a copy and produce a review sheet or markdown report before deleting sheets/tables.

## Commands

Preflight the dataset skill contract:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py status
```

Run one dataset through the PHI-safe pipeline:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
  --study Indo-VAP --form 6_HIV
```

Verify the latest run:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
  --study Indo-VAP
```

Focused duplicate tests after behavior changes:

```bash
uv run --all-groups python -m pytest \
  tests/test_dedup.py \
  tests/test_dataset_pipeline.py \
  tests/test_dataset_cleanup.py \
  tests/skills/sot-lean-generator/test_smoke_6_hiv.py -q
```

If only this skill text changes:

```bash
uv run --all-groups python -m pytest \
  tests/skills/test_excel_duplicate_handler_skill.py -q
```

## Reporting Checklist

When reporting duplicate handling, include:

- duplicate type and scope;
- study/form/file/sheet names;
- normalized filename keys;
- header relationship classification;
- header counts and header names only;
- actions taken or proposed;
- counts and column/header names only;
- ledger/report paths;
- verification command and result.

Do not claim a duplicate was safely removed unless there is an audit event or verifier result that proves it. When a duplicate is ambiguous, preserve it and list the reason it needs human review.

## When to Route Elsewhere

- Use `$dataset-to-llm-source` for raw workbook to published `llm_source` runs, verification, and operational dataset cleanup.
- Use `$sot-lean-generator` for duplicate row-1 headers in Source Truth YAML or joined query-view generation.
- Use a general spreadsheet skill only for explicitly non-PHI workbook edits that are not part of the RePORT-AI study pipeline.
