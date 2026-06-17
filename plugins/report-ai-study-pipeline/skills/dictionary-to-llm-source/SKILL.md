---
name: dictionary-to-llm-source
description: Extract the study data dictionary into staging JSONL so a later publish step can promote it into llm_source/dictionary_mapping/. The dictionary leg carries no PHI row values — only codelist/variable metadata, with reference URLs masked at publish time. Use as the dictionary publish leg of the pipeline.
---

# Dictionary to LLM Source

## Core Rule

This skill handles the study **data dictionary**, not dataset row values. The
dictionary is variable/codelist metadata (column definitions, code lists,
help-text) — it carries no PHI row values. Reference URLs in staff-authored
codelist help-text are masked to `<URL_REMOVED>` at publish time, so the leak
gate stays maximally broad.

## What This Skill Does

The data-dictionary extraction leg (Phase 1, no PHI). It wraps
`scripts.extraction.load_dictionary.load_study_dictionary`, which loads the
study's data dictionary into staging JSONL (`tmp/<study>/dictionary/`); a later
publish step promotes it into `llm_source/dictionary_mapping/jsonl/` so the LLM
sees variable definitions aligned with the published datasets.

By default missing-data NA tokens are **preserved** (a documented codelist value
such as a defined "not applicable" entry is meaningful metadata); pass
`--no-preserve-na` to drop them instead.

## CLI

```bash
uv run --all-groups python \
  plugins/report-ai-study-pipeline/skills/dictionary-to-llm-source/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID> --run-dir <output/<STUDY>/runs/<RUN_ID>>
```

Flags: `--no-preserve-na` (drop NA tokens instead of preserving them).

Exit `0` when the dictionary was extracted to staging; `1` on extraction
failure.

## Result Contract

Emits a single `RPLN_SKILL_RESULT:` marker line (the shared skill contract,
`scripts/utils/skill_protocol.py`): value-free — study name and the ok/failed
outcome only, never a dictionary value or a dataset row value.

## Portability

Pure host-side Python; no LLM call, no network. Invoked by the orchestrator as a
file-path subprocess and runnable from any LLM host the same way.
