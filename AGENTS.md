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

The `study_intake` CLI replaces the former 32-module `scripts/source_truth/` pipeline
with a single deterministic command. It pairs annotated PDFs with xlsx/csv datasets,
reads only row 1 of each dataset (headers-only PHI guarantee — row-2-or-later bytes
never enter Python), and emits exactly two output kinds: one `{form}_policy.yaml` per
cleanly-aligned pair, and a single `SoT_intake_review.md` checklist for every file that
could not be automatically paired or passed the header-safety checks.

```bash
# Build SoT YAMLs for a study (skip already-built YAMLs by default)
python -m scripts.source_truth.study_intake <study>

# Force-overwrite existing YAMLs (use with care — overwrites human-curated files)
python -m scripts.source_truth.study_intake <study> --force
```

**Inputs:**
- `data/raw/<study>/annotated_pdfs/*.pdf` — annotated PDF forms (assumed pre-annotated)
- `data/raw/<study>/datasets/*.xlsx` and `*.csv` — study datasets

**Outputs:**
- `data/SoT/<study>/<form>_policy.yaml` — one per cleanly-aligned PDF + dataset pair
- `data/SoT/<study>/human_review/SoT_intake_review.md` — checklist for everything else

See: `docs/runbook_sot_build.md`
