---
name: phi-classification
description: Run the header-only PHI handling review before any row value is opened — classify each form's headers by jurisdiction (USA/INDIA), cross-verify direct identifiers against the SoT, and write the authoritative phi_handling_approval.json (approved vs held forms, force-drop columns) consumed downstream by the scrub. Header NAMES + counts only, never row values.
---

# PHI Classification

## Core Rule

This skill runs **before any dataset row value is opened**. It classifies on
column NAMES and SoT (printed-PDF) signals only — never row values. Its output,
`phi_handling_approval.json`, is the authoritative decision record the scrub
later applies; the approval report itself is serialization-guarded so it can
never become a PHI side-channel.

## What This Skill Does

The deterministic jurisdiction PHI-classification gate (Phase 3 / 3b,
header-only). It reaches the trusted host gate
`extract_to_llm_source._run_form_approval_gate`, which for the study's
manifest-approved forms:

- classifies each header by jurisdiction (e.g. `USA`, `INDIA`) into `keep` /
  `drop` / `pseudonymize` / `jitter_date` / `generalize` / `cap` / `suppress`
  decisions,
- cross-verifies direct identifiers against the SoT (printed-PDF question
  signal) so a name/SoT-flagged escapee is force-dropped at the column level,
- records each form as **approved** (publishes, possibly with force-drop
  columns) or **held** (PHI-classification uncertainty → human review), a normal
  partial-publish outcome, not an error,
- writes the authoritative `phi_handling_approval.json` into the run dir.

The adversarial classification probe runs **once** against a frozen rule bundle
(deterministic; re-running cannot change the result), so a held form is a
rule-pattern gap to fix, not a transient condition to retry.

## CLI

```bash
uv run --all-groups python \
  plugins/report-ai-study-pipeline/skills/phi-classification/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID> --run-dir <output/<STUDY>/runs/<RUN_ID>>
```

Flags: `--form <FORM>` (repeatable; omit for all manifest forms),
`--max-workers <N>` (cap the header-review thread pool; default auto).

`--run-dir` is required (the approval file is written there); exit `2` if it is
missing. Exit `0` on success — including when some forms are held (partial
publish is a normal outcome, so the result is reported `ok`).

## Result Contract

Emits one `RPLN_SKILL_RESULT:` marker line (`scripts/utils/skill_protocol.py`):
value-free — approved/held form NAMES, counts, and the `partial` flag only,
never a row value.

## Portability

Pure host-side Python; deterministic, no LLM call, no network. Invoked by the
orchestrator as a file-path subprocess and runnable from any LLM host the same
way.
