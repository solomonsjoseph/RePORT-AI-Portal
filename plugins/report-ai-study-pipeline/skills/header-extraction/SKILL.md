---
name: header-extraction
description: Read the first-row column headers (NAMES only — metadata, never row values) from a study's manifest-kept datasets, writing them to header_extraction.json so the PHI-classification phase can classify columns before any row value is opened. Use as the first pipeline phase; honours the forms-manifest reject list.
---

# Header Extraction

> **Global Rule (GR-1):** No LLM — including Claude — may read dataset row values at any time, under any circumstance. Column headers (row 1) are the only permitted LLM dataset input. Failure reports carry pattern + column + count only, never a value.

## Core Rule

This skill reads **only the first row** of each dataset — the column NAMES. Row
2+ bytes are never read (the sole isolation point is
`study_intake.read_headers_only`, which closes the file handle after the first
row). Column names are metadata, not PHI row values.

## What This Skill Does

Phase **2b** of the orchestrator (after raw-file dedup, before SoT): the
PHI-classification phase classifies headers, and it cannot run until the headers
exist. For each dataset under `data/raw/<study>/datasets/` (skipping
`reject:`-listed files from `_forms_manifest.yaml`, Excel lock/temp siblings, and
underscore-prefixed control files) it reads the first-row headers and writes:

    <run-dir>/header_extraction.json   →  {"study": ..., "forms": {stem: [headers...]}}

## CLI

```bash
python plugins/report-ai-study-pipeline/skills/header-extraction/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID> --run-dir <output/<STUDY>/runs/<RUN_ID>>
```

Exit `0` when every kept dataset's headers were read; `1` if any dataset was
unreadable (named in the result's `errored_forms`); `2` when the datasets dir is
absent.

## Result Contract

Emits a single `RPLN_SKILL_RESULT:` marker line (the shared skill contract):
value-free — form NAMES and per-form column COUNTS only, never a row value.

## Portability

Pure host-side Python (openpyxl/csv); no LLM call, no network. Runnable from any
LLM host as a file-path subprocess.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Every manifest-kept dataset's first-row headers were read. |
| `1` | One or more datasets were unreadable (named in the result's `errored_forms`). |
| `2` | The datasets directory was not found; also argparse usage error. |

## What This Skill Does NOT Do

- **Does not read dataset row values** — reads only the first row (column NAMES); row 2+ bytes are never opened (GR-1).
- **Does not classify PHI** — it only emits the header lists; the phi-classification phase consumes them.
- **Does not open reject-listed or control files** — `reject:`-listed datasets, Excel lock/temp siblings (`~$*`), and underscore-prefixed files are skipped.
- **Does not deduplicate or extract** — those are separate downstream phases.
