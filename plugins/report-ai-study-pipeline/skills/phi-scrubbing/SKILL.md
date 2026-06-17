---
name: phi-scrubbing
description: Run the fail-closed per-form PHI scrub over the staged dataset JSONL — date jitter, ID pseudonymize, drop/generalize/band, force-drop, small-cell suppression — rewriting rows in place, quarantining un-scrubbable rows, and emitting count-only per-dataset audit ledgers. Defaults to partial-publish-on-review; --strict restores strict-abort. Trusted code path: handles row values internally, emits counts/metadata only.
---

# PHI Scrubbing

## Core Rule

This is the **trusted scrub code path** — it does read and rewrite dataset row
values internally, because scrubbing PHI requires touching the data. It never
exposes those values: rows are rewritten in place in staging (never the LLM read
zone), and every artifact it emits (audit ledgers, scrub-outcome sidecar) is
**counts / field-names / reason-codes only**. The scrub is **fail-closed** — an
un-scrubbable row is never promoted.

## What This Skill Does

The fail-closed per-form PHI scrub (Phase 4). It wraps
`scripts.security.phi_scrub.run_scrub` over the staged dataset JSONL, applying
the priority-ordered scrub rules: priority-0 force-drop (direct identifiers
flagged by classification) → keep → birthdate → drop → cap → generalize → band →
suppress_small_cell → date jitter → id pseudonymize. It also:

- jitters dates per-subject (keyed off subject_id so visit intervals survive),
- pseudonymizes the subject ID (`RID_<LABEL>_<alpha12>`, required for linkage),
- quarantines un-scrubbable / orphan (no resolvable subject_id) rows to the
  AMBER no-LLM `staging/quarantine/` zone — never promoted,
- emits the per-dataset PHI audit ledger and, when a `run_id` is supplied, the
  `runs/<run_id>/scrub_outcome.json` sidecar (per-form kept/quarantined counts +
  reason codes + `elevated` flag).

**Modes.** Defaults to **partial-publish-on-review** (one bad form quarantines
only its failing rows and is flagged `elevated`; the rest of the study still
publishes). `--strict` restores strict-abort: the first un-scrubbable row raises
a `PHIScrubError` and aborts the whole study.

## CLI

```bash
uv run --all-groups python \
  plugins/report-ai-study-pipeline/skills/phi-scrubbing/scripts/run.py \
  --study <STUDY> --run-id <RUN_ID> --run-dir <output/<STUDY>/runs/<RUN_ID>>
```

Flags: `--strict` (strict-abort on the first un-scrubbable row; default is
partial-publish-on-review).

Exit `0` when the scrub completes (including partial-publish with quarantined
rows); `1` when it fail-closes (`PHIScrubError`, e.g. an unmappable
band/generalize value, an unshiftable date, a missing scrub config, or a
strict-mode abort).

## Result Contract

Emits one `RPLN_SKILL_RESULT:` marker line (`scripts/utils/skill_protocol.py`):
value-free — study name, mode, run id, and on failure the `PHIScrubError`
subclass NAME only, never a row value.

## Portability

Pure host-side Python; fail-closed, no LLM call, no network. Invoked by the
orchestrator as a file-path subprocess and runnable from any LLM host the same
way.
