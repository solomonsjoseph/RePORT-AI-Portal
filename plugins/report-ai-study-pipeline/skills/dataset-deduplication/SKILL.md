---
name: dataset-deduplication
description: Clean the staging datasets tree — remove known junk files and merge only provably-safe duplicate file pairs (keeping the larger when one is a strict subset); route value-divergent pairs to human review instead of union-merging. Fail-closed scrub-first — refuses to run on unscrubbed rows. Counts/names only, never row values.
---

# Dataset Deduplication

## Core Rule

**Accuracy with no middle ground.** A duplicate pair is merged only when one
member is *provably* a strict subset of the other. A value-divergent pair
(neither is a subset) is NEVER union-merged — auto-union could drop real
clinical rows or fabricate duplicate subject records — it is routed to human
review with a count-only note. What must be preserved is preserved exactly.

## What This Skill Does

Phase 2 of the publish pipeline. Wraps `dataset_cleanup.clean_trio_datasets`
over the staging tree (`tmp/<study>/datasets/`):

- removes known junk files (`JUNK_PATTERNS`),
- structurally compares suspected duplicate pairs and merges the provably-safe
  ones (subset → keep larger),
- routes divergent pairs to `audit/human_review/<stem>/` (count-only note),
- emits the unified `dataset_cleanup_ledger.as_written.json` per dataset.

**Fail-closed scrub-first:** a pre-flight guard refuses to proceed unless every
staging row carries the `_phi_scrubbed` marker, so file-level merge can never
touch unscrubbed PHI.

## CLI

```bash
python plugins/report-ai-study-pipeline/skills/dataset-deduplication/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID>
```

Exit `0` on success (including divergent-pair-held, a normal outcome); `1` when
the scrub-first guard refuses or an I/O error occurs.

## Result Contract

Emits one `RPLN_SKILL_RESULT:` marker line: junk-removed / merged /
held-for-review / error COUNTS only — never a row value.

## Portability

Pure host-side Python; no LLM call, no network.
