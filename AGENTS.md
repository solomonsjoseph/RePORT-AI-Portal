# Project Agent Guidance

## LLM Tool Autonomy

For this project, do not add deterministic query-routing gates that force or block a specific agent tool based on keyword matching. The LLM should decide which tool to call from the available tool set.

Allowed guidance:
- Keep tool descriptions accurate and specific so the LLM can choose well.
- Keep system-prompt guidance high level and advisory.
- Keep the existing file-access boundary: agent tools may read the published `output/{STUDY}/trio_bundle/` and the agent workspace under `output/{STUDY}/agent/`, with writes confined to the approved agent output paths.

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
