# Real-Data Smoke Runbook — `verify_and_promote` Gate End-to-End

This runbook is the **deployment-time verification** for the
`make build-llm-source` pipeline, terminating in the
`scripts/source_truth/verify_and_promote.py` reconciliation gate. The
dev environment cannot run this end-to-end because the PHI scrub config
and the raw study workbooks are absent there; the first real run on a
machine that has both is the smoke test.

Target study throughout: `Indo-VAP`.

## 1. Prerequisites

Confirm each of these before running step 1.

- **PHI HMAC key is bootstrapped.** The key path is `config.PHI_KEY_PATH`
  (defaults under the user-config dir). Bootstrap with:

  ```bash
  uv run --all-groups python -m scripts.security.phi_scrub bootstrap-key
  ```

  See `scripts/security/phi_scrub.py::_cli_bootstrap_key`. Key file
  must exist and be mode `0600` — the scrubber hard-fails otherwise.

- **PHI scrub config YAML is present** at `config.PHI_SCRUB_CONFIG_PATH`
  (default: `scripts/security/phi_scrub.yaml`). The scrubber refuses
  to publish without it.

- **Raw study data is laid out** under `data/raw/Indo-VAP/` — workbooks
  and dictionaries as the extraction stages expect.

- **`data/SoT/Indo-VAP/` exists** with the 28 per-form policy YAMLs.
  Without `SoT/Indo-VAP/`, both `make build-llm-source` and `make verify-and-
  promote` short-circuit with a yellow `>> SKIP` message.

## 2. Steps

Run in order. Each step is independent enough to inspect its output
before continuing.

### Step 1 — Dictionary extraction

```bash
make dictionary STUDY=Indo-VAP
```

**Success:** `output/Indo-VAP/llm_source/dictionary_mapping/jsonl/*.json`
exists, one JSON per dictionary tab.

### Step 2 — Dataset extraction (pre-PHI)

```bash
make extract-datasets STUDY=Indo-VAP
```

**Success:** `tmp/Indo-VAP/datasets/*.jsonl` exists, one JSONL per
form. These are the raw, un-scrubbed extractions.

### Step 3 — PHI scrub

```bash
uv run --all-groups python -m scripts.security.phi_scrub run \
  --study Indo-VAP
```

**Success:**

- `tmp/Indo-VAP/datasets/*.jsonl` mutated in place (PHI scrubbed).
- `output/Indo-VAP/audit/phi_scrub_report.json` written.
- Sentinel `tmp/Indo-VAP/.phi_scrub_complete` written. Subsequent runs
  short-circuit until the sentinel is removed.

### Step 4 — Dataset cleanup (dedup + capture-metadata drops)

Triggered as part of the cleanup pass attached to extraction; if not
already run by step 2's pipeline target, run it explicitly:

```bash
uv run --all-groups python -m scripts.extraction.dataset_cleanup \
  --study Indo-VAP
```

**Success:** `output/Indo-VAP/audit/dataset_cleanup_report.json`
written. Each cleanup event carries `variable_id`, `form`, and
`reason`.

### Step 5 — Build LLM-source artifacts and run reconciliation gate

```bash
make build-llm-source STUDY=Indo-VAP
```

This emits all of:

- `output/Indo-VAP/llm_source/study_metadata/catalog.json`
- `output/Indo-VAP/llm_source/study_metadata/evidence_packs/*.json`
- `output/Indo-VAP/llm_source/concept/concept_index.json`
- `output/Indo-VAP/audit/phi_handling_ledger.declared.json`
- `output/Indo-VAP/audit/dataset_cleanup_ledger.declared.json`

…and then chains into `make verify-and-promote STUDY=Indo-VAP` as the
final stage. That stage is the reconciliation gate.

## 3. Expected outcomes

The gate has two terminal states.

### 3.a — Clean reconciliation

- All 28 forms reconcile cleanly: every SoT-declared column is either
  present in the scrubbed dataset or its absence is explained by the
  PHI ledger or the cleanup ledger.
- `output/Indo-VAP/llm_source/dataset_schema.json` is **promoted** to
  GREEN.
- `output/Indo-VAP/human_review/` directory does **not** exist.
- `make build-llm-source` exits `0`.

### 3.b — Discrepancies surfaced

- One or more forms have unexplained drops (or unexplained extras).
- Per-form discrepancy files written:
  `output/Indo-VAP/human_review/<form>_discrepancies.json`. Each file
  contains:

  | key | meaning |
  |---|---|
  | `missing_unexplained` | SoT-declared columns absent from scrubbed data with no PHI/cleanup ledger entry |
  | `extra_in_scrubbed` | columns present in scrubbed data but not in SoT |
  | `explained_by_phi` | drops correctly accounted for by `phi_scrub_report.json` |
  | `explained_by_cleanup` | drops correctly accounted for by `dataset_cleanup_report.json` |

- `dataset_schema.json` is **NOT** promoted; the previous GREEN copy
  (if any) remains authoritative.
- `make build-llm-source` exits **non-zero**.

## 4. What to do on each outcome

### Clean

Artifact set is ready for downstream consumers (chat agent retrieval,
UI). Done — promote the build through whatever release process the
deployment uses.

### Discrepancies

Investigate each form's discrepancy file in turn. For every entry
under `missing_unexplained`, decide which of these is the cause:

- **Scrub bug.** The scrub config dropped a column that wasn't
  intended to be PHI. Fix `scripts/security/phi_scrub.yaml`, delete
  the sentinel (see §6 Rollback), rerun from step 3.
- **SoT gap.** The form's policy YAML at
  `data/SoT/Indo-VAP/<form>_policy.yaml` declares a column that no
  longer exists in the workbook (or is misnamed). Update the YAML and
  rerun from step 5.
- **Cleanup miss.** The dataset cleanup pass dropped the column for a
  legitimate reason but did not record it in the cleanup ledger
  (orphan event). Fix the cleanup logic / mapping (see §5) and rerun
  from step 4.

Iterate until reconciliation is clean.

## 5. Known assumption gaps

The following are gaps the runbook explicitly acknowledges; the first
real run is the only authoritative test.

- **`dataset_cleanup_report.json` envelope was reverse-engineered**
  from `_serialize_audit` in `scripts/extraction/dataset_cleanup.py`.
  The synthetic test fixtures may disagree with the real shape on
  details (field names, nesting). If `verify_and_promote.py` blows up
  parsing the real cleanup report, that is the bug to fix first.
- **`source_to_form` mapping** (xlsx filename → form id) is built
  from each policy YAML's `source.dataset_file`. Forms with a missing
  or mistyped `source.dataset_file` will produce **orphan cleanup
  events** that the gate now logs as warnings (see commit `b9040b7`)
  rather than blowing up. Watch the warning output: a flood of
  orphans usually means a SoT typo in one form's `source.dataset_file`.

## 6. Rollback

A failed run leaves `staging/`, `tmp/`, and the previous GREEN
artifacts intact. Specifically:

- `output/Indo-VAP/llm_source/dataset_schema.json` (if previously
  promoted) is unchanged. Previously published artifacts remain
  authoritative.
- `tmp/Indo-VAP/datasets/*.jsonl` retains the scrubbed-in-place state
  from step 3.
- The PHI scrub sentinel `tmp/Indo-VAP/.phi_scrub_complete` is still
  present. Re-running step 3 short-circuits because of it.

To force a fresh PHI scrub run (e.g., after fixing
`phi_scrub.yaml`):

```bash
rm tmp/Indo-VAP/.phi_scrub_complete
```

…then resume from step 3.

To discard a partial build entirely and retry from clean state:

```bash
rm -rf output/Indo-VAP/staging
rm -rf output/Indo-VAP/human_review
rm tmp/Indo-VAP/.phi_scrub_complete
```

(Do not delete `output/Indo-VAP/llm_source/` or `output/Indo-VAP/audit/`
— those hold the last-known-good GREEN state.)
