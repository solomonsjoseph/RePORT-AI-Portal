---
name: dataset-to-llm-source
description: Run, verify, or audit the PHI-safe RePORT-AI dataset extraction skill that turns raw study workbooks into published llm_source dataset JSONL using scripts/skills/extract_to_llm_source.py. Use when the user asks for the dataset skill, dataset extraction, raw workbook to llm_source, PHI-safe dataset processing, one-form dataset pilots, or extract_to_llm_source operations.
---

# Dataset to LLM Source

## Core Rule

Do not read raw or staged dataset values into the agent context.

Allowed inputs for agent reasoning:

- File names and directory shape under `data/raw/{STUDY}/`.
- `_forms_manifest.yaml` and `_study_privacy.yaml`.
- Row-1 headers only when the CLI approval path reads them.
- Code, docs, tests, status files, verifier reports, ledgers, and approval artifacts that contain headers/actions only.
- Published `output/{STUDY}/llm_source/` artifacts only after the run has passed the verifier.

Real row values may be opened only inside the trusted extraction, scrub, cleanup, and publish pipeline driven by `scripts/skills/extract_to_llm_source.py`. Do not bypass the CLI by manually opening raw workbooks, staged JSONL, quarantine files, or audit payloads that may contain values.

Use `$sot-lean-generator` instead when the task is Source Truth YAML creation from printed PDFs plus dataset row-1 headers.

## What This Skill Does

The durable entry point is the cross-LLM CLI:

```bash
report-ai-pipeline
```

It drives one study through:

```text
data/raw/{STUDY}/datasets/*.{xlsx,csv}
  -> tmp/{STUDY}/datasets/*.jsonl
  -> scripts.security.phi_scrub.run_scrub
  -> dataset cleanup and cleanup propagation
  -> output/{STUDY}/llm_source/dataset_schema/files/*.jsonl
  -> verifier report and staging destruction attestation
```

Prefer this CLI over lower-level `make extract-datasets` for operator runs because it includes the manifest gate, privacy approval, pipeline lock, verifier, and destruction attestation.

## Key Boundary

Do not read, print, hash, stat, permission-check, or existence-check PHI HMAC keys or encryption keys from an agent workflow. The key is operator-managed secret material outside the repo. The only code path allowed to load it is the trusted PHI scrubber at the point where it rewrites staged values.

If a run fails because a key is missing or invalid, report the CLI failure stage and stop. Do not inspect the key file yourself.

## Preflight

1. Start at the repo root.
2. Check `git status --short` and preserve unrelated user changes.
3. Print the contract before a first run or when the scope is unclear:

```bash
report-ai-pipeline status
```

4. Confirm required inputs exist without opening dataset values:
   - `data/raw/{STUDY}/_forms_manifest.yaml`
   - `data/raw/{STUDY}/_study_privacy.yaml`
   - `data/raw/{STUDY}/datasets/`
   - `study_packs/{STUDY}/phi_scrub.yaml` (or the pack resolved via `--pack-dir` / `REPORT_AI_STUDY_PACK_DIR`)

Do not set `REPORTALIN_ALLOW_DISABLED_SCRUB`. The CLI fails closed when that variable is present.

## Run

Run all manifest-approved forms:

```bash
report-ai-pipeline run --root <DIR> \
  --study Indo-VAP
```

Run one dataset pilot:

```bash
report-ai-pipeline run --root <DIR> \
  --study Indo-VAP --form 6_HIV
```

Limit header-review parallelism when needed:

```bash
report-ai-pipeline run --root <DIR> \
  --study Indo-VAP --max-workers 2
```

The `--form` value may be a manifest-declared filename such as `6_HIV.xlsx` or a stem such as `6_HIV`. Repeat `--form` for a small explicit set.

## Verify

Always verify after a run before claiming the dataset publish is complete.

Verify the latest successful or partial-safe run:

```bash
report-ai-pipeline verify --root <DIR> \
  --study Indo-VAP
```

Verify a specific run:

```bash
report-ai-pipeline verify --root <DIR> \
  --study Indo-VAP --run RUN_ID
```

The verifier writes `output/{STUDY}/runs/{RUN_ID}/verifier_report.json` and updates `status.json` with `verifier_passed: true` on full pass.

## Exit-Code Handling

- `0`: Success. Report the run id, verifier status, published path, and attestation path.
- `2`: Manifest mismatch. Compare manifest entries to dataset filenames only; do not open dataset values.
- `3`: Ledger hash or no-LLM sentinel failed. Stop and report the failing assertion.
- `4`: Quarantine is non-empty. Stop and preserve artifacts for operator review.
- `5`: Verifier assertion failed. Use the verifier report to identify the failing invariant.
- `6`: Needs advice. Stop and report the exact pause reason.
- `7`: Destruction incomplete. Do not claim operational untraceability.
- `8`: Partial review. Approved forms may be published; held forms need human review. Report names/status only, not raw values.

Do not rerun with weaker privacy controls to force success.

## Evidence to Report

For a completed run, report these paths when present:

- `output/{STUDY}/runs/{RUN_ID}/status.json`
- `output/{STUDY}/runs/{RUN_ID}/phi_handling_approval.json`
- `output/{STUDY}/runs/{RUN_ID}/verifier_report.json`
- `output/{STUDY}/runs/{RUN_ID}/destruction_attestation.json`
- `output/{STUDY}/llm_source/dataset_schema/files/`
- `output/{STUDY}/audit/datasets/{DATASET}/phi_handling_ledger.as_written.json`
- `output/{STUDY}/audit/datasets/{DATASET}/dataset_cleanup_ledger.as_written.json`
- `output/{STUDY}/audit/dataset_cleanup_report.json`

If a file may contain dataset values, do not paste its contents into chat. Summarize pass/fail status, counts, filenames, hashes, and assertion names instead.

## Skill Maintenance

When editing this skill or the dataset CLI, keep these sources aligned:

- `scripts/skills/extract_to_llm_source.py`
- `docs/sphinx/developer_guide/extract_to_llm_source.rst`
- `docs/sphinx/developer_guide/data_extraction_datasets.rst`
- `docs/sphinx/developer_guide/architecture.rst`
- `tests/skills/test_extract_to_llm_source_cli.py`
- `tests/skills/test_extract_to_llm_source_verify.py`

Focused validation after skill-only edits:

```bash
uv run --all-groups python -m pytest \
  tests/skills/test_dataset_to_llm_source_skill.py \
  tests/skills/test_extract_to_llm_source_cli.py \
  tests/skills/test_extract_to_llm_source_verify.py -q
```
