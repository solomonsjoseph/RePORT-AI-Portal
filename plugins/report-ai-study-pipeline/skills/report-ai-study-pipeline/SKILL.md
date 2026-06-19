---
name: report-ai-study-pipeline
description: Use when a user wants the full RePORT-AI study preparation workflow as one portable LLM plugin. The orchestrator IS the pipeline — a 10-phase state machine that drives header/dictionary extraction, deduplication, Source Truth, PHI classification + scrubbing, audit verification, the PHI guard gate, promotion, the cleanup verifier, and the immutable snapshot, end to end.
---

# RePORT-AI Study Pipeline (Orchestrator)

> **Global Rule (GR-1):** No LLM — including Claude — may read dataset row values at any time, under any circumstance. Column headers (row 1) are the only permitted LLM dataset input. Failure reports carry pattern + column + count only, never a value.

## Core Rule

This is the platform-neutral **orchestrator** for the bundled RePORT-AI skills.
The plugin IS the pipeline: a single 10-phase state machine
(`skills/report-ai-study-pipeline/scripts/run.py`) holds the per-study pipeline
lock for the whole run and drives every phase to completion. Operators launch it
with:

```bash
make study STUDY=<name>
```

`make study` exports `STUDY_NAME` and delegates to the orchestrator. Do not run
the host publish engine (`scripts.pipeline.host_pipeline`) or the publish
supervisor directly for a normal build — the orchestrator owns lock acquisition,
phase ordering, the redundant-run short-circuit, and the snapshot commit.

**Do not read raw dataset row values into the agent context.** Dataset row 2+
values may only be handled inside trusted repo code paths that scrub, merge,
clean, audit, or publish data and expose metadata-only reports. Skills read row-1
column NAMES and metadata only.

## The 10 Phases

The conceptual 10 runtime phases map onto the supervisory steps `run.py` drives.
The contiguous publish phases 2–7 are executed by the proven
`$dataset-to-llm-source` publish supervisor in one locked subprocess (which in
turn invokes the host publish engine `scripts.pipeline.host_pipeline`), rather
than being re-decomposed into separate subprocesses.

| Phase | Action | Skill(s) |
|---|---|---|
| 0 | Config validation ∥ rulebook fetch/drift · input-fingerprint redundant-run check · dir pre-creation · acquire lock | (shared modules) |
| 1 | Header extraction ∥ dictionary extraction (column NAMES only) | `$header-extraction` ∥ `$dictionary-to-llm-source` |
| 2 | Per-form deduplication (scrub-first; provably-safe merges only) | `$dataset-deduplication` |
| 3 | Source Truth ∥ PHI classification ∥ full data extraction | `$sot-lean-generator` ∥ `$phi-classification` ∥ `$dataset-to-llm-source` |
| 3b | Cross-form PHI-classification consistency barrier | `$phi-classification` |
| 4 | Per-form PHI scrub (fail-closed) | `$phi-scrubbing` |
| 5 | 14-assertion audit verification | `$audit-verification` |
| 6 | PHI guard gate (Presidio + pyCANON, OR-combined) → atomic promotion | `$dataset-to-llm-source` |
| 7 | Cleanup propagation ∥ staging destruction + attestation ∥ key zero | `$dataset-to-llm-source` + orchestrator |
| 8 | Cleanup verifier over published tree + cleanup ledgers | orchestrator module |
| 9 | Idempotent 14-assertion re-verify | `$audit-verification` |
| 10 | Snapshot → current pointer → status.json → lock release | orchestrator module |

`$phi-rulebook` is a shared-module skill consumed in phase 0 and by
`$phi-classification` (not a DAG node). `$study-setup` is interactive scaffolding
and is **not** an orchestrator phase. `$excel-duplicate-handler` is retained as
a **legacy** maintainer-only helper (superseded by `$dataset-deduplication` at
orchestrator phase 2 — Note 18).

## Execution Unit

The atomic unit after duplicate preflight is a **raw-file set**: one canonical
form/work unit containing the associated raw dataset workbook or CSV, matching
printed PDF when Source Truth is required, manifest/privacy context, duplicate
variants already resolved or held for review, and the per-set output/audit
status.

Valid set statuses:

- `ready`
- `held_duplicate_review`
- `held_sot_review`
- `held_publish_review`
- `complete`

A held raw-file set blocks only itself, not unrelated ready sets.

## Maintainer Resume Loop

After a partial run holds forms for human review, a maintainer resolves the
inputs and re-runs:

```bash
make study STUDY=<name>            # first build (commits a snapshot if clean)
# orchestrator passthrough for the human-review resume cycle:
uv run --all-groups python plugins/report-ai-study-pipeline/skills/report-ai-study-pipeline/scripts/run.py \
  --study <name> --resume-held
```

`--resume-held` re-publishes the **full surviving set** (approved ∪ held), never
the held subset alone (promotion is a whole-leg atomic replace). A clean pass
commits an immutable snapshot under `output/<STUDY>/snapshots/<id>/` and points
`current.json` at it. The retry loop is a CLI/maintainer workflow only and is
never triggered from the Load Study UI.

## When To Use Only One Child Skill

If the user asks only about duplicate files, use `$dataset-deduplication`
(orchestrator phase 2). The legacy `$excel-duplicate-handler` merge helper is
not invoked by the publish path.
If the user asks only about PDF/header Source Truth policy YAML, use
`$sot-lean-generator`. If the user asks only to run or verify PHI-safe dataset
publishing, use `$dataset-to-llm-source`. Use this orchestrator when the request
spans more than one phase, when the phase is unclear, or when the user asks for
the full repo/plugin study build.

## Portability

This plugin is not Codex-only. Any LLM platform can use it by reading
`plugin.yaml`, this orchestrator, and the child `SKILL.md` files. Codex can also
load `.codex-plugin/plugin.json`, but that file is an adapter, not the source of
truth. Bundled skill agent metadata uses `agents/llm.yaml` as the
platform-neutral filename; adapters may map it into their native discovery
surface, but the plugin should not depend on vendor-specific filenames.

When porting to another repo, verify the host repo provides equivalent CLI paths
before running the workflow. If a required path is missing, report the missing
contract and do not invent a substitute that weakens the PHI boundary.
