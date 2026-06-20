# RePORT-AI Study Pipeline Plugin

This is a platform-neutral LLM plugin pack. Codex can load it through the
included `.codex-plugin/plugin.json`, but the source of truth is the portable
`plugin.yaml` plus the skill files under `skills/`.

**The plugin IS the pipeline.** A single 10-phase orchestrator skill
(`skills/report-ai-study-pipeline/scripts/run.py`) drives the entire
raw-to-`llm_source` publish path: header/dictionary extraction, deduplication,
Source Truth, PHI classification + scrubbing, audit verification, the
Presidio+pyCANON PHI guard gate, promotion, staging destruction, the cleanup
verifier, and the immutable snapshot. Operators launch it with:

```bash
make study STUDY=<name>
```

`make study` exports `STUDY_NAME` and delegates to the orchestrator, which holds
the per-study pipeline lock for the whole run.

## Phases

The orchestrator drives 10 conceptual phases (see
`skills/report-ai-study-pipeline/SKILL.md` for the full table). The contiguous
publish phases 2–7 are executed by the `dataset-to-llm-source` publish
supervisor in one locked subprocess, which invokes the host publish engine
`scripts/pipeline/host_pipeline.py`. Phase 0 runs config validation, PHI
rulebook drift detection, and an input-fingerprint redundant-run check; phase 10
commits an immutable snapshot and points `current.json` at it.

## Execution Model

Duplicate handling runs in orchestrator **phase 2** via
``dataset-deduplication`` (raw-file tiers, Note 4). Legacy
``excel-duplicate-handler`` is maintainer-only and not invoked by ``make study``.
After dedup, work is organized by **raw-file set** — one canonical form/work unit containing the
associated raw dataset workbook or CSV, matching printed PDF when Source Truth is
required, manifest/privacy context, duplicate variants already resolved or held
for review, and the per-set output/audit status.

Source Truth and header extraction may run in parallel across independent
ready sets. Dataset publishing is serialized under the orchestrator's per-study
lock — do not start uncontrolled concurrent publish processes against the same
`output/<study>/` tree.

If a set is unsafe, ambiguous, or fails verification, only the affected set is
held for human review; ready sets continue. Set statuses:

- `ready`
- `held_duplicate_review`
- `held_sot_review`
- `held_publish_review`
- `complete`

A maintainer resolves held sets and re-runs with `--resume-held` (a CLI-only
maintainer loop); a clean pass commits a snapshot. The Load Study UI surfaces
held/partial sets as non-blocking notices and never triggers the retry loop.

## Skills

- `skills/report-ai-study-pipeline/SKILL.md` — the 10-phase orchestrator entrypoint.
- `skills/header-extraction/SKILL.md` — row-1 column NAMES only.
- `skills/dictionary-to-llm-source/SKILL.md` — data dictionary mapping leg.
- `skills/dataset-deduplication/SKILL.md` — raw-file dedup (orchestrator phase 2, Note 4).
- `skills/sot-lean-generator/SKILL.md` — Source Truth from PDFs + row-1 headers → joined views.
- `skills/phi-classification/SKILL.md` — deterministic jurisdiction PHI classification.
- `skills/phi-scrubbing/SKILL.md` — fail-closed per-form PHI scrub.
- `skills/dataset-to-llm-source/SKILL.md` — publish supervisor (gate → promote → snapshot).
- `skills/audit-verification/SKILL.md` — the 16-assertion verifier.
- `skills/excel-duplicate-handler/SKILL.md` — **legacy** maintainer merge helper (superseded by dataset-deduplication).
- `skills/phi-rulebook/SKILL.md` — versioned offline PHI rulebook + drift detection.
- `skills/study-setup/SKILL.md` — interactive study scaffolding (not an orchestrator phase).

Each bundled skill may include platform-neutral agent metadata at
`agents/llm.yaml`. Platform adapters, including Codex, should read or map that
file instead of relying on vendor-specific names such as `openai.yaml`.

## Porting Contract

Copy this `plugins/report-ai-study-pipeline/` directory into another compatible
repo. The host repo must provide the CLI surfaces named in `plugin.yaml`, or the
platform adapter should map those commands to local equivalents.

The host repo must preserve these execution semantics:

- the orchestrator holds one per-study lock for the whole run;
- duplicate handling runs in orchestrator phase 2 (`dataset-deduplication`) before set-level fan-out;
- Source Truth may run one set at a time or in parallel across independent sets;
- dataset publishing must use the lock-aware host publish engine;
- the data-dictionary leg still publishes
  `llm_source/dictionary_mapping/jsonl/` when raw dictionary files exist;
- held sets are reported without blocking unrelated ready sets;
- final completion requires verifier pass and commits an immutable snapshot.

Do not let an LLM read row 2+ raw dataset values directly. Raw row values may
only be processed inside the trusted extraction, scrub, cleanup, merge, and
publish code paths. Reports exposed to agents must stay limited to filenames,
headers, counts, provenance, verifier status, and other metadata.

## Codex Adapter

For Codex, the plugin manifest is:

```text
plugins/report-ai-study-pipeline/.codex-plugin/plugin.json
```

If a repo-level Codex marketplace is desired, add a local marketplace entry
that points to `./plugins/report-ai-study-pipeline`.
