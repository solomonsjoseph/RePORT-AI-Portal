# Trio Bundle Snapshots — Version-Controlled Baselines

This directory holds **cleaned and verified trio bundle snapshots** committed to
version control. Each subdirectory under `snapshots/` is a per-study baseline
with the same layout as the live `output/{STUDY}/trio_bundle/`:

```
snapshots/
└── {STUDY_NAME}/             # e.g. snapshots/Indo-VAP/ — must match config.STUDY_NAME exactly
    ├── datasets/             # *.jsonl scrubbed datasets (PHI already removed)
    ├── dictionary/           # *.json data dictionary
    ├── pdfs/                 # *_variables.json PDF extractions
    └── variables.json        # consolidated variables reference
```

## Purpose

Snapshots are the **deterministic fallback baseline** for the pipeline:

1. **PDF orchestrator fallback.** When the wizard's "Load Study" runs and the
   PDF orchestrator's LLM tier is unavailable for a particular PDF (no API
   key, image-only PDF, capability gate fails, LLM call errors), the
   orchestrator reads `snapshots/{STUDY}/pdfs/{stem}_variables.json` instead
   of publishing a code-only heuristic guess.

2. **Network-isolated runs.** Operators on hardened hosts without LLM access
   can run `python main.py --pipeline` and the pipeline will populate
   `trio_bundle/pdfs/` from these snapshots so the agent has something to
   answer questions against.

## Read posture

- **The LLM agent must NOT read this directory.** The agent's read zone is
  restricted to `output/{STUDY}/trio_bundle/` and `output/{STUDY}/agent/` only
  (see `scripts/security/secure_env.py`). Putting snapshots outside both zones
  is intentional — a stale snapshot must never be served as live data.
- **The wizard's "Load Study" subprocess is the only legitimate reader.** The
  pipeline's PDF orchestrator imports `config.STUDY_SNAPSHOTS_DIR` and uses it
  as the snapshot lookup root.

## Maintenance

- **Snapshots are PHI-scrubbed.** Only files that have been through the full
  `phi_scrub` + `kanon_gate` chain belong here. Adding raw subject IDs or
  unscrubbed dates to a snapshot would defeat the entire purpose.
- **Update by promoting from a verified production run.** A maintainer
  copies `output/{STUDY}/trio_bundle/` → `snapshots/{STUDY}/` after manual
  review, commits, and references the lineage_manifest.json hash in the
  commit message for audit trail.
- **Do not generate snapshots from `--force` runs without manual review.**
  The whole value of a snapshot is the human verification step.

## .gitignore

The repo's `.gitignore` explicitly tracks `snapshots/`. Files under this
directory ARE committed.
