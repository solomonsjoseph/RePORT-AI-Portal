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
Stages 0, 4, and 5 are deterministic scripts; Stages 1–3 require LLM reasoning.
All LLM tools share the same rules files and the same verifier — only the
orchestration shell differs.

For a full runtime rebuild, use the repo-level wrapper:

```bash
make build-llm-source STUDY=Indo-VAP
```

That command generates and verifies all PDF-backed lean YAMLs into
`output/<study>/llm_source/source_truth/`, then runs the main pipeline to publish
dictionary mappings, PHI-scrubbed dataset JSONL, audit ledgers, lineage, and the
output signpost. Use `make rebuild-llm-source STUDY=Indo-VAP` when you want to
remove generated `llm_source/`, `audit/`, and study staging first. It preserves
`output/<study>/agent/`.

### Stage 0 — Source pack (run this first, any LLM tool)

```bash
# Makefile alias (recommended)
make sot-source-pack STUDY=Indo-VAP FORM=6_HIV

# Direct CLI (cross-LLM entry point)
python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV
```

Reads **only row 1** of the dataset file (headers-only PHI guarantee — row 2+
bytes never enter Python) and renders the annotated PDF at 600 DPI via Ghostscript.

**Inputs:**
- `data/raw/<study>/annotated_pdfs/<form>.pdf`
- `data/raw/<study>/datasets/<form>.xlsx`

**Outputs:**
- `/tmp/sot_source_pack_<form>.json` — dataset header array + PDF SHA-256
- `/tmp/sot_render_<form>/<form>.pdf.png` — 600 DPI render (visual ground truth)

### Stages 1–3 — LLM YAML authoring

These stages cannot be automated by a deterministic script.

**Claude Code users** — invoke `skills/sot-lean-generator/SKILL.md` (Stages 1–3
are orchestrated by the Claude Code skill runner).

**Other LLM tools (ChatGPT, Gemini, Cursor, etc.):**
1. Read `skills/sot-lean-generator/references/exhaustive_yaml_rules.md` — write the
   full exhaustive YAML draft for the form.
2. Run 5 visual sweep iterations comparing the 600 DPI render against the draft and
   correcting mismatches.
3. Read `skills/sot-lean-generator/references/lean_yaml_rules.md` — trim the draft
   to the lean schema and write the result to `/tmp/<form>_lean.yaml`.

### Stage 4 — Verify (any LLM tool)

```bash
make sot-verify STUDY=Indo-VAP FORM=6_HIV
```

Exit codes: **0** = ready to promote; **2** = SHA mismatch (re-run Stage 0);
**3** = script gap (stop and ask the human).

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
