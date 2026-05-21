---
name: report-ai-study-pipeline
description: Use when a user wants the full RePORT-AI study preparation workflow as one portable LLM plugin: duplicate dataset handling first, Source Truth policy generation second, and PHI-safe dataset-to-llm-source publishing last.
---

# RePORT-AI Study Pipeline

## Core Rule

This is the platform-neutral orchestrator for the bundled RePORT-AI skills.
For a full study or form workflow, always preserve this phase order:

1. `$excel-duplicate-handler`
2. `$sot-lean-generator`
3. `$dataset-to-llm-source`

Do not read raw dataset row values into the agent context. Dataset row 2+
values may only be handled inside trusted repo code paths that scrub, merge,
clean, audit, or publish data and expose metadata-only reports.

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

## When To Use Only One Child Skill

If the user asks only about duplicate files, use `$excel-duplicate-handler`.
If the user asks only about PDF/header Source Truth policy YAML, use
`$sot-lean-generator`.
If the user asks only to run or verify PHI-safe dataset publishing, use
`$dataset-to-llm-source`.

Use this orchestrator when the request spans more than one phase, when the
phase is unclear, or when the user asks for the repo/plugin workflow.

## Full Workflow

### Phase 1 - Duplicate Handling

Use `$excel-duplicate-handler` before Source Truth or dataset publishing.
This phase runs once for the study, not once per set and not in parallel.
Resolve or route these issues first:

- duplicate dataset files;
- Excel lock/temp files;
- same ordered headers;
- header supersets/subsets;
- partial overlaps;
- duplicate row-1 headers;
- duplicate columns or records that require trusted pipeline handling.

If a duplicate case is unsafe or ambiguous, write or preserve the human-review
audit output and mark only the affected raw-file sets `held_duplicate_review`.
Continue only with sets classified `ready`.

### Phase 2 - Source Truth

Use `$sot-lean-generator` after duplicate/header issues are settled.
Source Truth may use:

- printed PDFs and page renders for clinical meaning;
- dataset row-1 headers for binding only;
- no dataset row 2+ values.

The printed PDF is the clinical authority. The dataset is binding/inventory
evidence only.

For a single-set request, run Source Truth only for that exact set. For a batch
request, Source Truth may run in parallel across independent ready sets. Use
exact form ids when more than one dataset shares a leading form number. If a
set has a missing or ambiguous PDF/dataset pair, mark that set
`held_sot_review` and continue with unrelated ready sets.

### Phase 3 - Dataset To LLM Source

Use `$dataset-to-llm-source` after duplicate handling and Source Truth are
settled. Run the repo CLI and verifier instead of lower-level extraction
shortcuts:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py status
uv run --all-groups python scripts/skills/extract_to_llm_source.py run --study <STUDY>
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify --study <STUDY>
```

For one-form pilots, pass `--form <FORM>`.

For batch work, dataset publishing may run one set at a time or through a
controlled parallel wrapper that respects the host repo's pipeline locks. Never
force concurrent writes into the same study output tree. If a publish job cannot
proceed because the lock is held or verifier output fails, mark that set
`held_publish_review` and report the exact verifier or lock stage.

## Execution Modes

### Single-Set Mode

1. Run or confirm the study-level duplicate preflight.
2. Run Source Truth for the requested set.
3. Run dataset publishing for the requested set through the trusted CLI.
4. Verify the published output before reporting `complete`.

### Batch-Parallel Mode

1. Run duplicate preflight once for the study.
2. Discover ready raw-file sets and held review sets.
3. Fan out Source Truth for ready sets where the PDF/dataset pair is exact.
4. Run dataset publishing through lock-aware or serialized publish jobs.
5. Aggregate per-set statuses and verifier results.

## Portability

This plugin is not Codex-only. Any LLM platform can use it by reading
`plugin.yaml`, this orchestrator, and the child `SKILL.md` files.

Codex can also load `.codex-plugin/plugin.json`, but that file is an adapter,
not the source of truth for the workflow.

Bundled skill agent metadata uses `agents/llm.yaml` as the platform-neutral
filename. Platform adapters may map that file into their native discovery
surface, but the plugin should not depend on vendor-specific filenames.

When porting to another repo, verify the host repo provides equivalent CLI
paths before running the workflow. If a required path is missing, report the
missing contract and do not invent a substitute that weakens the PHI boundary.
