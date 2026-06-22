---
name: dataset-deduplication
description: Raw-file dataset deduplication (Note 4) — filename normalization, header/row-count tiers, no cell reads. Orchestrator phase 2, before shared header extraction (Note 6), SoT, and extraction.
---

# Dataset Deduplication

## Core Rule (GR-1 + Note 4)

**No LLM and no dedup logic may read dataset row values.** Column headers
(row 1) and row **counts** only. Lock/temp files (``~$*.xlsx``) are ignored
automatically.

## What This Skill Does

Orchestrator **phase 2**. Deduplicates **raw** Excel/CSV files under
``data/raw/<study>/datasets/`` via ``scripts/extraction/raw_file_dedup.py``
**before** the shared header-extraction store (Note 6) is built. Reads row-1
column headers internally when comparing duplicate candidates — it does not
consume the Note 6 temp store.

1. **Normalize** filenames (strip underscores, numeric suffixes, case).
2. **Group** files sharing the same normalized base (2+ = duplicate candidate set).
3. **Tier 1 — perfect column match:** identical header name/count/order;
   row counts equal → auto-resolve (archive duplicates); row counts differ →
   human review.
4. **Tier 2 — header superset:** one file's columns are a strict superset of
   another's → keep the file with the maximum column count; archive others.
5. **All other cases** → human review (count-only note under
   ``audit/human_review/``).

JSONL-level duplicate merging (``clean_trio_datasets`` pair merge) is **retired**
from the production path (legacy unit tests only).

## CLI

```bash
uv run --all-groups python \
  plugins/report-ai-study-pipeline/skills/dataset-deduplication/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID>
```

Exit ``0`` on success (including held-for-review groups); ``1`` on I/O or
manifest errors.

## Result Contract

Emits one ``RPLN_SKILL_RESULT:`` marker: auto-resolved / held / archived /
error **counts** only — never a row value.

## Portability

Pure host-side Python; no LLM call, no network.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Dedup completed — including groups routed to held-for-review (a held group is a normal, non-error outcome). |
| `1` | Dedup failed on an I/O or manifest error (the exception type NAME only is reported). |
| `2` | Argparse usage error (e.g. missing `--study`). |

## What This Skill Does NOT Do

- **Never reads dataset cell values** — uses filename normalization, header NAMES, and row **counts** only (GR-1 + Note 4).
- **Does not auto-resolve ambiguous groups** — anything beyond the Tier 1 (exact-header) / Tier 2 (superset) cases routes to count-only human review.
- **Does not delete originals destructively** — removed duplicates are archived, not erased.
- **Does not merge at the JSONL row level** — the legacy `clean_trio_datasets` pair merge is retired from the production path.
