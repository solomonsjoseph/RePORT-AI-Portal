---
name: phi-rulebook
description: Resolve and inspect the jurisdiction PHI rulebook (HIPAA Safe Harbor + India DPDPA) with a versioned offline cache, a committed airgapped seed, and drift detection. Use when the user asks about which PHI rules are active, the rulebook version/hash, rule drift, or wants to refresh/inspect jurisdiction classification rules. Metadata only — never reads study data.
---

# PHI Rulebook

## Core Rule

This skill operates on **rule metadata only** — jurisdiction rule ids, actions,
reasons, official-source URLs, and SHA-256 hashes. It never reads any study
dataset value, row, or header content.

## What This Skill Does

Resolves the active PHI jurisdiction rulebook for a study and records its
provenance, so an IRB reviewer can confirm exactly which rule set produced a
publish, and an operator is warned when the rules change.

The rulebook engine lives in the host repo at
`scripts/security/phi_rulebook.py`; it wraps the pinned jurisdiction rules in
`scripts/security/phi_review.py` with three guarantees:

- **Versioned offline cache** — each resolved bundle's provenance is persisted
  to `output/{STUDY}/audit/phi_rulebook/rulebook_v{N}_{JURIS}.json` (audit zone,
  no LLM access).
- **Committed airgapped seed** — `config/_defaults/phi_rulebook/` holds a v1 seed
  per supported jurisdiction set so the first run in a network-isolated
  environment still has a known-good baseline. The classification rules
  themselves are pinned in code, so the engine always works offline.
- **Drift detection** — the freshly built `rules_sha256` is compared to the
  cached/seed baseline; a mismatch is surfaced so a rule-set change (code update
  or live-source update) is never silent.

## CLI

```bash
# Resolve the active rulebook for a study (offline / pinned by default).
uv run --all-groups python -m \
  plugins.report-ai-study-pipeline.skills.phi-rulebook.scripts.rulebook_cli \
  resolve --study Indo-VAP

# Inspect a committed seed rulebook for a jurisdiction set.
uv run --all-groups python -m \
  plugins.report-ai-study-pipeline.skills.phi-rulebook.scripts.rulebook_cli \
  show --jurisdictions INDIA,USA
```

Exit codes: `0` resolved (no drift) · `3` resolved but DRIFT detected (confirm
the rule-set change) · `2` usage/config error.

## Portability

The engine is platform-neutral host-repo code; this skill is the thin operator
command surface. Any LLM host reads this `SKILL.md`; `agents/llm.yaml` carries
the short-form adapter metadata.
