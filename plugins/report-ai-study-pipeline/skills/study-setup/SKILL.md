---
name: study-setup
description: Prepare a study for a publish run OUTSIDE the 10-phase DAG — pre-create the run directory tree and report readiness of the required inputs (forms manifest, study privacy config, raw datasets dir, PHI HMAC key); optionally bootstrap a fresh 0600 PHI key when none exists (never overwriting). Setup only, not a publish phase.
---

# Study Setup

## Core Rule

Setup is **not** a publish phase (Gap 4) — it never reads dataset rows and never
mutates published output. The PHI key is **never overwritten**: bootstrapping a
new key only happens when none exists, because overwriting would silently
invalidate every prior pseudonym and force full re-ingestion.

## What This Skill Does

Prepares a study so the orchestrator can run cleanly:

- pre-creates `output/<study>/` + per-run directories (`ensure_directories` +
  `ensure_run_directories`),
- reports readiness of each required input:
  `config/<study>/_forms_manifest.yaml`, `config/<study>/_study_privacy.yaml`,
  `data/raw/<study>/datasets/`, and the PHI HMAC key,
- with `--bootstrap-key`, creates a fresh 0600 HMAC key **only if absent**.

The rich interactive wizard lives in the host UI
(`scripts/ai_assistant/ui/wizard.py`); this entrypoint is the non-interactive
scaffold for scripted setup.

## CLI

```bash
python plugins/report-ai-study-pipeline/skills/study-setup/scripts/run.py \
  --study <STUDY> [--run-id <RUN_ID>] [--bootstrap-key]
```

Exit `0` when all required inputs are present; `1` when any are missing (named in
the result's `readiness` map).

## Result Contract

Emits one `RPLN_SKILL_RESULT:` marker line: a per-input present/absent readiness
map and a `key_created` flag — no secrets, no row values.

## Portability

Pure host-side Python; no LLM call, no network.
