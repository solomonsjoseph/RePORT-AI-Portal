# ============================================================================
# RePORT AI Portal - Local Development Makefile
# ============================================================================
# Package manager: uv (required)
# Python: 3.11+
#
# One-stop command centre for the entire project lifecycle:
#   Environment  — sync, version, clean, nuke
#   Study prep   — plugin workflow, dictionary, dataset publish, llm_source
#   AI Assistant  — chat, web
#   Quality      — test, lint, typecheck, ci, verify
#   Docs         — docs, docs-quality, docs-linkcheck, docs-ci
#
# Modifiers (prefix any target):
#   VERBOSE=1    — DEBUG logging / extra output
#   FORCE=1      — Bypass incremental cache
# ============================================================================

UV ?= uv
UV_RUN_LOCKED ?= $(UV) run --locked
STUDY ?= Indo-VAP
CANDIDATE ?= /tmp/$(FORM)_lean.yaml
SOT_PAIR ?= $(FORM)

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

ifdef ADD
ADDFLAG := --add
else
ADDFLAG :=
endif

RESUME ?=
ifdef RESUME
RESUMEFLAG := --resume-held
else
RESUMEFLAG :=
endif

STRICT ?=
ifdef STRICT
STRICTFLAG := --strict-abort
else
STRICTFLAG :=
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
	study rebuild-llm-source \
	sot-source-pack sot-generate-all sot-verify sot-verify-output sot-validate \
	chat-deps chat-cli-deps chat-cli chat \
	test test-all lint lint-legacy-dirs typecheck typecheck-skills security ci verify release-check \
	docs doc-freshness docs-quality docs-linkcheck docs-ci release-notes \
	chat-smoke check-study-knowledge \
	clean clean-legacy clean-legacy-dry-run nuke \
	organize

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
	@printf "  $(C)make quickstart$(N)       Sync → web UI → Load Study plugin\n"
	@printf "  $(C)make debug$(N)            Same as quickstart with DEBUG logging\n"
	@printf "\n"
	@printf "$(B)$(G)  Environment$(N)\n"
	@printf "  $(C)make sync$(N)             Install / restore all dependencies (uv sync)\n"
	@printf "  $(C)make version$(N)          Show version + environment info\n"
	@printf "\n"
	@printf "$(B)$(G)  Study build (the plugin IS the pipeline)$(N)\n"
	@printf "  $(C)make study$(N) STUDY=…    Build/publish a study via the 10-phase orchestrator\n"
	@printf "                          (modifiers: FORCE=1, RESUME=1 maintainer resume, STRICT=1)\n"
	@printf "  $(C)make rebuild-llm-source$(N) STUDY=… — Remove generated llm_source/staging, preserve audit/agent, then re-run\n"
	@printf "\n"
	@printf "$(B)$(G)  Source-of-Truth (SoT)$(N)\n"
	@printf "  $(C)make sot-source-pack$(N)  STUDY=… FORM=… — Stage 0: resolve PDF+dataset → source pack + page renders\n"
	@printf "  $(C)make sot-generate-all$(N) STUDY=… — Generate+verify PDF/header SoT outputs into llm_source/SoT\n"
	@printf "  $(C)make sot-verify$(N)       STUDY=… FORM=… [CANDIDATE=…] — Stage 4 candidate verifier + property validator\n"
	@printf "  $(C)make sot-verify-output$(N) STUDY=… FORM=… — Verify promoted output YAML\n"
	@printf "  $(C)make sot-validate$(N)     STUDY=… FORM=… — All gates: verifier + validator + diff-against-gold\n"
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
	@printf "  $(C)make check-study-knowledge$(N) Diff published study_variable_map.yaml against config/study_knowledge.yaml\n"
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

quickstart: sync chat

debug:
	@printf "$(Y)⚙  Debug mode — quickstart with DEBUG logging$(N)\n"
	@$(MAKE) quickstart VERBOSE=1

# ═══════════════════════════════════════════════════════════════════════
# STUDY BUILD — the plugin IS the pipeline (10-phase orchestrator)
# ═══════════════════════════════════════════════════════════════════════
#
# `make study STUDY=<name>` is the single entry point for building/publishing a
# study. It exports STUDY_NAME (so config resolves the same study the
# orchestrator was asked to run) and delegates to the 10-phase orchestrator,
# which holds the per-study lock for the whole run. Modifiers:
#   FORCE=1   — run even if inputs are unchanged (skip the redundant-run check)
#   RESUME=1  — maintainer human-review resume (re-publish the full surviving set)
#   STRICT=1  — abort the whole study on the first un-scrubbable row
ORCHESTRATOR := plugins/report-ai-study-pipeline/skills/report-ai-study-pipeline/scripts/run.py

study: ## Build/publish a study via the 10-phase orchestrator (the pipeline)
	@printf "$(C)Running 10-phase study pipeline for STUDY=$(STUDY)...$(N)\n"
	@STUDY_NAME=$(STUDY) $(UV) run --all-groups python $(ORCHESTRATOR) \
		--study $(STUDY) $(FFLAG) $(RESUMEFLAG) $(STRICTFLAG)
	@printf "$(G)✓ Study pipeline complete for $(STUDY)$(N)\n"

INTAKE := plugins/report-ai-study-pipeline/skills/raw-data-intake/scripts/run.py

organize: ## Skill 0: sort an unorganized study delivery into data/raw/<study>/ (SRC=dir-or-zip; ADD=1 to file new files into an organized study)
	@printf "$(C)Organizing raw delivery for STUDY=$(STUDY) from SRC=$(SRC)...$(N)\n"
	@STUDY_NAME=$(STUDY) $(UV) run --all-groups python $(INTAKE) \
		--study $(STUDY) --src $(SRC) $(FFLAG) $(ADDFLAG)
	@printf "$(G)✓ Intake complete for $(STUDY)$(N)\n"

rebuild-llm-source: ## Remove generated llm_source/staging, preserve audit/agent, then re-run the orchestrator
	@printf "$(Y)Removing generated llm_source/staging for STUDY=$(STUDY); preserving audit manifest, agent state, and raw inputs.$(N)\n"
	@STUDY_NAME=$(STUDY) $(UV) run --all-groups python -m scripts.utils.pre_delete_cleanup
	@rm -rf "output/$(STUDY)/llm_source" "tmp/$(STUDY)" 2>/dev/null || true
	@$(MAKE) study STUDY=$(STUDY) FORCE=1

# ═══════════════════════════════════════════════════════════════════════
# SOURCE TRUTH — INDIVIDUAL STEPS
# ═══════════════════════════════════════════════════════════════════════

sot-source-pack: ## Stage 0: resolve PDF+dataset and write source pack JSON + per-page render PNGs
	$(UV) run --all-groups python -m scripts.source_truth.study_intake \
		--study $(STUDY) --form $(FORM) --repo-root .

sot-generate-all: ## Generate and verify all PDF/header SoT outputs into llm_source/SoT
	$(UV) run --all-groups python -m scripts.source_truth.generate_lean_outputs \
		--study $(STUDY) --repo-root .

sot-verify: ## Stage 4: verify candidate lean YAML against the source pack produced by sot-source-pack
	$(UV) run --all-groups python \
		plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/check_lean_policy.py \
		--lean $(CANDIDATE) \
		--source-pack /tmp/sot_source_pack_$(FORM).json \
		--repo-root .

sot-verify-output: ## Verify the construction policy YAML (audit zone) against the source pack produced by sot-source-pack
	$(UV) run --all-groups python \
		plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/check_lean_policy.py \
		--policy output/$(STUDY)/audit/SoT_construction/$(SOT_PAIR)/pdf/$(FORM)_policy.yaml \
		--source-pack /tmp/sot_source_pack_$(FORM).json \
		--repo-root .

sot-validate: ## All-gates check: verifier + property validator + diff-against-gold (hard fail on any step)
	@if [ -z "$(FORM)" ]; then \
		printf "$(R)ERROR: FORM is required — e.g. make sot-validate STUDY=Indo-VAP FORM=6_HIV$(N)\n"; \
		exit 1; \
	fi
	@if [ ! -f /tmp/sot_source_pack_$(FORM).json ]; then \
		printf "$(R)ERROR: source pack missing — run 'make sot-source-pack STUDY=$(STUDY) FORM=$(FORM)' first$(N)\n"; \
		exit 1; \
	fi
	@if [ ! -f "$(CANDIDATE)" ]; then \
		printf "$(R)ERROR: candidate missing — expected $(CANDIDATE) or pass CANDIDATE=/path/to/lean.yaml$(N)\n"; \
		exit 1; \
	fi
	@$(UV) run --all-groups python \
		plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/check_lean_policy.py \
		--lean $(CANDIDATE) \
		--source-pack /tmp/sot_source_pack_$(FORM).json \
		--repo-root .
	@$(UV) run --all-groups python scripts/source_truth/diff_against_gold.py \
		--study $(STUDY) --form $(FORM) \
		--candidate $(CANDIDATE)
	@printf "$(G)✓ sot-validate STUDY=$(STUDY) FORM=$(FORM) — all gates green$(N)\n"


# ═══════════════════════════════════════════════════════════════════════
# AI Assistant
# ═══════════════════════════════════════════════════════════════════════

chat-cli-deps:
	@echo Ensuring AI Assistant dependencies...
	@$(UV_RUN_LOCKED) $(CLI_GROUPS) python -c "import langchain, langgraph"

chat-deps:
	@echo Ensuring web chat dependencies...
	@$(UV_RUN_LOCKED) $(CHAT_GROUPS) python -c "import streamlit, langchain, langgraph"

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
	@$(UV_RUN_LOCKED) $(CLI_GROUPS) python main.py --chat $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

chat: chat-deps
	@echo Launching Streamlit web UI...
	@$(UV_RUN_LOCKED) $(CHAT_GROUPS) python main.py --web $(PROVIDERFLAG) $(MODELFLAG) $(VFLAG)

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

typecheck-skills: ## Informational mypy over the plugin skill scripts (non-blocking)
	@printf "$(C)Type-checking plugin skill scripts (informational)…$(N)\n"
	@# The skill scripts are imported as scripts.* via the meta_path shim at
	@# runtime; mypy cannot follow that bridge, so they are type-checked here by
	@# their on-disk path. Non-blocking (piped) — the blocking gate is `typecheck`.
	@$(MYPY) plugins/report-ai-study-pipeline/skills/*/scripts/*.py \
		--ignore-missing-imports --no-error-summary 2>&1 | tail -20 || true
	@printf "$(G)✓ Skill typecheck (informational) done$(N)\n"

security:
	@$(UV) run pip-audit
	@printf "$(G)✓ Security audit passed$(N)\n"

chat-smoke:
	@$(PYTHON) -m pytest tests/test_production_smoke.py
	@printf "$(G)✓ Chat smoke passed$(N)\n"

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

check-study-knowledge: ## Diff published study_variable_map.yaml against config/study_knowledge.yaml
	@PUBLISHED="output/$(STUDY)/llm_source/study_metadata/study_variable_map.yaml"; \
	SOURCE="config/study_knowledge.yaml"; \
	if [ ! -f "$$SOURCE" ]; then \
		printf "$(R)ERROR: source not found: $$SOURCE$(N)\n"; exit 1; \
	fi; \
	if [ ! -f "$$PUBLISHED" ]; then \
		printf "$(Y)SKIP: published file not found: $$PUBLISHED (run the pipeline first)$(N)\n"; exit 0; \
	fi; \
	if diff -q "$$SOURCE" "$$PUBLISHED" >/dev/null 2>&1; then \
		printf "$(G)✓ study_variable_map.yaml matches config/study_knowledge.yaml$(N)\n"; \
	else \
		printf "$(R)✗ Drift detected between config/study_knowledge.yaml and $$PUBLISHED$(N)\n"; \
		diff "$$SOURCE" "$$PUBLISHED" || true; \
		exit 1; \
	fi

clean:
	@find scripts tests docs/sphinx -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	@find scripts tests docs/sphinx -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@find . -maxdepth 1 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -maxdepth 1 -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov docs/sphinx/_build 2>/dev/null || true
	@if [ -d ".logs" ]; then find .logs/ -type f -mtime +7 -delete 2>/dev/null || true; fi
	@printf "$(G)✓ Caches, sessions, stale logs cleaned$(N)\n"

clean-legacy: ## Phase 5b: write pre-delete manifest, delete legacy output dirs
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
