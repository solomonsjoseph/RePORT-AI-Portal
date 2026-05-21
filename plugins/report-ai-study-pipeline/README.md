# RePORT-AI Study Pipeline Plugin

This is a platform-neutral LLM plugin pack. Codex can load it through the
included `.codex-plugin/plugin.json`, but the source of truth is the portable
`plugin.yaml` plus the skill files under `skills/`.

## Workflow Order

Always run the workflow in this order for a full study preparation pass:

1. `excel-duplicate-handler`
2. `sot-lean-generator`
3. `dataset-to-llm-source`

The order matters because duplicate dataset files and duplicate headers can
change Source Truth binding, and Source Truth context should be settled before
publishing final PHI-safe `llm_source` dataset artifacts.

## Execution Model

Duplicate handling is a **single study-level preflight execution**. It runs
before any Source Truth or dataset publishing work. If it finds unsafe or
ambiguous duplicates, only the affected raw-file sets are held for human
review; ready sets may continue.

After duplicate preflight, work is organized by raw-file set.

A raw-file set is one canonical form/work unit after duplicate preflight. It
contains the associated raw dataset workbook or CSV, matching printed PDF when
Source Truth is required, manifest/privacy context, duplicate variants already
resolved or held for review, and the per-set output/audit status.

The plugin supports two execution modes:

- **Single-set mode:** run one raw-file set through SoT and dataset publishing.
- **Batch-parallel mode:** run duplicate preflight once, then fan out ready
  raw-file sets for SoT and controlled dataset publishing.

Parallelism is allowed across independent raw-file sets for Source Truth work.
Dataset publishing may be parallel only through the host repo's lock-aware
publish path, or through a wrapper that queues/serializes jobs when shared study
output locks are held. Do not start uncontrolled concurrent publish processes
against the same `output/<study>/` tree.

Set statuses:

- `ready`
- `held_duplicate_review`
- `held_sot_review`
- `held_publish_review`
- `complete`

## Skills

- `skills/report-ai-study-pipeline/SKILL.md` is the orchestrator entrypoint.
- `skills/excel-duplicate-handler/SKILL.md` handles duplicate workbook and schema issues.
- `skills/sot-lean-generator/SKILL.md` builds or audits Source Truth policy YAML from PDFs plus row-1 headers.
- `skills/dataset-to-llm-source/SKILL.md` publishes verified PHI-safe dataset JSONL.

Each bundled skill may include platform-neutral agent metadata at
`agents/llm.yaml`. Platform adapters, including Codex, should read or map that
file instead of relying on vendor-specific names such as `openai.yaml`.

## Porting Contract

Copy this `plugins/report-ai-study-pipeline/` directory into another compatible
repo. The host repo must provide the CLI surfaces named in `plugin.yaml`, or the
platform adapter should map those commands to local equivalents.

The host repo must preserve these execution semantics:

- duplicate handling runs once per study before set-level fan-out;
- Source Truth may run one set at a time or in parallel across independent sets;
- dataset publishing must use a lock-aware or serialized write path;
- held sets are reported without blocking unrelated ready sets;
- final completion requires verifier pass for the affected published outputs.

Do not let an LLM read row 2+ raw dataset values directly. Raw values may only
be processed inside the trusted extraction, scrub, cleanup, merge, and publish
code paths. Reports exposed to agents must stay limited to filenames, headers,
counts, provenance, verifier status, and other metadata.

## Codex Adapter

For Codex, the plugin manifest is:

```text
plugins/report-ai-study-pipeline/.codex-plugin/plugin.json
```

If a repo-level Codex marketplace is desired, add a local marketplace entry
that points to `./plugins/report-ai-study-pipeline`.
