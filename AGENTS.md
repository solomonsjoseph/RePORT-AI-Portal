# Project Agent Bootstrap

This file is machine bootstrap for AI coding assistants. Durable user,
auditor, operator, and contributor documentation lives in Sphinx under
`docs/sphinx/`. The root `README.md` is the public entry point.

Start with:

- Sphinx docs: `docs/sphinx/index.rst`
- Agent briefing: `docs/sphinx/developer_guide/agents.rst`
- Source Truth build: `docs/sphinx/developer_guide/source_truth_build.rst`
- `extract_to_llm_source` skill: `docs/sphinx/developer_guide/extract_to_llm_source.rst`
- Documentation rules: `docs/sphinx/developer_guide/documentation_style.rst`

Do not add standalone Markdown runbooks or parallel documentation trees.
Promote durable instructions into Sphinx and keep this file short.

## LLM Tool Autonomy

For this project, do not add deterministic query-routing gates that force
or block a specific agent tool based on keyword matching. The LLM should
decide which tool to call from the available tool set.

Allowed guidance:

- Keep tool descriptions accurate and specific so the LLM can choose well.
- Keep system-prompt guidance high level and advisory.
- Keep the existing file-access boundary: agent tools may read the
  published `output/{STUDY}/llm_source/` and the agent workspace under
  `output/{STUDY}/agent/`, with writes confined to approved agent output
  paths.

Avoid:

- Injecting per-query hidden routing hints.
- Hard-blocking `run_study_analysis` or any other tool for a user
  question category outside the tool implementation's own validation and
  safety checks.
- Replacing LLM judgment with brittle keyword routers.

## Required Boundaries

- Raw source files under `data/raw/{STUDY}/` are presumed PHI-bearing.
- Staging under `tmp/{STUDY}/` may contain PHI during a run.
- Assistant-readable files are limited to
  `output/{STUDY}/llm_source/` and `output/{STUDY}/agent/` through
  `scripts.ai_assistant.file_access`.
- Audit, raw, staging, temporary, and held-form details are not
  assistant-readable.
- Preserve the row-1-header-only boundary for Source Truth and PHI
  approval flows until real-data extraction starts inside trusted scripts.

## Canonical Commands

Build the complete LLM source bundle:

```bash
make build-llm-source STUDY=Indo-VAP
```

Generate a single Source Truth source pack:

```bash
make sot-source-pack STUDY=Indo-VAP FORM=6_HIV
```

Run and verify the audited extraction skill:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run --study Indo-VAP
uv run --all-groups python scripts/skills/extract_to_llm_source.py verify --study Indo-VAP
```

Run one manifest-declared dataset:

```bash
uv run --all-groups python scripts/skills/extract_to_llm_source.py run --study Indo-VAP --form 6_HIV
```

Run documentation gates:

```bash
make docs-quality
```
