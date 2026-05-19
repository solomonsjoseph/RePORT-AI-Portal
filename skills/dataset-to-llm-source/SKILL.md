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
uv run --all-groups python scripts/skills/extract_to_llm_source.py
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

Prefer this CLI over lower-level `make extract-datasets` for operator runs because it includes the manifest gate, privacy approval, PHI key preflight, pipeline lock, verifier, and destruction attestation.

## Preflight

1. Start at the repo root.
2. Check `git status --short` and preserve unrelated user changes.
3. Print the contract before a first run or when the scope is unclear:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py status
```

4. Confirm required inputs exist without opening dataset values:
   - `data/raw/{STUDY}/_forms_manifest.yaml`
   - `data/raw/{STUDY}/_study_privacy.yaml`
   - `data/raw/{STUDY}/datasets/`
   - `scripts/security/phi_scrub.yaml`
   - `~/.config/report_ai_portal/phi_key` (check existence only; never print it)

Do not set `REPORTALIN_ALLOW_DISABLED_SCRUB`. The CLI fails closed when that variable is present.

## Run

Run all manifest-approved forms:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
  --study Indo-VAP
```

Run one dataset pilot:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
  --study Indo-VAP --form 6_HIV
```

Limit header-review parallelism when needed:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
  --study Indo-VAP --max-workers 2
```

The `--form` value may be a manifest-declared filename such as `6_HIV.xlsx` or a stem such as `6_HIV`. Repeat `--form` for a small explicit set.

## Verify

Always verify after a run before claiming the dataset publish is complete.

Verify the latest successful or partial-safe run:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
  --study Indo-VAP
```

Verify a specific run:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
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
- `output/{STUDY}/audit/phi_handling_ledger.as_written.json`
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
