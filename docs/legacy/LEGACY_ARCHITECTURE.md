# Legacy Architecture Reference
> This file documents the OLD pipeline design, superseded on 2026-06-17.
> Do not follow these patterns in new code.

<!--
Doc-boundary note: this historical implementation plan is no longer current documentation.
This is a point-in-time legacy archive, not parallel user documentation; durable
user/auditor/operator/contributor documentation lives in docs/sphinx. The pointer
phrase on the line above is a verbatim POINTER_PHRASES entry from
scripts/lint_doc_freshness.py (`_check_markdown_boundary()`), kept on one line so
`make doc-freshness` passes.
-->

This is a historical record only. The pipeline was re-architected across Waves 0–6
(commits `cb7f6fa` → `ec8ae19`) per `docs/plans/pipeline_redesign_implementation_plan.md`
so that **the plugin IS the pipeline**: a 10-phase orchestrator skill
(`plugins/report-ai-study-pipeline/skills/report-ai-study-pipeline/scripts/run.py`)
governs the pipeline skills, `main.py` is now a thin AI-assistant launcher, and
study config moved out of the raw-data tree. Everything below describes the state
*before* that change.

## What was replaced and why

- `main.py --pipeline` → plugin orchestrator (reason: plugin consolidation — "the plugin IS the pipeline")
- `extract_to_llm_source.py` monolith → orchestrator + skills (reason: single responsibility)
- `SUSPECTED_DUPLICATE_PAIRS` → dynamic dedup (reason: hardcoded lists don't scale)
- `excel-duplicate-handler` → `dataset-deduplication` (reason: new tiered approach)
- `data/raw/{STUDY}/` config files → `config/{STUDY}/` (reason: config/data separation)
- Hash snapshot IDs → timestamp IDs (reason: human readability)

### Notes on the two duplicate-handling rows (verified against the current repo)

These two rows reflect the design intent recorded in the spec (Note 21). The
*in-DAG* duplicate handling was indeed replaced, but two artifacts named above
are not fully gone today, so they are annotated here rather than misrepresented:

- **`excel-duplicate-handler` was fully retired (2026-06-23).** Orchestrator
  phase 2 **`dataset-deduplication`** (`raw_file_dedup.py`) replaced it in the
  active publish path (Note 18). Its one surviving asset — the lossless
  workbook-merge engine `merge_excel_duplicates.py` — was folded into
  `dataset-deduplication/scripts/` as a maintainer-only resolution arm (never
  auto-invoked); the skill directory, SKILL.md, `agents/llm.yaml`, and the
  `plugin.yaml` `legacy_preflight` registration were removed.
- **`SUSPECTED_DUPLICATE_PAIRS`, `JUNK_PATTERNS`, and the `clean_trio_datasets`
  JSONL-level dedup/junk passes were removed** (Note 18 remediation). Raw-file
  dedup (`raw_file_dedup.py`, orchestrator phase 2) plus the manifest `reject:`
  gate handle duplicate/junk FILES before extraction, so the post-extraction
  `dataset_cleanup.py` is now an audit-envelope writer only (it emits the dataset
  audit + `as_written` cleanup ledgers from extraction column-drop events). The
  `UnscrubbedDatasetError` class is retained for backwards-compatible imports but
  is no longer raised.

## Old module paths (now removed or relocated)

The host publish engine moved verbatim out of `main.py` into
`scripts/pipeline/host_pipeline.py` (Wave 6 thin-`main.py` cutover). The
raw → `llm_source/` pipeline modules physically moved from `scripts/` into
`plugins/report-ai-study-pipeline/skills/<skill>/scripts/` (Wave 2, commit
`6fd2be6`) but remain importable under their canonical `scripts.*` names via the
`sys.meta_path` finder (`_MovedModuleFinder`) installed in `scripts/__init__.py`.
So "old path" below is the pre-redesign canonical name; "new path" is the actual
on-disk location today.

| OLD path | NEW path / status |
|---|---|
| `main.py` — host publish engine (`run_pipeline`/`run_step`/`_run_dict_leg`/`_run_dataset_leg`/`_publish_leg`/`_prune_empty_staged_forms`/`main --pipeline`) | `scripts/pipeline/host_pipeline.py` (`run_pipeline`, `run_step`, …). `main.py` is now a thin `--chat`/`--web`/`--version` launcher. |
| `python main.py --pipeline` (publish invocation) | `python -m scripts.pipeline.host_pipeline --pipeline`, invoked by the `dataset-to-llm-source` publish supervisor under the orchestrator lock |
| `main.py:_install_log_redactor_best_effort` | `scripts/utils/log_hygiene.py:install_phi_redactor_best_effort` |
| `scripts/security/phi_scrub.py` | `plugins/report-ai-study-pipeline/skills/phi-scrubbing/scripts/phi_scrub.py` (still importable as `scripts.security.phi_scrub` via the shim) |
| `scripts/security/phi_gate.py` | `plugins/report-ai-study-pipeline/skills/phi-scrubbing/scripts/phi_gate.py` (importable as `scripts.security.phi_gate`) |
| `scripts/security/phi_review.py` | `plugins/report-ai-study-pipeline/skills/phi-classification/scripts/phi_review.py` (importable as `scripts.security.phi_review`) |
| `scripts/extraction/dataset_pipeline.py` | `plugins/report-ai-study-pipeline/skills/dataset-to-llm-source/scripts/dataset_pipeline.py` (importable as `scripts.extraction.dataset_pipeline`) |
| `scripts/extraction/dataset_cleanup.py` | `plugins/report-ai-study-pipeline/skills/dataset-to-llm-source/scripts/dataset_cleanup.py` (importable as `scripts.extraction.dataset_cleanup`) |
| `scripts/extraction/cleanup_propagation.py` | `plugins/report-ai-study-pipeline/skills/dataset-to-llm-source/scripts/cleanup_propagation.py` (importable as `scripts.extraction.cleanup_propagation`) |
| `scripts/skills/extract_to_llm_source.py` | `plugins/report-ai-study-pipeline/skills/dataset-to-llm-source/scripts/extract_to_llm_source.py` (importable as `scripts.skills.extract_to_llm_source`) |
| `scripts/extraction/load_dictionary.py` | `plugins/report-ai-study-pipeline/skills/dictionary-to-llm-source/scripts/load_dictionary.py` (importable as `scripts.extraction.load_dictionary`) |
| `scripts/source_truth/study_intake.py` | `plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/study_intake.py` (importable as `scripts.source_truth.study_intake`) |
| `scripts/source_truth/generate_lean_outputs.py` | `plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/generate_lean_outputs.py` (importable as `scripts.source_truth.generate_lean_outputs`) |
| `data/raw/{STUDY}/datasets/_forms_manifest.yaml` | `config/{STUDY}/_forms_manifest.yaml` (Wave 1, commit `c31758a`) |
| `data/raw/{STUDY}/_study_privacy.yaml` | `config/{STUDY}/_study_privacy.yaml` (Wave 1, commit `c31758a`) |
| `scripts/security/phi_scrub.yaml` — single global scrub config | `config/_defaults/phi_scrub.yaml` + optional `config/{STUDY}/phi_scrub.yaml` override (deep-merged by `load_scrub_config`) (Wave 1, commit `925d5fe`) |
| Snapshot ids `snap_<sha256[:32]>` (content hash as the directory name) | `snap_<YYYYMMDDTHHMMSSZ>` UTC timestamp labels (Wave 5, commit `ec8ae19`); the content hash is retained as the `content_hash` manifest field, no longer the id |

## Old Makefile targets (removed)

The following per-stage publish targets were removed (they existed at commit
`6fd2be6`; absent in the current `Makefile`). Their single replacement is
`make study STUDY=<name>`, which delegates to the 10-phase orchestrator.

- `make pipeline`
- `make build-llm-source`
- `make dictionary`
- `make extract-datasets`
- `make bundle`

Replacement: **`make study STUDY=<name>`** (the plugin orchestrator is the pipeline).
