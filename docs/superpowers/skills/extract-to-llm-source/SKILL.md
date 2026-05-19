---
name: extract-to-llm-source
description: Raw .xlsx → PHI-scrubbed llm_source/ for one study. Untraceable temp removal after scrub. Cross-LLM canonical lives in AGENTS.md.
---

# extract-to-llm-source

**This is a thin shim.** The cross-LLM canonical entry point, prerequisites,
exit-code contract, scope banner, destruction-attestation policy, and the full
12-assertion verifier list all live in `AGENTS.md` (section
`## Skill: extract_to_llm_source`). Read that section before invoking.

This skill drives raw `.xlsx` datasets through HIPAA Safe Harbor PHI scrubbing
and produces a clean `llm_source/` tree for one study.
Implementation: `scripts/skills/extract_to_llm_source.py` with three
subcommands — `run`, `verify`, and `status`.

**Primary CLI invocation (`run` subcommand):**

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run --study {STUDY}
```

Replace `{STUDY}` with the study folder name under `data/raw/` (e.g. `Indo-VAP`).
