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
#   Docs         — docs, docs-quality, docs-linkcheck, docs-ci
#
# Modifiers (prefix any target):
#   VERBOSE=1    — DEBUG logging / extra output
#   FORCE=1      — Bypass incremental cache
# ============================================================================

UV ?= uv
STUDY ?= Indo-VAP
COLUMN_INVENTORY ?=

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(abspath .venv/Scripts/python.exe)
else
VENV_PYTHON := $(abspath .venv/bin/python)
endif

CHAT_GROUPS := --group web --group ai_assistant --group llm
CLI_GROUPS := --group ai_assistant --group llm
PYTHON := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),$(UV) run python)
RUFF := $(UV) run ruff
MYPY := $(UV) run mypy
VERBOSE ?=
FORCE ?=
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
	pipeline dictionary extract-datasets build-llm-source verify-and-promote bundle \
	chat-deps chat-cli-deps chat-cli chat \
	test test-all lint typecheck security ci verify release-check \
	docs doc-freshness docs-quality docs-linkcheck docs-ci release-notes \
	chat-smoke \
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
	@printf "  $(C)make pipeline$(N)         Dict → Datasets → Bundle\n"
	@printf "\n"
	@printf "$(B)$(G)  Pipeline — individual steps$(N)\n"
	@printf "  $(C)make dictionary$(N)       Step 0  — Load data dictionary → JSON\n"
	@printf "  $(C)make extract-datasets$(N) Step 1+3 — Extract → promote datasets\n"
	@printf "  $(C)make bundle$(N)           Step 2   — Build Trio bundle (dict + datasets)\n"
	@printf "\n"
	@printf "$(B)$(G)  AI Assistant$(N)\n"
	@printf "  $(C)make chat-cli$(N)         Start interactive AI Assistant chat (CLI)\n"
	@printf "  $(C)make chat$(N)             Install needed deps → launch web UI\n"
	@printf "\n"
	@printf "$(B)$(G)  Quality$(N)\n"

	@printf "  $(C)make lint$(N)             Ruff check + format\n"
	@printf "  $(C)make test$(N)             Run deterministic pytest suite (no AI Assistant/agent)\n"
	@printf "  $(C)make test-all$(N)         Run full pytest suite (requires AI Assistant deps)\n"
	@printf "  $(C)make typecheck$(N)        Run mypy\n"
	@printf "  $(C)make security$(N)         Run dependency vulnerability audit\n"
	@printf "  $(C)make ci$(N)               lint → typecheck → test\n"
	@printf "  $(C)make verify$(N)           Local readiness checks\n"
	@printf "  $(C)make release-check$(N)    verify → typecheck → test-all → docs-ci → security\n"
	@printf "\n"
	@printf "$(B)$(G)  Docs$(N)\n"
	@printf "  $(C)make docs$(N)             Build Sphinx HTML docs\n"
	@printf "  $(C)make doc-freshness$(N)    Run stale-doc lint\n"
	@printf "  $(C)make docs-quality$(N)     Doc freshness + warnings-as-errors build\n"
	@printf "  $(C)make docs-linkcheck$(N)   Run Sphinx linkcheck\n"
	@printf "  $(C)make docs-ci$(N)          Docs quality + linkcheck, matching docs CI\n"
	@printf "  $(C)make release-notes$(N)    Print Sphinx release notes source\n"
	@printf "\n"
	@printf "$(B)$(G)  Maintenance$(N)\n"
	@printf "  $(C)make clean$(N)            Remove caches, docs build output, stale logs\n"
	@printf "  $(C)make nuke$(N)             Remove generated state; preserve data/raw\n"
	@printf "\n"
	@printf "$(Y)  Modifiers:$(N)\n"
	@printf "  $(Y)VERBOSE=1$(N) make <target>   Enable DEBUG logging\n"
	@printf "  $(Y)FORCE=1$(N)   make <target>   Force re-run (ignore cache)\n"
	@printf "  $(Y)PROVIDER=anthropic$(N)          LLM provider (ollama, anthropic, openai, google-genai)\n"
	@printf "  $(Y)MODEL=claude-opus-4-7$(N)      LLM model name\n"
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

pipeline: build-llm-source
	@printf "$(C)Running full pipeline: Dict → Datasets → Bundle$(N)\n"
	@$(PYTHON) main.py --pipeline $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG) $(FFLAG)
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

build-llm-source: ## Run SoT-driven build coordinator (Branch Y of pipeline)
	@if [ ! -d "data/$(STUDY)/SoT" ]; then \
		printf "$(Y)>> SKIP build-llm-source for STUDY=$(STUDY): data/$(STUDY)/SoT/ not found.$(N)\n"; \
		printf "$(Y)>> To enable, add per-form policy YAMLs under data/$(STUDY)/SoT/.$(N)\n"; \
		exit 0; \
	fi; \
	printf "$(C)$(B)>> Running build coordinator for STUDY=$(STUDY)$(N)\n"; \
	$(UV) run --all-groups python -m scripts.source_truth.build \
		--study $(STUDY) \
		--policies-dir data/$(STUDY)/SoT \
		--output-root output/$(STUDY) \
		$(if $(COLUMN_INVENTORY),--column-inventory $(COLUMN_INVENTORY)) && \
	$(MAKE) verify-and-promote STUDY=$(STUDY) --no-print-directory

verify-and-promote: ## Reconcile SoT vs scrubbed dataset; emit per-form discrepancies on mismatch
	@if [ ! -d "data/$(STUDY)/SoT" ]; then \
		printf "$(Y)>> SKIP verify-and-promote for STUDY=$(STUDY): data/$(STUDY)/SoT/ not found.$(N)\n"; \
		exit 0; \
	fi; \
	printf "$(C)$(B)>> Running verify-and-promote gate for STUDY=$(STUDY)$(N)\n"; \
	$(UV) run --all-groups python -m scripts.source_truth.verify_and_promote --study $(STUDY)

phi-audit: ## Run SoT-driven PHI sweep and emit drafts under tmp/
	$(UV) run --all-groups python -m scripts.security.phi_sot_sweep
	$(UV) run --all-groups python -m scripts.security.phi_sot_sweep emit

phi-audit-verify: ## Fail if any SoT variable lacks coverage AND no open HITL draft
	$(UV) run --all-groups python -m scripts.security.phi_sot_sweep verify

llm-source-build: ## Build per-form evidence packs + lean catalogs for llm_source/
	$(UV) run --all-groups python -m scripts.source_truth.evidence_pack_consolidator
	$(UV) run --all-groups python -m scripts.source_truth.llm_source_catalogs

cross-verify: ## Run mid-pipeline cross-verifier (scanner + fix agent), emit drafts and live PRs/issues
	$(UV) run --all-groups python -m scripts.source_truth.cross_verify_pipeline

bundle:
	@printf "$(C)Step 2: Building Trio bundle...$(N)\n"
	@$(PYTHON) main.py --build-bundle $(VFLAG) $(FFLAG)
	@printf "$(G)✓ Trio bundle built$(N)\n"


# ═══════════════════════════════════════════════════════════════════════
# AI Assistant
# ═══════════════════════════════════════════════════════════════════════

chat-cli-deps:
	@echo Ensuring AI Assistant dependencies...
	@$(UV) run $(CLI_GROUPS) python -c "import langchain, langgraph"

chat-deps:
	@echo Ensuring web chat dependencies...
	@$(UV) run $(CHAT_GROUPS) python -c "import streamlit, langchain, langgraph"

chat-cli: chat-cli-deps
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
	@$(UV) run $(CLI_GROUPS) python main.py --chat $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

chat: chat-deps
	@echo Launching Streamlit web UI...
	@$(UV) run $(CHAT_GROUPS) python main.py --web $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

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

lint: lint-legacy-dirs
	@$(RUFF) check . --fix
	@$(RUFF) format .
	@printf "$(G)✓ Lint + format done$(N)\n"

lint-legacy-dirs: ## Phase 5b: fail on legacy output directory name strings in scripts/
	@$(PYTHON) -m scripts.lint_legacy_dirs
	@printf "$(G)✓ Legacy-dirs lint passed$(N)\n"

typecheck:
	@$(MYPY) scripts/ main.py config.py --ignore-missing-imports
	@printf "$(G)✓ Typecheck passed$(N)\n"

security:
	@$(UV) run pip-audit
	@printf "$(G)✓ Security audit passed$(N)\n"

chat-smoke:
	@$(PYTHON) -m pytest tests/test_production_smoke.py
	@printf "$(G)✓ Chat smoke passed$(N)\n"

cutover-gate:
	@$(PYTHON) -m pytest tests/test_hard_cutover_validation_gate.py -v
	@printf "$(G)✓ Hard cutover validation gate passed$(N)\n"

ci: lint typecheck test-all chat-smoke
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

release-check: verify typecheck test-all docs-ci security
	@printf "$(G)✓ Release readiness checks passed$(N)\n"

# ═══════════════════════════════════════════════════════════════════════
# DOCS
# ═══════════════════════════════════════════════════════════════════════

docs:
	@cd docs/sphinx && $(MAKE) html SPHINXBUILD="$(UV) run --frozen sphinx-build"
	@printf "$(G)✓ Docs built → docs/sphinx/_build/html/index.html$(N)\n"

doc-freshness:
	@printf "$(C)🔍 Doc-freshness lint$(N)\n"
	@$(UV) run --frozen python scripts/lint_doc_freshness.py

docs-quality: doc-freshness
	@cd docs/sphinx && $(MAKE) html SPHINXBUILD="$(UV) run --frozen sphinx-build" SPHINXOPTS="-W"
	@printf "$(G)✓ Docs quality checks passed$(N)\n"

docs-linkcheck:
	@cd docs/sphinx && $(UV) run --frozen sphinx-build -b linkcheck . _build/linkcheck
	@printf "$(G)✓ Docs linkcheck passed$(N)\n"

docs-ci: docs-quality docs-linkcheck
	@printf "$(G)✓ Docs CI checks passed$(N)\n"

release-notes:
	@sed -n '1,220p' docs/sphinx/release_notes.rst

# ═══════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ═══════════════════════════════════════════════════════════════════════

clean:
	@find scripts tests docs/sphinx -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	@find scripts tests docs/sphinx -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@find . -maxdepth 1 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -maxdepth 1 -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov docs/sphinx/_build 2>/dev/null || true
	@if [ -d ".logs" ]; then find .logs/ -type f -mtime +7 -delete 2>/dev/null || true; fi
	@printf "$(G)✓ Caches, sessions, stale logs cleaned$(N)\n"

clean-legacy: ## Phase 5b: write pre-delete manifest, prune per-variable packs, delete legacy output dirs
	$(UV) run --all-groups python -m scripts.utils.pre_delete_cleanup

clean-legacy-dry-run: ## Phase 5b: print what clean-legacy would delete (no filesystem changes)
	$(UV) run --all-groups python -m scripts.utils.pre_delete_cleanup --dry-run

nuke:
	@printf "$(R)This removes generated state: .venv, output/, .logs/, logs/, tmp/, docs/sphinx/_build/, caches.$(N)\n"
	@printf "$(Y)It preserves data/raw/.$(N)\n"
	@printf "Type 'yes' to confirm: " && read r && [ "$$r" = "yes" ] || { printf "$(Y)Cancelled.$(N)\n"; exit 1; }
	@rm -rf .venv output/ .logs/ logs/ tmp/ docs/sphinx/_build/ 2>/dev/null || true
	@find scripts tests docs/sphinx -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	@find scripts tests docs/sphinx -type f \( -name "*.pyc" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@find . -maxdepth 1 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -maxdepth 1 -type f \( -name "*.pyc" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov 2>/dev/null || true
	@printf "$(G)Nuked. Run 'make chat' to recreate web deps + launch, or 'make sync' to restore every dependency group first.$(N)\n"
