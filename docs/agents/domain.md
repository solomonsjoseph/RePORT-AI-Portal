# Domain Docs

This repo uses a single-context domain-doc layout.

## Primary Domain Sources

Read these before architecture, diagnosis, TDD, triage, or PRD work when relevant:

- `CONTEXT.md` at the repo root, if present
- `docs/adr/`, if present
- `docs/sphinx/developer_guide/decisions.rst`
- Relevant files under `docs/sphinx/developer_guide/`
- Relevant files under `docs/sphinx/irb_auditor/`

If `CONTEXT.md` or `docs/adr/` do not exist, proceed silently. They are created lazily when domain terms or architectural decisions are resolved.

## Domain Vocabulary

Use the project vocabulary from `CONTEXT.md` when it exists. If a new concept is not represented there, do not invent competing terms silently. Clarify the term through `grill-with-docs` before encoding it into durable docs or issue titles.

## ADR Conflicts

If a proposed change contradicts an existing ADR or `docs/sphinx/developer_guide/decisions.rst`, surface the conflict explicitly before implementation.

## Policy Pilot Note

Tmp-only policy pilot outputs under `tmp/results/` are working artifacts, not durable project docs, unless explicitly promoted.
