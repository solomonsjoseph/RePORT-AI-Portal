# ============================================================================
# RePORT AI Portal - Local Development Makefile
# ============================================================================
# Package manager: uv (required)
# Python: 3.11+
#
# One-stop command centre for the entire project lifecycle:
#   Environment  — sync, version, clean, nuke
#   Pipeline     — dictionary, process-datasets, bundle
#   AI Assistant  — chat, web
#   Quality      — test, lint, typecheck, ci, verify
#   Docs         — docs
#
# Modifiers (prefix any target):
#   VERBOSE=1    — DEBUG logging / extra output
#   FORCE=1      — Bypass incremental cache
# ============================================================================

SHELL := /bin/bash

UV := $(shell command -v uv 2>/dev/null)
ifndef UV
$(error uv is required. Install: curl -LsSf https://astral.sh/uv/install.sh | sh)
endif

PYTHON := $(UV) run python
RUFF := $(UV) run ruff
MYPY := $(UV) run mypy
VERBOSE ?=
FORCE ?=
PDF_SOURCE ?=
PROVIDER ?=
MODEL ?=

ifdef VERBOSE
VFLAG := --verbose
else
VFLAG :=
endif

ifdef FORCE
FFLAG := --force
else
FFLAG :=
endif

ifdef PDF_SOURCE
PDFFLAG := --pdf-source $(PDF_SOURCE)
else
PDFFLAG :=
endif

ifdef PROVIDER
PROVIDERFLAG := --provider $(PROVIDER)
else
PROVIDERFLAG :=
endif

ifdef MODEL
MODELFLAG := --model $(MODEL)
else
MODELFLAG :=
endif

# ANSI color helpers — use printf so escape codes render on macOS + Linux
C := \033[0;36m
G := \033[0;32m
Y := \033[0;33m
R := \033[0;31m
B := \033[1m
N := \033[0m

.DEFAULT_GOAL := help
.PHONY: \
	help quickstart debug sync version \
	pipeline dictionary extract-datasets bundle pdf-extract \
	chat-cli chat build-variables \
	snapshot snapshot-study restore-study list-snapshots \
	test test-all lint typecheck security ci verify docs \
	clean nuke

# ═══════════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════════

help:
	@printf "\n"
	@printf "$(B)$(C) ╔══════════════════════════════════════════════════════╗ $(N)\n"
	@printf "$(B)$(C) ║        RePORT AI Portal — Command Centre                  ║ $(N)\n"
	@printf "$(B)$(C) ╚══════════════════════════════════════════════════════╝ $(N)\n"
	@printf "\n"
	@printf "$(B)$(G)  Quickstart$(N)\n"
	@printf "  $(C)make quickstart$(N)       Sync → full pipeline\n"
	@printf "  $(C)make debug$(N)            Same as quickstart with DEBUG logging\n"
	@printf "\n"
	@printf "$(B)$(G)  Environment$(N)\n"
	@printf "  $(C)make sync$(N)             Install / restore all dependencies (uv sync)\n"
	@printf "  $(C)make version$(N)          Show version + environment info\n"
	@printf "\n"
	@printf "$(B)$(G)  Pipeline — full$(N)\n"
	@printf "  $(C)make pipeline$(N)         Dict → Datasets → PDF → Bundle\n"
	@printf "\n"
	@printf "$(B)$(G)  Pipeline — individual steps$(N)\n"
	@printf "  $(C)make dictionary$(N)       Step 0  — Load data dictionary → JSON\n"
	@printf "  $(C)make extract-datasets$(N) Step 1+3 — Extract → promote datasets\n"
	@printf "  $(C)make bundle$(N)           Step 2   — Build Trio bundle (dict + pdf + datasets)\n"
	@printf "  $(C)make pdf-extract$(N)      Standalone — Extract annotated PDFs → JSON\n"
	@printf "\n"
	@printf "$(B)$(G)  AI Assistant$(N)\n"
	@printf "  $(C)make chat-cli$(N)         Start interactive AI Assistant chat (CLI)\n"
	@printf "  $(C)make chat$(N)             Launch Streamlit web UI (with setup wizard)\n"
	@printf "  $(C)make build-variables$(N)  Build variables.json from all annotation sources\n"
	@printf "\n"
	@printf "$(B)$(G)  Restore Points$(N)  $(Y)(output/{STUDY}/agent/restore_points/ — gitignored)$(N)\n"
	@printf "  $(C)make snapshot$(N)         Copy output/{STUDY}/trio_bundle/ → agent/restore_points/<ts>/\n"
	@printf "  $(C)make list-snapshots$(N)   List available restore points (newest first)\n"
	@printf "  $(C)make restore-study$(N)    Restore a point back into trio_bundle/ (SNAPSHOT=<name>)\n"
	@printf "  $(Y)Note: tracked-baseline snapshots/{STUDY}/ is curated by hand — see snapshots/README.md$(N)\n"
	@printf "\n"
	@printf "$(B)$(G)  Quality$(N)\n"

	@printf "  $(C)make lint$(N)             Ruff check + format\n"
	@printf "  $(C)make test$(N)             Run deterministic pytest suite (no AI Assistant/agent)\n"
	@printf "  $(C)make test-all$(N)         Run full pytest suite (requires AI Assistant deps)\n"
	@printf "  $(C)make typecheck$(N)        Run mypy\n"
	@printf "  $(C)make security$(N)         Run dependency vulnerability audit\n"
	@printf "  $(C)make ci$(N)               lint → typecheck → test\n"
	@printf "  $(C)make verify$(N)           Local readiness checks\n"
	@printf "\n"
	@printf "$(B)$(G)  Docs$(N)\n"
	@printf "  $(C)make docs$(N)             Build Sphinx HTML docs\n"
	@printf "\n"
	@printf "$(B)$(G)  Maintenance$(N)\n"
	@printf "  $(C)make clean$(N)            Remove caches, sessions, stale logs\n"
	@printf "  $(C)make nuke$(N)             Remove everything (venv, output, indexes)\n"
	@printf "\n"
	@printf "$(Y)  Modifiers:$(N)\n"
	@printf "  $(Y)VERBOSE=1$(N) make <target>   Enable DEBUG logging\n"
	@printf "  $(Y)FORCE=1$(N)   make <target>   Force re-run (ignore cache)\n"
	@printf "  $(Y)PDF_SOURCE=/path$(N)            Use pre-extracted PDF JSON files\n"
	@printf "  $(Y)PROVIDER=anthropic$(N)          LLM provider (ollama, anthropic, openai, google-genai)\n"
	@printf "  $(Y)MODEL=claude-sonnet-4-6$(N)     LLM model name\n"
	@printf "\n"

# ═══════════════════════════════════════════════════════════════════════
# ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════

sync:
	@printf "$(C)Syncing dependencies with uv...$(N)\n"
	@$(UV) sync --all-groups
	@printf "$(G)✓ Dependencies synced$(N)\n"

version:
	@$(PYTHON) main.py --version
	@$(PYTHON) --version
	@$(UV) --version

# ═══════════════════════════════════════════════════════════════════════
# QUICKSTART
# ═══════════════════════════════════════════════════════════════════════

quickstart: sync pipeline

debug:
	@printf "$(Y)⚙  Debug mode — quickstart with DEBUG logging$(N)\n"
	@$(MAKE) quickstart VERBOSE=1

# ═══════════════════════════════════════════════════════════════════════
# PIPELINE — FULL
# ═══════════════════════════════════════════════════════════════════════

pipeline:
	@printf "$(C)Running full pipeline: Dict → Datasets → PDF → Bundle → Variables$(N)\n"
	@$(PYTHON) main.py --pipeline $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG) $(FFLAG) $(PDFFLAG)
	@printf "$(G)✓ Pipeline complete$(N)\n"

# ═══════════════════════════════════════════════════════════════════════
# PIPELINE — INDIVIDUAL STEPS
# ═══════════════════════════════════════════════════════════════════════

dictionary:
	@printf "$(C)Step 0: Loading data dictionary...$(N)\n"
	@$(PYTHON) main.py $(VFLAG) $(FFLAG)
	@printf "$(G)✓ Data dictionary loaded$(N)\n"

extract-datasets:
	@printf "$(C)Step 1+3: Extract → promote datasets...$(N)\n"
	@$(PYTHON) main.py --skip-dictionary --process-datasets $(VFLAG) $(FFLAG)
	@printf "$(G)✓ Dataset processing complete$(N)\n"

bundle:
	@printf "$(C)Step 2: Building Trio bundle...$(N)\n"
	@$(PYTHON) main.py --build-bundle $(VFLAG) $(FFLAG) $(PDFFLAG)
	@printf "$(G)✓ Trio bundle built$(N)\n"

pdf-extract:
	@printf "$(C)PDF Extraction: annotated PDFs → structured JSON$(N)\n"
	@$(PYTHON) -m scripts.extraction.extract_pdf_data
	@printf "$(G)✓ PDF extraction complete$(N)\n"



# ═══════════════════════════════════════════════════════════════════════
# AI Assistant
# ═══════════════════════════════════════════════════════════════════════

chat-cli:
	@printf "$(C)Starting interactive AI Assistant chat (CLI)...$(N)\n"
	@if [ -z "$(PROVIDER)" ] || [ "$(PROVIDER)" = "ollama" ]; then \
		if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then \
			printf "$(Y)Ollama not running — starting it…$(N)\n"; \
			ollama serve >/dev/null 2>&1 & \
			for i in 1 2 3 4 5 6 7 8 9 10; do \
				sleep 1; \
				if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi; \
			done; \
			if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then \
				printf "$(R)✗ Could not start Ollama. Install it or use PROVIDER=anthropic$(N)\n"; \
				exit 1; \
			fi; \
			printf "$(G)✓ Ollama started$(N)\n"; \
		fi; \
	fi
	@$(PYTHON) main.py --chat $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

chat:
	@printf "$(C)Launching Streamlit web UI...$(N)\n"
	@$(PYTHON) main.py --web $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

build-variables:
	@printf "$(C)Building variables.json from all annotation sources...$(N)\n"
	@$(PYTHON) main.py --build-variables $(VFLAG)
	@printf "$(G)✓ variables.json built$(N)\n"

# ═══════════════════════════════════════════════════════════════════════
# RESTORE POINTS — trio_bundle backup / restore (gitignored, multi-named)
# ═══════════════════════════════════════════════════════════════════════
# Lands in ``output/{STUDY}/agent/restore_points/`` — DISTINCT from the
# tracked baseline at ``snapshots/{STUDY}/`` (which is maintainer-curated
# by hand; see ``snapshots/README.md``).
# SNAPSHOT=<name>   (optional) explicit restore-point name
#                   default for snapshot-study: UTC timestamp
#                   required   for restore-study
# FORCE=1           allow overwriting an existing restore point of the same name

snapshot-study:
	@$(PYTHON) -m scripts.utils.snapshots create \
		$(if $(SNAPSHOT),--name $(SNAPSHOT),) \
		$(if $(filter 1 true yes True Yes on,$(FORCE)),--force,)

# Short alias for snapshot-study — intended for operators who invoke
# this frequently. Same behaviour, same modifiers (SNAPSHOT=, FORCE=1).
snapshot: snapshot-study

restore-study:
	@if [ -z "$(SNAPSHOT)" ]; then \
		printf "$(R)✗ Usage: make restore-study SNAPSHOT=<name>$(N)\n"; \
		printf "$(Y)Available snapshots:$(N)\n"; \
		$(PYTHON) -c "from scripts.utils.snapshots import list_snapshots; \
[print(f'  - {n}') for n in list_snapshots()] or print('  (none)')"; \
		exit 1; \
	fi
	@printf "$(C)Restoring snapshot '$(SNAPSHOT)' into trio_bundle/...$(N)\n"
	@$(PYTHON) -c "from scripts.utils.snapshots import restore_snapshot; \
p = restore_snapshot('$(SNAPSHOT)'); \
print(f'✓ Restored {p}')"

list-snapshots:
	@$(PYTHON) -c "from scripts.utils.snapshots import list_snapshots; \
names = list_snapshots(); \
print('Snapshots (newest first):') if names else print('No snapshots available.'); \
[print(f'  - {n}') for n in names]"

# ═══════════════════════════════════════════════════════════════════════
# QUALITY
# ═══════════════════════════════════════════════════════════════════════

test:
	@$(PYTHON) -m pytest tests/ \
		--ignore=tests/test_agent_tools.py \
		--ignore=tests/test_agent_graph.py \
		--ignore=tests/test_cli.py \
		--ignore=tests/test_telemetry.py
	@printf "$(G)✓ Tests passed$(N)\n"

test-all:
	@$(PYTHON) -m pytest tests/
	@printf "$(G)✓ All tests passed$(N)\n"

lint:
	@$(RUFF) check . --fix
	@$(RUFF) format .
	@printf "$(G)✓ Lint + format done$(N)\n"

typecheck:
	@$(MYPY) scripts/ main.py config.py --ignore-missing-imports
	@printf "$(G)✓ Typecheck passed$(N)\n"

security:
	@$(UV) run pip-audit
	@printf "$(G)✓ Security audit passed$(N)\n"

ci: lint typecheck test
	@printf "$(G)✓ All CI gates passed$(N)\n"

verify:
	@printf "$(C)🔍 Local verification$(N)\n"
	@$(RUFF) check . --quiet && printf "$(G)✓ Lint$(N)\n"
	@$(MYPY) scripts/ main.py config.py --ignore-missing-imports --no-error-summary 2>&1 | tail -1
	@for f in \
		scripts/security/secure_env.py; do \
		test -f "$$f" && printf "  $(G)✓$(N) %s\n" "$$f" || printf "  $(R)✗$(N) %s\n" "$$f"; \
	done
	@printf "$(G)✓ Local verification complete$(N)\n"

# ═══════════════════════════════════════════════════════════════════════
# DOCS
# ═══════════════════════════════════════════════════════════════════════

docs:
	@cd docs/sphinx && $(MAKE) html
	@printf "$(G)✓ Docs built → docs/sphinx/_build/html/index.html$(N)\n"

doc-freshness:
	@printf "$(C)🔍 Doc-freshness lint$(N)\n"
	@$(UV) run --frozen python scripts/lint_doc_freshness.py

# ═══════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ═══════════════════════════════════════════════════════════════════════

clean:
	@find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov 2>/dev/null || true
	@if [ -d ".logs" ]; then find .logs/ -type f -mtime +7 -delete 2>/dev/null || true; fi
	@printf "$(G)✓ Caches, sessions, stale logs cleaned$(N)\n"

nuke:
	@printf "$(R)This removes: .venv, output/, .logs/, caches, tmp/$(N)\n"
	@printf "Type 'yes' to confirm: " && read r && [ "$$r" = "yes" ] || { printf "$(Y)Cancelled.$(N)\n"; exit 1; }
	@rm -rf .venv output/ .logs/ logs/ tmp/* docs/sphinx/_build/ 2>/dev/null || true
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name "*.pyc" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov 2>/dev/null || true
	@printf "$(G)Nuked. Run 'make sync' to restore deps (or 'make quickstart' to fully rebuild + launch).$(N)\n"
