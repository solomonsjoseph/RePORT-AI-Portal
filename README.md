# RePORT AI Portal

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/status-beta-blue.svg)](https://github.com/solomonsjoseph/RePORT-AI-Portal)
[![Documentation](https://img.shields.io/badge/docs-sphinx-blue.svg)](https://solomonsjoseph.github.io/RePORT-AI-Portal/)
[![Privacy-Aware](https://img.shields.io/badge/Privacy-Aware-blue.svg)](https://www.hhs.gov/hipaa/index.html)
[![Multi-Regulation Support](https://img.shields.io/badge/Regulations-IN%20%2F%20US-green.svg)](https://gdpr.eu/)

A single-study, privacy-first, local-first AI Assistant for clinical-research data.
The pipeline processes one already-existing study (currently Indo-VAP), runs every
row through an 8-action PHI scrubber, and publishes a PHI-free *trio bundle*
plus an agent-state zone (`output/{STUDY}/agent/`) for conversations and
snapshots — the two zones the downstream LLM agent is ever allowed to read.

> 📚 **Full documentation**: <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

## Table of Contents

- [Overview](#overview)
- [Data Flow](#data-flow)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Makefile Commands](#makefile-commands)
- [Usage Examples](#usage-examples)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Version Management](#version-management)
- [Troubleshooting](#troubleshooting)
- [Requirements](#requirements)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Overview

**What.** RePORT AI Portal turns a single locked clinical study on disk into a
read-only, PHI-scrubbed dataset that an AI Assistant can answer questions
against — without the LLM (or the humans running it) ever seeing raw PHI.

**Why.** Data managers take months to export variables for epidemiologists.
The Portal short-circuits that queue for an already-consented, already-locked
study by giving the researcher a chat interface over a published *trio bundle*,
while keeping every byte of PHI behind an irrevocable honest-broker boundary
that an IRB / IEC can audit line by line.

**How.** A four-tier honest-broker architecture:

- **RED — `data/raw/{STUDY}/`** — read-only to the extraction leg; an
  `assert_not_raw` zone guard refuses any other caller.
- **AMBER — `tmp/{STUDY}/`** — transient staging, mode 0700 + umask 0077 +
  zero-fill teardown, optional tmpfs via `REPORTALIN_TMPFS_STAGING=1`. Never
  readable by the LLM agent.
- **GREEN — `output/{STUDY}/trio_bundle/`** — PHI-free by construction; one
  of the two zones the LLM agent reads (the other is `output/{STUDY}/agent/`,
  the agent's own state). Every tool resolves paths through
  `scripts.ai_assistant.file_access.validate_agent_read`; pipeline-side
  early-reject uses `assert_trio_bundle_zone`.
- **GREEN-PROTECT — agent boundary** — every `@tool` return flows through a
  regex PHI gate (with a clinical-phrase allowlist) plus a k-anonymity gate
  (k ≥ 5) before it leaves the agent.

Between AMBER and GREEN, the PHI scrubber evaluates ~200 Indo-VAP-calibrated
rules in strict priority order through eight action classes —
**keep → birthdate → drop → cap → generalize → suppress_small_cell →
date_jitter → id_pseudonymize** — and emits a counts-only audit report plus a
lineage manifest pairing every raw-file SHA-256 with every published artifact
SHA-256. That manifest is the single IRB-review evidence file.

For the active IRB conformance inventory, with one passing test cited
per claim, see `docs/irb_dossier/conformance_matrix.md`.

## Data Flow

```text
raw study files
  data/raw/{STUDY}/
      ├── datasets/            # Excel exports (PHI-bearing)
      ├── annotated_pdfs/      # Blank-form annotations (PHI-free)
      └── data_dictionary/     # Variable definitions

        ↓ extraction legs write to AMBER staging, not directly to GREEN

transient staging workspace  (removed on success; kept for inspection on failure)
  tmp/{STUDY}/
      ├── datasets/            # Dataset extraction output
      ├── dictionary/          # Dictionary extraction output
      └── pdfs/                # PDF extraction output

        ↓ PHI scrub (8 actions, ~200 rules) → dataset cleanup → cross-leg pruning
        ↓ atomic publish: staging → trio_bundle/
        ↓ lineage manifest: raw SHA-256 → trio SHA-256

processed artifacts
  output/{STUDY}/
      ├── trio_bundle/         # GREEN — PHI-scrubbed artifacts; LLM read zone (1/2)
      │   ├── datasets/        # PHI-scrubbed deduplicated JSONL
      │   ├── pdfs/            # Structured PDF form extractions
      │   ├── dictionary/      # Data dictionary mappings
      │   └── variables.json   # Unified variables reference (v3, 23 fields)
      ├── audit/               # IRB / maintainer evidence (counts only, no values)
      │   ├── lineage_manifest.json
      │   ├── phi_scrub_report.json
      │   ├── dataset_cleanup_report.json
      │   ├── telemetry/       # Agent events — off-limits to the LLM surface
      │   └── …
      └── agent/               # Session state; LLM read zone (2/2), write zone (analysis/ only)
          ├── analysis/        # Deterministic epidemiology outputs
          ├── conversations/   # Chat transcripts
          └── snapshots/       # Restore-ready copies of the trio bundle

        ↓ agent tools call trio_bundle/ directly — no vector index, no chunking

  AI Assistant (--chat / --web)
      ReAct agent → 12 structured tools → GREEN-PROTECT gate → answer
```

## Key Features

### 🔒 Security & Privacy — Four-Tier Honest-Broker

- **Zone guards** — `scripts/security/secure_env.py` enforces every boundary at
  call sites; see `docs/sphinx/developer_guide/phi_architecture.rst`.
- **8-action PHI scrub** — `scripts/security/phi_scrub.py` +
  `scripts/security/phi_scrub.yaml`. Priority-ordered actions: keep allowlist
  (clinical lab / medication / time-of-day) → birthdate handling → drop
  (names / Indian government IDs / staff identifiers / narrative fields /
  geography / contact / financial) → cap (age ≥ 90 → "90+") → generalize
  (marital status, facility type) → suppress small cells → SANT date jitter
  (±30 days, per-subject constant offset) → HMAC-SHA256 ID pseudonymize.
- **Sidecar HMAC key** at `~/.config/report_ai_portal/phi_key` (mode 0600, 32
  random bytes) — outside the repo tree, never read by the agent. Bootstrap
  with `python -m scripts.security.phi_scrub bootstrap-key`.
- **Agent-boundary gates** — `scripts/security/phi_gate.py` (regex + clinical
  allowlist) and `scripts/security/kanon_gate.py` (k-anonymity ≥ 5 on
  quasi-identifier equivalence classes) wrap every `@tool` return via
  `scripts/ai_assistant/phi_safe.py`.
- **Lineage manifest + integrity chain** — every extracted row's
  `_provenance` carries `raw_sha256` + `pipeline_version` +
  `extraction_engine`. `output/{STUDY}/audit/lineage_manifest.json` pairs
  input SHA-256 with every published trio artifact SHA-256.
- **Log PHI hygiene** — `scripts/utils/log_hygiene.py` redacts subject-ID
  substrings (per-subject HMAC tag) + Aadhaar / PAN / phone / email / pincode
  / SSN / dates from every log record before the handler emits.
- **PDF PHI-safety gate** — external-API PDF extraction (Anthropic / Gemini)
  is refused unless the operator sets `REPORTALIN_PDF_PHI_FREE=1` **and**
  commits an attestation note at
  `docs/irb_dossier/authorities/phi_free_pdfs.md` (template lives alongside).
- **Counts-only audit** — every audit JSON under `output/{STUDY}/audit/` is
  shape + counts. No raw values anywhere.

### 🤖 AI Assistant

- **ReAct agent** — LangChain `create_agent` with 12 structured-data
  tools.
- **Direct data access** — no chunking, no embedding index; tools query the
  trio bundle directly.
- **Privacy-aware prompts** — disclosure rules baked into the system prompt.
- **Sandboxed code execution** — pandas / scipy / statsmodels / matplotlib in
  a restricted `exec` environment.
- **Deterministic analytical engine** — univariate logistic regression,
  backward-stepwise multivariate selection, and interaction analysis wired
  through `run_study_analysis` with publication-quality plots. Rare-event
  cohorts render descriptive tables/plots and explicitly refuse invalid
  logistic inference.
- **Grounded natural-language answers** — the system prompt requires the
  assistant to resolve variables before analysis, distinguish computed facts
  from interpretation, and surface caveats such as sparse events,
  low-confidence CRF matches, missing variables, or k-anonymity suppression.
- **Multi-provider LLM** — OpenAI, Anthropic, Google Gemini, Ollama,
  and NVIDIA AI Endpoints via LangChain provider integrations.
- **Dual interface** — CLI (`--chat`) and Streamlit web UI (`--web`).
- **Telemetry** — append-only event logging with conservative field masking.

### 📊 Data Processing

- **Multi-table detection** from complex Excel layouts.
- **JSONL output** for efficient streaming.
- **Deduplication** and intelligent column handling.
- **Cross-leg pruning** — dataset cleanup propagates to dictionary + PDF
  artifacts so the trio bundle stays consistent.
- **Type conversion** with validation and error handling.

### 🔧 Robust Configuration

- **Type-safe paths** — full `pathlib` enforcement, zero string-joined paths
  in runtime code.
- **Cross-platform** — macOS, Linux, Windows.
- **Deterministic builds** — `uv` lockfile + `__version__.py` as single source
  of truth.

> 📖 **Learn more**: <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager

### Installation

```bash
# 1. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone and navigate
git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
cd RePORT-AI-Portal

# 3. Bootstrap the PHI HMAC key (once per machine)
uv run python -m scripts.security.phi_scrub bootstrap-key

# 4. (Optional) Set a cloud LLM API key — the default model is qwen3:8b via Ollama
# (no API key needed for local Ollama). Set a key only if using a cloud provider:
export ANTHROPIC_API_KEY="sk-ant-..."   # for claude-* models
# export OPENAI_API_KEY="sk-..."        # for gpt-* models
# export GOOGLE_API_KEY="..."           # for gemini-* models

# 5. Quick start — sync deps → run full pipeline
make quickstart
```

### Example Output

```text
Processing Indo-vap dataset...
✓ Loaded 43 Excel files
✓ Extracted 1,854,110 text fields
✓ Lineage manifest: 43 raw → 43 trio SHA-256 pairs
✓ Time: ~8 seconds
```

## Makefile Commands

```text
Quickstart
  make quickstart          Sync deps → full pipeline
  make debug               Same as quickstart with DEBUG logging

Environment
  make sync                Install / restore all dependencies (uv sync)
  make version             Show version + environment info

Pipeline — full
  make pipeline            Dict → Datasets → Cleanup → PDF → Bundle

Pipeline — individual steps
  make dictionary          Load data dictionary
  make extract-datasets    Extract and promote datasets (+ cleanup)
  make bundle              Build trio bundle
  make pdf-extract         Standalone PDF extraction
  make build-variables     Build variables.json from all annotation sources

AI Assistant
  make chat                Launch Streamlit web UI
  make chat-cli            Start interactive AI Assistant chat (terminal)

Restore Points — output/{STUDY}/agent/restore_points/ (gitignored)
  make snapshot            Copy output/{STUDY}/trio_bundle/ → agent/restore_points/<ts>/
                           SNAPSHOT=<name> for an explicit label; FORCE=1 to overwrite
  make list-snapshots      List available restore points (newest first)
  make restore-study       Restore a point back into trio_bundle/ (SNAPSHOT=<name>)
  Note: tracked-baseline snapshots/{STUDY}/ is curated by hand — see docs/sphinx/developer_guide/operations.rst

Quality
  make test                Run pytest suite
  make lint                Ruff check + format
  make typecheck           Run mypy
  make security            Run pip-audit
  make ci                  lint → typecheck → test
  make verify              Local readiness checks

Docs
  make docs                Build Sphinx HTML docs

Maintenance
  make clean               Remove caches, sessions, stale logs
  make nuke                Remove everything (venv, output, indexes)

Modifiers
  VERBOSE=1 make <target>  Enable DEBUG logging
  FORCE=1   make <target>  Force re-run (ignore cache)
```

> **LLM provider** is configured via `config/config.yaml` (`llm.provider`) and
> the corresponding `*_API_KEY` environment variable.

## Usage Examples

### Pipeline Execution

```bash
# Full pipeline (dictionary → datasets → cleanup → PDF → bundle)
make pipeline

# Or run directly via Python:
uv run python main.py --pipeline

# Skip specific steps
uv run python main.py --skip-dictionary
uv run python main.py --skip-datasets

# Process datasets only (extract → promote → cleanup)
uv run python main.py --process-datasets

# Verbose mode (DEBUG level with file:line in logs)
uv run python main.py --verbose
uv run python main.py -v --pipeline

# Get enhanced help
uv run python main.py --help
```

### AI Assistant / Chat

```bash
# Streamlit web UI (preferred — includes setup wizard)
make chat

# Interactive CLI REPL
make chat-cli

# Or run directly via Python:
uv run python main.py --web    # web UI
uv run python main.py --chat   # CLI

# Chat with verbose logging
uv run python main.py --chat --verbose
```

### Logging & Debugging

The `--verbose` flag provides detailed debugging context with file and line
numbers:

```bash
# Standard logging (INFO level)
uv run python main.py
# Log format: 2026-04-23 19:17:11 - report_ai_portal - INFO - Processing data

# Verbose logging (DEBUG level with context)
uv run python main.py --verbose
# Log format: 2026-04-23 19:17:11 - report_ai_portal - DEBUG - [main.py:123] - Processing data
#                                                          ↑ Shows source location
```

**Log file location**: `.logs/report_ai_portal_TIMESTAMP.log`. Every log record
passes through `scripts/utils/log_hygiene.py` before emission — PHI never
reaches disk.

## Project Structure

```text
RePORT AI Portal/
├── main.py                          # Pipeline entry point + staging lifecycle
├── config.py                        # Centralized configuration + zone paths
├── __version__.py                   # Single source of truth for version
├── config/
│   ├── config.yaml                  # Runtime LLM and app settings
│   └── study_knowledge.yaml         # Ground-truth variable mappings
├── pyproject.toml                   # Python dependencies (managed by uv)
├── Makefile                         # Command centre
├── scripts/                         # Core processing modules
│   ├── __init__.py
│   ├── artifact_versions.py         # Artifact version tracking
│   ├── extraction/                  # Extraction legs
│   │   ├── __init__.py
│   │   ├── build_variables_reference.py
│   │   ├── cleanup_propagation.py
│   │   ├── dataset_cleanup.py
│   │   ├── dataset_pipeline.py
│   │   ├── dedup.py
│   │   ├── extract_pdf_data.py
│   │   ├── load_dictionary.py
│   │   └── io/
│   │       ├── clinical_dates.py
│   │       ├── file_discovery.py
│   │       ├── file_io.py
│   │       └── jsonl_reader.py
│   ├── ai_assistant/                # AI Assistant system
│   │   ├── __init__.py
│   │   ├── agent_graph.py           # ReAct agent
│   │   ├── agent_prompts.py         # System + node prompts
│   │   ├── agent_tools.py           # 12 zone-guarded tools
│   │   ├── analytical_engine.py     # Deterministic epidemiology
│   │   ├── cli.py                   # Interactive REPL
│   │   ├── file_access.py           # Unified zone-validator chokepoint
│   │   ├── phi_safe.py              # @phi_safe_return decorator
│   │   ├── study_knowledge.py       # YAML-driven variable lookup
│   │   ├── tool_cache.py            # In-memory LRU tool cache
│   │   └── web_ui.py                # Streamlit web interface
│   ├── security/                    # Honest-broker boundary
│   │   ├── __init__.py
│   │   ├── secure_env.py            # Zone guards (RED/AMBER/GREEN/GREEN-PROTECT)
│   │   ├── phi_scrub.py             # 8-action catalog
│   │   ├── phi_scrub.yaml           # ~200 Indo-VAP rules
│   │   ├── phi_patterns.py          # Shared regex catalog
│   │   ├── phi_allowlist.py         # Clinical-phrase allowlist
│   │   ├── phi_gate.py              # Query-time PHI gate
│   │   └── kanon_gate.py            # k-anonymity gate
│   └── utils/                       # Utility modules
│       ├── __init__.py
│       ├── logging_system.py        # Centralized logging
│       ├── log_hygiene.py           # PHI log redactor
│       ├── integrity.py             # Streamed SHA-256 helpers
│       ├── lineage.py               # Per-run lineage manifest
│       ├── secure_staging.py        # mode 0700 + tmpfs staging
│       ├── snapshots.py             # Trio-bundle snapshot/restore
│       ├── step_cache.py            # Incremental-run manifest
│       ├── telemetry.py             # Agent event telemetry
│       ├── errors.py                # Structured error envelopes
│       └── smart-commit.sh          # Smart commit with version bump
├── tests/                           # pytest suite; `make test` / `make test-all`
│   ├── __init__.py
│   ├── conftest.py                  # Shared fixtures + zone-guard patches
│   ├── test_smoke.py                # End-to-end smoke tests
│   └── test_*.py                    # Module-level unit tests
├── typings/                         # Custom type stubs for Pyright
├── docs/
│   ├── irb_dossier/                 # IRB conformance matrix + executive summary
│   └── sphinx/                      # User + developer Sphinx guides
├── data/                            # Raw study data (gitignored)
│   └── raw/{STUDY}/
│       ├── datasets/
│       ├── annotated_pdfs/
│       └── data_dictionary/
├── tmp/                             # AMBER staging workspace (gitignored)
│   └── {STUDY}/                     # Removed on success; kept on failure
│       ├── datasets/
│       ├── dictionary/
│       └── pdfs/
└── output/                          # Processed artifacts (gitignored)
    └── {STUDY}/
        ├── trio_bundle/             # GREEN — LLM read zone (1 of 2)
        ├── audit/                   # IRB evidence + telemetry (LLM hard-rejected)
        └── agent/                   # GREEN — LLM read zone (2 of 2); tool-managed writes anywhere under agent/, exec-python writes restricted to analysis/
            ├── analysis/
            ├── conversations/
            └── snapshots/           # Restore-ready trio copies
```

## Configuration

Primary configuration lives in:

- `config.py` — canonical runtime paths, zone markers, and defaults
- `config/config.yaml` — project-level runtime settings (LLM provider, model)
- Environment variables — secrets and deployment-specific overrides

Key environment variables:

```bash
ANTHROPIC_API_KEY=             # Anthropic (cloud provider; default model is qwen3:8b on Ollama)
OPENAI_API_KEY=                # OpenAI
GOOGLE_API_KEY=                # Google Gemini
STUDY_NAME=                    # Override auto-detected study name
LOG_LEVEL=                     # Logging level override
LLM_PROVIDER=                  # openai | anthropic | google-genai | ollama | nvidia-ai-endpoints
LLM_MODEL=                     # LLM model name (e.g., gpt-5.5)
REPORTALIN_TMPFS_STAGING=1     # Route AMBER staging through /dev/shm
REPORTALIN_PDF_PHI_FREE=1      # Attest PDFs are PHI-free (requires dossier note)
XDG_CONFIG_HOME=               # Override HMAC sidecar-key location
```

### Security Boundaries

- **RED** — `data/raw/{STUDY}/` — read-only to extraction; `assert_not_raw` guard.
- **AMBER** — `tmp/{STUDY}/` — mode 0700, umask 0077, zero-fill teardown,
  optional `/dev/shm` tmpfs. Never LLM-readable.
- **GREEN** — `output/{STUDY}/trio_bundle/` plus `output/{STUDY}/agent/` —
  the two zones the LLM agent reads. `trio_bundle/` is PHI-free by
  construction; `agent/` holds the agent's own conversations, snapshots,
  and analysis output. Tool-managed writes (conversation persistence,
  snapshot creation, analysis narratives) may land anywhere under
  `agent/` via `validate_agent_write`; the exec-python sandbox is
  narrower and restricts LLM-generated code to `agent/analysis/` via
  `validate_sandbox_write`.
- **GREEN-PROTECT** — `phi_gate_check` + `kanon_check` on every agent tool
  return; defence-in-depth against any residual the scrub missed.
- **PHI scrub** — runs on AMBER before any audit is written; 8-action catalog,
  ~200 Indo-VAP-calibrated rules.

## Version Management

RePORT AI Portal uses **`__version__.py`** as the single source of truth for
version information, with **Conventional Commits** driving automatic semantic
versioning.

### Current Version

```bash
make version
# Or
uv run python main.py --version
```

### Automatic Version Bumping

| Commit Message | Version Bump | Example |
|----------------|--------------|---------|
| `fix: ...` | **Patch** | 0.20.0 → 0.20.1 |
| `feat: ...` | **Minor** | 0.20.0 → 0.21.0 |
| `feat!: ...` or `BREAKING CHANGE:` | **Major** | 0.20.0 → 1.0.0 |
| Other (docs, chore, refactor, style, test) | **No bump** | 0.20.0 (unchanged) |

**Via Git hooks (automatic):** commit normally — the post-commit hook analyses
the message and bumps the version automatically:

```bash
git commit -m "feat: add user authentication"
# → Bumps version and amends the commit to include __version__.py
```

**Via smart-commit (explicit with preview):**

```bash
./scripts/utils/smart-commit "feat: add user authentication"
```

Both methods detect each other to prevent double-bumping. Use `--no-verify`
to skip automatic bumping:

```bash
git commit --no-verify -m "docs: update README"
```

### Manual Version Bumping

```bash
# Edit __version__.py, then:
git add __version__.py && git commit --no-verify -m "chore: bump version to X.Y.Z"
```

### Version Consistency

All modules import version from `__version__.py`:
- `config.py`
- `main.py`
- `scripts/__init__.py`
- `scripts/utils/__init__.py`
- `docs/sphinx/conf.py`

## Troubleshooting

### Missing Dependencies / `ModuleNotFoundError`

```bash
uv sync
# or
make sync
```

### No Study Detected

Ensure your study data directory exists with the expected structure:

```text
data/raw/{STUDY}/
    ├── datasets/
    ├── annotated_pdfs/
    └── data_dictionary/
```

### Date Format Warnings

The system handles date ambiguity with country-specific format priority:

1. **Unambiguous formats first**: ISO 8601 (`YYYY-MM-DD`) always takes priority.
2. **Country-specific preference** for ambiguous dates (e.g., `08/09/2020`):
   - India (`IN`): DD/MM/YYYY → September 8, 2020
   - USA (`US`): MM/DD/YYYY → August 9, 2020
3. **Smart validation**: rejects impossible formats (e.g., `13/05/2020` can only be DD/MM).

Falls back to `[DATE-HASH]` placeholders only when all parsing attempts fail.

### Permission Denied

Check file permissions and ensure you have read/write access to `data/`,
`tmp/`, and `output/` directories. The AMBER staging workspace is created
with mode 0700 and will refuse to run if the parent directory's permissions
conflict.

### Out of Memory

The pipeline uses streaming readers for large files. If issues persist:

- Process files in smaller batches.
- Increase available RAM.

### LLM Provider Errors

Confirm that:

- `llm.provider` in `config/config.yaml` matches your intended provider.
- The required API key environment variable is set.

### PDF Extraction Refuses to Run

The external-API PDF extractor is blocked by default. To enable it you must
both (a) set `REPORTALIN_PDF_PHI_FREE=1`, and (b) commit an attestation note
at `docs/irb_dossier/authorities/phi_free_pdfs.md` (copy the template in the
same directory). Missing either will fail the step by design.

## Requirements

### System Requirements

- **Python**: 3.11+
- **Package Manager**: [uv](https://docs.astral.sh/uv/) (required)
- **OS**: macOS, Linux, or Windows
- **Memory**: 4 GB RAM minimum (8 GB+ recommended)
- **Disk**: 1 GB+ free space

### Key Python Dependencies

- `pandas`, `openpyxl`, `numpy` — data manipulation
- `cryptography` — HMAC + secure randomness
- `pypdf`, `pdfplumber` — PDF processing
- `tqdm`, `pyyaml` — UX and configuration
- `langchain-core`, `langgraph` — AI Assistant framework
- `scipy`, `statsmodels`, `matplotlib` — analytical engine
- `streamlit` — web UI

See `pyproject.toml` for the complete list with version constraints.

> 📖 **Installation details**: [Installation Guide](docs/sphinx/user_guide/installation.rst)

## Documentation

Build docs locally:

```bash
make docs
# Or manually:
cd docs/sphinx && make html
open _build/html/index.html   # macOS
xdg-open _build/html/index.html  # Linux
```

Online: <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

## Contributing

1. **Fork** the repository.
2. **Create** a feature branch.
3. **Make** your changes.
4. **Test** thoroughly (`make ci`).
5. **Update** documentation.
6. **Submit** a pull request.

See the [Contributing Guide](docs/sphinx/developer_guide/contributing.rst)
for details.

## License

This project is licensed under the MIT License — see the LICENSE file for
details.

## Support

- **Issues**: [GitHub Issues](https://github.com/solomonsjoseph/RePORT-AI-Portal/issues)
- **Documentation**: <https://solomonsjoseph.github.io/RePORT-AI-Portal/>

---

**Version**: 0.20.0 | **Status**: Beta (Active Development — Single-Study Mode)
