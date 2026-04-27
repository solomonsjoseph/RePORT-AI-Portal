# Agent Instructions — RePORT AI Portal

Privacy-first, local-first AI Assistant system for clinical research data. The PHI scrubber (Step 1.6) is an **honest-broker catalog** with eight action classes — keep / birthdate / drop / cap / generalize / suppress_small_cell / date / id — evaluated in strict priority order against ~200 Indo-VAP-calibrated rules; see `scripts/security/phi_scrub.py` and `scripts/security/phi_scrub.yaml`. The HMAC key lives at `~/.config/report_ai_portal/phi_key` (outside this repo, never read by agent code).

**Four-tier architecture**: RED `data/raw/` → AMBER `tmp/{STUDY}/` secure staging (mode 0700, umask 0077, zero-fill teardown, optional tmpfs via `REPORTALIN_TMPFS_STAGING=1`) → GREEN `output/{STUDY}/trio_bundle/` (PHI-free artifacts) + `output/{STUDY}/agent/` (the agent's own state) — these two zones form the LLM's read surface, enforced by `scripts.ai_assistant.file_access.validate_agent_read` → GREEN-PROTECT agent boundary (`phi_gate_check` + `kanon_check` on every tool return). See `docs/irb_dossier/` for the 31-criterion IRB benchmark (plus four follow-ups added in patches 2026-04-23a/b) and `memory/project_phi_architecture_locked.md` for the architecture provenance.

## Quick Reference

```bash
make sync          # Install all deps (uv sync --all-groups)
make test          # ~703 deterministic tests (excludes AI Assistant/LLM tests)
make test-all      # Full suite including AI Assistant tests (requires langchain)
make lint          # ruff check + format
make ci            # lint → typecheck → test
make chat          # Launch Streamlit web UI
make chat-cli      # Launch CLI REPL
make pipeline      # Full data pipeline (dict → datasets + pdfs → variables.json)
```

## Architecture

Two-world system — see [architecture.rst](docs/sphinx/developer_guide/architecture.rst) for details.

**World 1 — Deterministic Pipeline** (`main.py` → `scripts/extraction/`, `scripts/security/`, `scripts/utils/`):
Three extraction legs (dictionary, datasets, PDFs) write into a transient staging workspace at `tmp/{STUDY_NAME}/` (prepared via `scripts/utils/secure_staging.prepare_staging` with mode 0700 under umask 0077; optionally redirected to `/dev/shm/{STUDY}/` when `REPORTALIN_TMPFS_STAGING=1`). Every extracted row gets a full `_provenance` dict (raw_sha256, pipeline_version, extraction_engine, source_file, sheet_name, row_index, study_name, extraction_utc). **`phi_scrub.run_scrub` (Step 1.6)** scrubs staged datasets in place via eight action classes in strict priority order — keep → birthdate → drop → cap → generalize → suppress_small_cell → date-jitter → id-pseudonymize — BEFORE any audit is written so no raw PHI lands in `output/`. `dataset_cleanup` (Step 1.7) runs against staged datasets and emits `audit/dataset_cleanup_report.json`. `cleanup_propagation.run_propagation` (Step 1.8) reads the dataset audit, computes the pruning set, and rewrites staged dictionary + PDF artifacts, emitting `audit/dictionary_cleanup_report.json` and `audit/pdfs_cleanup_report.json`. `_publish_staging` atomically renames staging → `trio_bundle/` (per-leg, copytree fallback across filesystems). `build_variables_reference` runs after publish. **Step 4** emits `audit/lineage_manifest.json` pairing every raw input (SHA-256) with every published trio artifact (SHA-256) + per-leg audit references + compliance posture — the single IRB-review artifact. On success, staging is **securely removed** (`secure_remove_tree`: overwrite-with-random + fsync + unlink); on failure, `tmp/{STUDY_NAME}/` is preserved for operator inspection.

**PDF extraction PHI-safety:** external-API PDF extraction (Anthropic / Google Gemini) is refused unless the operator sets `REPORTALIN_PDF_PHI_FREE=1` attesting the source PDFs are PHI-free (blank CRFs / protocol / MOP). Pre-extracted JSON via `--pdf-source` bypasses the external API entirely.

**World 2 — AI Assistant** (`scripts/ai_assistant/`):
LangGraph ReAct agent with 12 tools for querying study data. Never accesses raw data.

**Output structure**: `output/{STUDY_NAME}/trio_bundle/{datasets,pdfs,dictionary,variables.json}`, `audit/{dataset,dictionary,pdfs}_cleanup_report.json` + `audit/phi_scrub_report.json` + `audit/lineage_manifest.json` + `audit/telemetry/events.jsonl`, `agent/{analysis,conversations,restore_points}/`; transient staging sibling: `tmp/{STUDY_NAME}/{datasets,dictionary,pdfs}/` (+ `quarantine/` for rows with missing subject_id).

**Two snapshot tiers** (PR #18 split):

1. **Tracked baseline** at `snapshots/{STUDY_NAME}/{datasets,dictionary,pdfs,variables.json}` — version-controlled, maintainer-curated, single per-study cleaned trio bundle. The pipeline's PDF orchestrator reads it as the per-PDF fallback when the LLM tier is unavailable. **LLM is forbidden from reading it** — the LLM read zone is `trio_bundle/` + `agent/` only. Maintainer protocol: see `snapshots/README.md`.
2. **Operator restore points** at `output/{STUDY_NAME}/agent/restore_points/<name>/` — gitignored, multi-named, agent-writable. Used for crash recovery during dev; never read by the pipeline. Library `scripts.utils.snapshots.{create_snapshot, list_snapshots, restore_snapshot, resolve_snapshot_name, SnapshotError}`, CLI `python -m scripts.utils.snapshots {create,list,restore}`, Makefile `make snapshot [SNAPSHOT=<label>] [FORCE=1]` / `make list-snapshots` / `make restore-study SNAPSHOT=<name>`.

**Wizard step 2 flow** (PR #18 rewrite): two top-level buttons — *Use Existing Study* (skip pipeline; trust the live `trio_bundle/`) and *Load Study* (run the pipeline subprocess; orchestrator falls back to the tracked snapshot baseline per-PDF when the LLM tier is unavailable).

**PHI key**: sidecar at `~/.config/report_ai_portal/phi_key` (resolved via `config.PHI_KEY_PATH`, overridable with `XDG_CONFIG_HOME`). Mode must be `0600`. Missing = hard-fail. Bootstrap via `python -m scripts.security.phi_scrub bootstrap-key`. Key rotation = full re-ingestion.

## Tech Stack

- **Python 3.11+**, **uv** package manager (required)
- **Ruff** linter (line-length=100, see `pyproject.toml [tool.ruff]`)
- **MyPy** type checker (`ignore_missing_imports=true`)
- **Pytest** (`tests/`, `@pytest.mark.slow` for heavy tests)
- **LangChain/LangGraph** for AI Assistant agent, **Streamlit ≥1.38, <2.0** for web UI (pin in `pyproject.toml`)
- Custom type stubs in `typings/` for google, anthropic

## Critical Conventions

### Security Zones (MUST follow)

- **Never access `data/raw/`** from agent code — only `output/{STUDY}/trio_bundle/`
- Always call `validate_agent_read(path)` or `validate_agent_write(path)` from `scripts.ai_assistant.file_access` before any file I/O in tools. This is the unified chokepoint — it accepts only `trio_bundle/` + `agent/` paths and rejects audit, telemetry, staging, raw, and arbitrary filesystem paths with `ZoneViolationError`.
- Route every free-text tool return through `scripts.ai_assistant.phi_safe.guard_text` or wrap the tool with `@phi_safe_return`
- When surfacing row-level data, call `scripts.ai_assistant.phi_safe.guard_rows_with_kanon(rows, quasi_identifiers=..., k=5)` first — it suppresses responses when any quasi-identifier equivalence class has fewer than k members (ICMR §11.7 k-anonymity, defence-in-depth against the offline scrub missing a quasi-id combination)
- When writing pipeline code that logs subject data, install the PHI log redactor: `scripts.utils.log_hygiene.install_phi_redactor(hmac_key=...)` so raw `SUBJID` / dates / emails / Aadhaar / phone / PIN never land in `.logs/*.log`

### Conversational-shortcut guard on fuzzy search tools (added 2026-04-24)

- Greetings / acknowledgements / queries shorter than 3 chars are short-circuited **inside** `search_variables`, `find_variable_candidates`, `search_pdf_context` via `_query_looks_conversational` in `scripts/ai_assistant/agent_tools.py`. The tool returns a user-facing refusal (`_CONVERSATIONAL_REFUSAL_MESSAGE`) instead of surfacing noisy fuzzy-substring hits.
- Paired with a CONVERSATIONAL WORLD section at the top of `scripts/ai_assistant/agent_prompts.py` that tells the LLM to answer greetings / small-talk without any tool call.
- This is UX hygiene, **not** a security control. `phi_safe.guard_user_prompt` still runs on every prompt at UI + CLI entry; this guard operates inside the tool so a retry-happy agent that tries to call it anyway gets a clean refusal rather than a name-variable paraphrase.
- When adding a new fuzzy search tool, call `_query_looks_conversational(query)` and return `_CONVERSATIONAL_REFUSAL_MESSAGE` on `True`. Covered by `tests/test_agent_conversational_guard.py`.

### Prompt-injection + at-rest defences (added 2026-04-23, patches a + b)

- **Input-side gate.** Every researcher prompt must pass `scripts.ai_assistant.phi_safe.guard_user_prompt(text)` before the LLM is invoked. Already wired at `scripts/ai_assistant/ui/chat.py` + `scripts/ai_assistant/cli.py`; new entry points must mirror this.
- **Untrusted text must be wrapped.** Any text surfaced from outside the agent's control (PDF extracts, dictionary free-text strings, external vocab) must pass through `scripts.ai_assistant.phi_safe.sanitise_untrusted_snippet(text, source_label=...)` before it reaches the LLM. Already applied inside `search_pdf_context` in `scripts/ai_assistant/agent_tools.py`.
- **At-rest redaction.** Any surface that persists or exports user-generated content (conversation JSONs, text / markdown exports, any future telemetry sink that stores free text) must run content through `scripts.ai_assistant.phi_safe.redact_phi_in_text(text)`. Already wired at `_save_conversation`, `_export_conversation_as_text`, `_export_conversation_as_md` in `scripts/ai_assistant/ui/conversations.py`.
- **Traceback surfaces.** Any code path that surfaces a traceback to the LLM or to the user (tool error returns, UI error expanders, telemetry error payloads) must sanitise with `scripts.ai_assistant.phi_safe.sanitise_traceback(tb)` — strips long literal previews (pandas row fragments), truncates to the last 12 lines, and redacts PHI shapes. Already wired at `agent_tools.py::run_study_analysis` error branch and `ui/streaming.py` error expander.
- **Refused-prompt placeholder.** When `guard_user_prompt` refuses, the persisted conversation must store a category-tagged placeholder (e.g. `"[PHI-REFUSED prompt — AADHAAR]"`), **not** the raw prompt. Findings list is kept in `messages_meta` for audit.
- **Telemetry non-string payloads.** `scripts/utils/telemetry.py::on_custom_event` force-stringifies + masks any value that is not a primitive (`int`/`float`/`bool`/`None`) before writing to the JSONL sink.
- Adding a new agent tool means: `@tool` → `@phi_safe_return` → open with `validate_agent_read(...)` (or `validate_agent_write(...)`). Any deviation fails `tests/test_agent_tools_phi_safe.py` + `tests/test_file_access.py`.

### Config

All paths and settings come from `config.py` (env vars + YAML overlay from `config/config.yaml`). Never hardcode paths — use `config.TRIO_BUNDLE_DIR`, `config.TMP_DIR`, etc.

Key flags: `STUDY_NAME`, `LOG_LEVEL`, `LOG_VERBOSE` (see `.env.example`)

### Imports

- Use `from __future__ import annotations` in all modules
- Lazy-import optional deps (streamlit, langchain) inside functions
- First-party packages: `scripts`, `config`

### Agent Tools

Tools live in `scripts/ai_assistant/agent_tools.py` as `@tool`-decorated functions. The docstring becomes the agent-visible description. All tools are collected in `ALL_TOOLS` list. Use `tool_cache` for memoization.

### Web UI

- `scripts/ai_assistant/web_ui.py` is the main Streamlit app
- UI modules split into `scripts/ai_assistant/ui/` (streaming, conversations, providers, session)
- Sidebar JS in `scripts/ai_assistant/ui/assets/bridge.js` — uses `document` (not `window.parent.document`)
- Use `st.html()` for injecting JS/CSS (not deprecated `st.components.v1.html`)

### Tests

- Fixtures in `tests/conftest.py` — use `tmp_path` + `monkeypatch_config` to isolate
- Synthetic data helpers: `_fake_records(n)`, `synthetic_excel()`
- Tests requiring LLM/langchain are excluded from `make test` (included in `make test-all`)
- Zone markers are patched via `monkeypatch` in fixtures

## Key Files

| Area | Files |
|------|-------|
| Entry point | `main.py`, `config.py` |
| Pipeline | `scripts/extraction/dataset_pipeline.py`, `scripts/extraction/build_variables_reference.py`, `scripts/extraction/extract_pdf_data.py` |
| PHI scrub + catalog | `scripts/security/phi_scrub.py`, `scripts/security/phi_scrub.yaml` |
| PHI gate + k-anon + allowlist | `scripts/security/phi_gate.py`, `scripts/security/kanon_gate.py`, `scripts/security/phi_allowlist.py`, `scripts/security/phi_patterns.py`, `scripts/security/phi_ner.py` (design stub for future work) |
| Phase-0 staging hardening | `scripts/utils/secure_staging.py` |
| Integrity + governance | `scripts/utils/lineage.py`, `scripts/utils/log_hygiene.py` |
| Zone guards | `scripts/security/secure_env.py` |
| AI Assistant agent | `scripts/ai_assistant/agent_graph.py`, `scripts/ai_assistant/agent_tools.py`, `scripts/ai_assistant/agent_prompts.py`, `scripts/ai_assistant/phi_safe.py` (hosts `phi_safe_return`, `guard_text`, `guard_rows_with_kanon`, `guard_user_prompt`, `sanitise_untrusted_snippet`, `redact_phi_in_text`, `sanitise_traceback`) |
| Telemetry | `scripts/utils/telemetry.py` (agent event logger, attached as LangChain callback) |
| Web UI | `scripts/ai_assistant/web_ui.py`, `scripts/ai_assistant/ui/` |
| Config | `config.py`, `config/config.yaml`, `config/study_knowledge.yaml` |
| IRB benchmark dossier | `docs/irb_dossier/conformance_matrix.md`, `docs/irb_dossier/executive_summary.md`, `docs/irb_dossier/phi_walkthrough.md` (technical + non-technical walk-through, patch log) |

## Web UI Architecture

The Streamlit web UI implements Claude Desktop's dark design language. It is production-ready with a setup wizard, conversation history, model switching, and interactive analysis charts.

### UI edit-safe files

Only these paths may be touched by UI work:

- `scripts/ai_assistant/web_ui.py`
- `scripts/ai_assistant/ui/{chat,conversations,model_policy,providers,shell,state,streaming,wizard}.py`
- `scripts/ai_assistant/ui/assets/{theme.css, bridge.js, fonts/}`
- `.streamlit/config.toml`
- `pyproject.toml` (kaleido pin only: `kaleido>=1.0.0,<2.0`)

### UI edit-forbidden files (hard stop)

- `config.py`
- `scripts/ai_assistant/agent_graph.py` (read-only; use the three entry points only: `stream_query`, `invoke_query`, `reset_agent`)
- `scripts/ai_assistant/agent_tools.py`, `agent_prompts.py`, `analytical_engine.py`, `study_knowledge.py`, `file_access.py`, `tool_cache.py`, `phi_safe.py`, `cli.py`
- Everything under `scripts/extraction/`, `scripts/security/`, `scripts/utils/`

### Design token system

All design tokens in `scripts/ai_assistant/ui/assets/theme.css` use the `--rpln-*` namespace (canonical primary `:root` block). New CSS must use these — never the deprecated backward-compat scales.

| Category | Tokens | Raw values that stay raw |
|---|---|---|
| Colors | `--rpln-bg`, `--rpln-text`, `--rpln-accent`, `--rpln-text-muted`, `--rpln-hairline`, `--rpln-good`, `--rpln-bad` | — |
| Spacing | `--rpln-space-{0-5…10}` (2 px–48 px) | 7 px, 42 px (Streamlit-tuned) |
| Type | `--rpln-text-{2xs…5xl}` (10 px–44 px) | 13 px, 15 px, 17 px (emotion-cache tuning) |
| Line-height | `--rpln-leading-{tight/snug/body/loose}` | — |
| Radius | `--rpln-radius-{xs/sm/md/lg/pill/bubble}` | — |
| Z-index | `--rpln-z-{base/raise/topbar/overlay/menu/popover}` | 2, 3, 16, 24, 40–42 (ambiguous layers) |
| Easing | `--rpln-ease-out` | — |
| Durations | `--rpln-dur-{fast/base/slow}` (150/200/280 ms) | 120 ms, 160 ms (Streamlit overrides) |

**Deprecated (do not use in new CSS):** `--fs-*`, `--sp-*`, `--r-*`, `--dur-*` — annotated `@deprecated` in the backward-compat `:root` block with pointers to the `--rpln-*` equivalents. Values differ (`--dur-fast: 120ms` vs `--rpln-dur-fast: 150ms`).

**`--rpln-accent-orange` is a compat alias** — use `--rpln-accent` instead; the color-named token is wrong for 7 of 8 themes.

### Regression gate (run before every UI commit)

```
uv run pytest tests/ -x -q
```

Any red test in a non-UI module = hard stop. Revert the wave, do not patch the test.

## Documentation

- **Full docs**: [docs/sphinx/](docs/sphinx/) — build with `make docs`
- **Testing**: [testing.rst](docs/sphinx/developer_guide/testing.rst)
- **Contributing**: [contributing.rst](docs/sphinx/developer_guide/contributing.rst)
- **Data pipeline**: [data_pipeline.rst](docs/sphinx/user_guide/data_pipeline.rst)
