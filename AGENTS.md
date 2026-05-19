# Project Agent Guidance

## LLM Tool Autonomy

For this project, do not add deterministic query-routing gates that force or block a specific agent tool based on keyword matching. The LLM should decide which tool to call from the available tool set.

Allowed guidance:
- Keep tool descriptions accurate and specific so the LLM can choose well.
- Keep system-prompt guidance high level and advisory.
- Keep the existing file-access boundary: agent tools may read the published `output/{STUDY}/llm_source/` and the agent workspace under `output/{STUDY}/agent/`, with writes confined to the approved agent output paths.

Avoid:
- Injecting per-query hidden routing hints.
- Hard-blocking `run_study_analysis` or any other tool for a user question category outside the tool implementation's own validation and safety checks.
- Replacing LLM judgment with brittle keyword routers.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `solomonsjoseph/RePORT-AI-Portal`. See `docs/agents/issue-tracker.md`.

### Triage labels

Agent triage uses the `agent:*` label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain-doc layout. See `docs/agents/domain.md`.

## SoT creation

SoT lean YAML files are produced by the **sot-lean-generator** 5-stage pipeline.
Stages 0, 4, and 5 are deterministic scripts. Stages 1–3 have two valid paths:
high-assurance LLM/manual authoring from the rules files, and conservative
script-backed candidate generation for runtime rebuilds. All candidates use the
same source pack and verifier before promotion.

For a full runtime rebuild, use the repo-level wrapper:

```bash
make build-llm-source STUDY=Indo-VAP
```

That command generates and verifies all PDF-backed lean YAMLs into
`output/<study>/llm_source/source_truth/`, then runs the main pipeline to publish
dictionary mappings, PHI-scrubbed dataset JSONL, audit ledgers, lineage, and the
output signpost. Use `make rebuild-llm-source STUDY=Indo-VAP` when you want to
remove generated `llm_source/` and study staging first. It preserves audit
manifests and `output/<study>/agent/`.

### Stage 0 — Source pack (run this first, any LLM tool)

```bash
# Makefile alias (recommended)
make sot-source-pack STUDY=Indo-VAP FORM=6_HIV

# Direct CLI (cross-LLM entry point)
python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV
```

Reads **only row 1** of the dataset file (headers-only PHI guarantee — row 2+
values are never read or used) and renders every annotated-PDF page at 600 DPI
via Ghostscript.

**Inputs:**
- `data/raw/<study>/annotated_pdfs/<form>.pdf`
- `data/raw/<study>/datasets/<form>.xlsx`

**Outputs:**
- `/tmp/sot_source_pack_<form>.json` — dataset header array, PDF SHA-256, and render list
- `/tmp/sot_render_<form>/<pdf-name>.page-001.png`, `.page-002.png`, ... — 600 DPI renders (visual ground truth)

### Stages 1–3 — LLM YAML authoring

High-assurance Stages 1–3 require LLM/manual reasoning. For runtime rebuilds,
`make sot-generate-all` uses the conservative candidate generator at
`skills/sot-lean-generator/scripts/generate_pdf_aware_candidate.py`; those
script-backed candidates are still only promotable after Stage 4 validation and,
when anchored gold exists, diff-against-gold.

**Any LLM/manual tool:**
1. Read `skills/sot-lean-generator/references/exhaustive_yaml_rules.md` — write the
   full exhaustive YAML draft for the form.
2. Run 5 visual sweep iterations comparing all 600 DPI page renders against the draft and
   correcting mismatches.
3. Read `skills/sot-lean-generator/references/lean_yaml_rules.md` — trim the draft
   to the lean schema and write the result to `/tmp/<form>_lean.yaml`.

### Stage 4 — Verify (any LLM tool)

```bash
make sot-verify STUDY=Indo-VAP FORM=6_HIV
# validates /tmp/6_HIV_lean.yaml by default; pass CANDIDATE=/path/to/file to override
```

Exit codes: **0** = ready for the next gate; **1** = content/validation failure;
**2** = SHA mismatch (re-run Stage 0); **3** = script gap (stop and ask the human).

### Stage 4.5 — Property validator + diff-against-gold (Phase 1 of the AFK plan)

The property validator runs automatically after Stage 4 (`check_lean_policy.py`
wires it in). It enforces 10 invariants over a loaded lean YAML: section /
skip-logic / arrow / instruction cross-reference resolution, mutex reciprocity,
and the PHI typing matrix (`pseudonymize` / `drop` / `jitter_date` allowlists).
Importable from any LLM tool via:

```python
from scripts.ai_assistant.sot_loader import load_lean_yaml, validate
report = validate(load_lean_yaml(Path("data/SoT/Indo-VAP/6_HIV_policy.lean.yaml")))
```

The diff-against-gold CLI is the seam for Phase 4's diff triage classifier:

```bash
uv run --all-groups python scripts/source_truth/diff_against_gold.py \
  --study Indo-VAP --form 6_HIV \
  --candidate /tmp/6_HIV_lean.yaml
# Exit 0 if candidate matches gold (or only cosmetic diffs).
# Exit 1 if novel diffs detected.
# Exit 2 on I/O error.
```

All three checks (verifier + property validator + diff-against-gold) run
together via the Make target:

```bash
make sot-validate STUDY=Indo-VAP FORM=6_HIV
# Requires /tmp/sot_source_pack_<form>.json — run `make sot-source-pack` first.
# Validates /tmp/<form>_lean.yaml by default; pass CANDIDATE=/path/to/file to override.
# Hard-fails on any one of the three checks.
```

Frozen gold lives at `data/SoT/<study>/<form>_policy.lean.yaml`. Each form has
a placeholder attestation at
`data/SoT/<study>/_attestations/<form>.attestation.json` with
`status: "pre-plan"` until Phase 6's anchor ceremony writes the real
e-signed attestation (Operating Rule 4: no gold changes without an anchor or
re-anchor workflow).

### Stage 5 — Promote (any LLM tool)

```bash
cp /tmp/<form>_lean.yaml output/<study>/llm_source/source_truth/<form>_policy.lean.yaml
```

**Canonical output path:** `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml`

**Canonical gold:** `data/SoT/<study>/` holds frozen calibration gold when a
form has been anchored. It is used by `sot-validate` / diff-against-gold for
those anchored forms. Runtime YAMLs under `output/<study>/llm_source/source_truth/`
are always checker-verified against the PDF/source pack before promotion; they
are not silently copied over a gold file.

### Batch Runtime SoT Generation

```bash
make sot-generate-all STUDY=Indo-VAP
```

This repo-level wrapper discovers PDF-backed forms, handles known duplicate
dataset-code exceptions, writes candidates to `/tmp`, verifies each one, and
promotes only passing YAMLs to
`output/<study>/llm_source/source_truth/`.

See: `docs/runbook_sot_build.md`

## Skill: extract_to_llm_source

Cross-LLM canonical entry point for the raw `.xlsx` → PHI-clean `llm_source/`
pipeline. Any agent (Claude Code, ChatGPT, Gemini, Cursor, …) invokes this
skill through a plain subprocess call — no Claude-specific tooling required.

Implementation: `scripts/skills/extract_to_llm_source.py`

### Prerequisites

| Item | Location / value |
|------|-----------------|
| PHI key | `~/.config/report_ai_portal/phi_key` (installed by `cli.py install`) |
| Forms manifest | `data/raw/{STUDY}/_forms_manifest.yaml` |
| `REPORTAL_RUN_ID` | Optional env var — caller-supplied run identifier; auto-generated when absent |
| `REPORTALIN_ALLOW_DISABLED_SCRUB` | **Blocked in production.** Setting this env var causes the pipeline to refuse to start. |

### CLI invocations

```bash
# End-to-end pipeline for one study
uv run --all-groups python scripts/skills/extract_to_llm_source.py run --study {STUDY}

# Post-run verifier (most-recent run by default; --run for a specific run)
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify --study {STUDY} [--run RUN_ID]

# Print skill scope banner and exit-code contract
uv run --all-groups python scripts/skills/extract_to_llm_source.py status
```

Replace `{STUDY}` with the study folder name under `data/raw/` (e.g. `Indo-VAP`).
`RUN_ID` is the opaque identifier printed by `run` or found under `output/{STUDY}/runs/`.

### Exit codes

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | `EXIT_OK` | ok |
| 2 | `EXIT_MANIFEST_MISMATCH` | manifest mismatch (missing required / unknown / reject) |
| 3 | `EXIT_LEDGER_HASH_NULL` | audit ledger hash null or sentinel missing |
| 4 | `EXIT_QUARANTINE_NON_EMPTY` | quarantine directory non-empty |
| 5 | `EXIT_VERIFIER_FAIL` | verifier assertion failed |
| 6 | `EXIT_NEEDS_ADVICE` | needs-advice (paused — operator inspection required) |
| 7 | `EXIT_DESTRUCTION_INCOMPLETE` | destruction incomplete |

Code 1 is reserved for unexpected exceptions (unhandled Python error).

### Scope banner (`status` subcommand output)

```
Pipeline: raw .xlsx → PHI-scrubbed llm_source/ (one study)

PHI coverage: HIPAA Safe Harbor identifiers per scripts/security/phi_scrub.yaml
              + project-specific patterns in scripts/security/phi_patterns.py
Out of scope (operator responsibility): DPDPA §16 cross-border egress,
                                        §12 right-to-erase, §8(6) breach
                                        notification, ICMR l-diversity gate.

Temp removal: operational untraceability after successful publish (APFS COW
              acknowledged in destruction attestation; not forensic erasure).
```

### Destruction attestation

Written to `output/{STUDY}/runs/{run_id}/destruction_attestation.json` **only
after a successful publish**. On any pre-publish failure, staging is preserved
for inspection and exit code 6 (`EXIT_NEEDS_ADVICE`) is surfaced.

Required fields:

```json
{
  "run_id": "<opaque id>",
  "study": "<STUDY>",
  "started_utc": "<ISO-8601>",
  "completed_utc": "<ISO-8601>",
  "staging_path": "<absolute path to destroyed staging dir>",
  "removed_paths": ["<relative paths of destroyed files>"],
  "files_destroyed": 42,
  "cryptographic_erasure": false,
  "apfs_cow_disclaimer": "Filesystem-level overwrite was performed via secrets.token_bytes + fsync; APFS copy-on-write means prior blocks may persist until trimmed. Skill scope is operational untraceability, not forensic erasure."
}
```

`cryptographic_erasure` is always `false` — APFS COW means forensic erasure is
out of scope (see the `apfs_cow_disclaimer` field).

### Verifier assertions

`verify` runs **12 ordered assertions**; the first failure stops the chain.
For the full assertion list see `scripts/skills/extract_to_llm_source.py:_cmd_verify`.

- Failure writes a report to `output/{STUDY}/runs/{run_id}/verifier_report.json`
  regardless of pass/fail.
- On full pass, `output/{STUDY}/runs/{run_id}/status.json` is updated with
  `verifier_passed: true`.
