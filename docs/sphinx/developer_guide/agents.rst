Agent Instructions (for AI Coding Assistants)
=============================================

This page is the authoritative briefing for AI coding assistants
(Claude Code, Copilot CLI, Codex, Gemini CLI) working on this
repository. It supersedes the historical ``AGENTS.md`` at the repo
root, which has been retired (per the directive "keep only README +
Sphinx + IRB dossier").

The remainder of this page is organised the way an assistant's
context-builder reads it: orientation → conventions → rules.

Orientation
-----------

Privacy-first, local-first AI Assistant system for clinical research
data. The PHI scrubber (Step 1.6) is an *honest-broker catalog* with
eight action classes — keep / birthdate / drop / cap / generalize /
suppress_small_cell / date / id — evaluated in strict priority order
against ~200 Indo-VAP-calibrated rules. See
:mod:`scripts.security.phi_scrub` and ``scripts/security/phi_scrub.yaml``.
The HMAC key lives at ``~/.config/report_ai_portal/phi_key`` (outside
this repo, never read by agent code).

**Four-tier zone model:**

* **RED** — ``data/raw/``: raw clinical inputs.
* **AMBER** — ``tmp/{STUDY}/``: secure staging (mode 0700, umask 0077,
  zero-fill teardown; optional tmpfs via
  ``REPORTALIN_TMPFS_STAGING=1``).
* **GREEN** — ``output/{STUDY}/trio_bundle/`` (PHI-free artifacts) +
  ``output/{STUDY}/agent/`` (the agent's own state). These two zones
  form the LLM's read surface, enforced by
  :func:`scripts.ai_assistant.file_access.validate_agent_read`.
* **GREEN-PROTECT** — the agent tool boundary: PHI regex gate plus
  k-anonymity and l-diversity for row-level results before the LLM can
  answer.
* **AUDIT envelope** — ``output/{STUDY}/audit/``: counts-only IRB
  evidence, hard-rejected for the agent.

Plus a fifth, **out-of-zone** tier: ``snapshots/{STUDY}/`` at the repo root holds a version-controlled
cleaned trio bundle baseline for the PDF orchestrator's per-PDF
fallback. **The LLM cannot read it.**

See :doc:`architecture` for the full architecture. The IRB-grade
benchmark lives outside the Sphinx tree at
``docs/irb_dossier/conformance_matrix.md``.

Quick reference
---------------

.. code-block:: bash

   make sync          # Install all deps (uv sync --all-groups)
   make test          # Deterministic subset excluding AI Assistant construction smokes
   make test-all      # Full suite including AI Assistant construction smokes
   make lint          # ruff check + format
   make ci            # lint → typecheck → test
   make chat          # Launch Streamlit web UI
   make chat-cli      # Launch CLI REPL
   make pipeline      # Full data pipeline (dict → datasets + pdfs → variables.json)

Architecture (two-world)
------------------------

**World 1 — Deterministic Pipeline** (``main.py`` →
``scripts/extraction/``, ``scripts/security/``, ``scripts/utils/``):

The three extraction legs (dictionary, datasets, PDFs) write into a
transient staging workspace at ``tmp/{STUDY_NAME}/``. The three legs
run **in parallel** on a 3-worker
``concurrent.futures.ThreadPoolExecutor``; the cleanup chain (PHI
scrub / dataset cleanup / cleanup propagation) and Publish + Variables
are sequential after the join.

Every extracted row gets a full ``_provenance`` dict (raw_sha256,
pipeline_version, extraction_engine, source_file, sheet_name,
row_index, study_name, extraction_utc).
:func:`scripts.security.phi_scrub.run_scrub` (Step 1.6) scrubs staged
datasets in place via the eight action classes in strict priority
order **BEFORE** any audit is written so no raw PHI lands in
``output/``. ``dataset_cleanup`` (Step 1.7) runs against staged
datasets and emits ``audit/dataset_cleanup_report.json``.
:func:`scripts.extraction.cleanup_propagation.run_propagation`
(Step 1.8) reads the dataset audit, computes the pruning set, and
rewrites staged dictionary + PDF artifacts. ``_publish_staging``
atomically renames staging → ``trio_bundle/`` (per-leg, copytree
fallback across filesystems).
:func:`scripts.extraction.build_variables_reference.build_variables_reference`
runs after publish. **Step 4** emits ``audit/lineage_manifest.json``
pairing every raw input (SHA-256) with every published trio artifact
(SHA-256). On success, staging is **securely removed** (overwrite +
fsync + unlink); on failure, ``tmp/{STUDY_NAME}/`` is preserved for
operator inspection.

**PDF extraction:** the wizard's "Load Study" button selects the orchestrator path
(:mod:`scripts.extraction.pdf_pipeline`). pdfplumber extracts text
locally; the text is PHI-redacted; only redacted text reaches the LLM;
the response is re-scrubbed and merged with the code candidate. When
the LLM tier is unavailable for any reason, the orchestrator falls
back per-PDF to ``snapshots/{STUDY}/pdfs/`` (the tracked baseline).
The legacy raw-PDF API path (:mod:`scripts.extraction.extract_pdf_data`)
is the CLI default and is gated by the two-part
``REPORTALIN_PDF_PHI_FREE`` operator attestation.

**World 2 — AI Assistant** (``scripts/ai_assistant/``):
LangGraph ReAct agent with 12 tools for querying study data. Never
accesses raw data.

**Output structure:**
``output/{STUDY_NAME}/trio_bundle/{datasets,pdfs,dictionary,variables.json}``,
``audit/{dataset,dictionary,pdfs}_cleanup_report.json`` +
``audit/phi_scrub_report.json`` + ``audit/lineage_manifest.json`` +
``audit/telemetry/events.jsonl``,
``agent/{analysis,conversations,restore_points}/``; transient staging
sibling: ``tmp/{STUDY_NAME}/{datasets,dictionary,pdfs}/``.

**Two snapshot tiers:**

1. **Tracked baseline** at
   ``snapshots/{STUDY_NAME}/{datasets,dictionary,pdfs,variables.json}``
   — version-controlled, maintainer-curated, single per-study cleaned
   trio bundle. The PDF orchestrator reads it as the per-PDF fallback.
   **LLM is forbidden from reading it.** Maintainer protocol: see
   :doc:`operations`.
2. **Operator restore points** at
   ``output/{STUDY_NAME}/agent/restore_points/<name>/`` — gitignored,
   multi-named, agent-writable. Crash recovery only; never read by the
   pipeline.

**Wizard step 2:** two top-level buttons — *Use
Existing Study* (skip pipeline; trust the live ``trio_bundle/``) and
*Load Study* (run the pipeline subprocess; orchestrator falls back to
the tracked snapshot baseline per-PDF when the LLM tier is
unavailable).

**PHI key:** sidecar at ``~/.config/report_ai_portal/phi_key``
(resolved via ``config.PHI_KEY_PATH``, overridable with
``XDG_CONFIG_HOME``). Mode must be ``0600``. Missing = hard-fail.
Bootstrap via ``python -m scripts.security.phi_scrub bootstrap-key``.
Key rotation = full re-ingestion.

Tech stack
----------

* **Python 3.11+**, **uv** package manager (required)
* **Ruff** linter (line-length=100, see ``pyproject.toml [tool.ruff]``)
* **MyPy** type checker (``ignore_missing_imports=true``)
* **Pytest** (``tests/``, ``@pytest.mark.slow`` for heavy tests)
* **LangChain/LangGraph** for AI Assistant agent, **Streamlit ≥1.38, <2.0**
  for web UI
* Custom type stubs in ``typings/`` for google, anthropic

Critical conventions
--------------------

Security zones (MUST follow)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Never access** ``data/raw/`` from agent code — only
  ``output/{STUDY}/trio_bundle/``.
* Always call :func:`scripts.ai_assistant.file_access.validate_agent_read`
  or ``validate_agent_write`` before any file I/O in tools. This is
  the unified chokepoint — accepts only ``trio_bundle/`` + ``agent/``
  paths and rejects audit, telemetry, staging, raw, and arbitrary
  filesystem paths with ``ZoneViolationError``.
* Route every free-text tool return through
  :func:`scripts.ai_assistant.phi_safe.guard_text` or wrap the tool
  with ``@phi_safe_return``.
* When surfacing row-level data, call
  :func:`scripts.security.kanon_gate.guard_rows_with_kanon_and_ldiv`
  first — k=5 + l=2. The gate suppresses responses when any
  quasi-identifier equivalence class has fewer than k members or when
  l-diversity (l=2) on the sensitive attribute is violated.
* When writing pipeline code that logs subject data, install the PHI
  log redactor via
  :func:`scripts.utils.log_hygiene.install_phi_redactor` so raw
  ``SUBJID`` / dates / emails / Aadhaar / phone never land in
  ``.logs/*.log``.

Conversational-shortcut guard on fuzzy search tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Greetings / acknowledgements / queries shorter than 3 chars are
  short-circuited *inside* ``search_variables``,
  ``find_variable_candidates``, ``search_pdf_context`` via
  ``_query_looks_conversational`` in
  ``scripts/ai_assistant/agent_tools.py``. The tool returns a refusal
  (``_CONVERSATIONAL_REFUSAL_MESSAGE``) instead of surfacing noisy
  fuzzy-substring hits.
* Paired with a CONVERSATIONAL WORLD section at the top of
  ``scripts/ai_assistant/agent_prompts.py`` that tells the LLM to
  answer greetings / small-talk without any tool call.
* This is UX hygiene, **not** a security control.
  ``phi_safe.guard_user_prompt`` still runs on every prompt at UI +
  CLI entry; this guard operates inside the tool so a retry-happy
  agent that tries to call it anyway gets a clean refusal rather than
  a name-variable paraphrase.
* When adding a new fuzzy search tool, call
  ``_query_looks_conversational(query)`` and return
  ``_CONVERSATIONAL_REFUSAL_MESSAGE`` on ``True``. Covered by
  ``tests/test_agent_conversational_guard.py``.

Prompt-injection + at-rest defences
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Input-side gate.** Every researcher prompt must pass
  :func:`scripts.ai_assistant.phi_safe.guard_user_prompt` before the
  LLM is invoked. Already wired at ``ui/chat.py`` + ``cli.py``.
* **Untrusted text must be wrapped.** Any text surfaced from outside
  the agent's control (PDF extracts, dictionary free-text, external
  vocab) must pass through
  :func:`scripts.ai_assistant.phi_safe.sanitise_untrusted_snippet`
  before it reaches the LLM. Already applied inside
  ``search_pdf_context``.
* **At-rest redaction.** Any surface that persists user-generated
  content (conversation JSONs, exports, future telemetry sinks) must
  run content through
  :func:`scripts.ai_assistant.phi_safe.redact_phi_in_text`. Already
  wired at ``conversations.py``'s save / export branches.
* **Traceback surfaces.** Tool error returns, UI error expanders, and
  telemetry error payloads must sanitise with
  :func:`scripts.ai_assistant.phi_safe.sanitise_traceback`. Already
  wired at ``run_study_analysis`` + ``streaming.py``.
* **Refused-prompt placeholder.** When ``guard_user_prompt`` refuses,
  the persisted conversation must store a category-tagged placeholder
  (e.g. ``"[PHI-REFUSED prompt — AADHAAR]"``), **not** the raw prompt.
* Adding a new agent tool = ``@tool`` → ``@phi_safe_return`` → open
  with ``validate_agent_read(...)`` (or ``validate_agent_write(...)``).
  Any deviation fails ``tests/test_agent_tools_phi_safe.py`` +
  ``tests/test_file_access.py``.

KeyStore
~~~~~~~~

* The Streamlit wizard's step 1 routes the pasted API key into
  :mod:`scripts.ai_assistant.keystore` (an in-memory ``KeyStore``
  registry) and scrubs the corresponding ``*_API_KEY`` from
  ``os.environ``.
* Keys are re-injected only into the short-lived pipeline subprocess
  via :meth:`KeyStore.env_for_subprocess`.
* Every LLM client constructor (``ChatAnthropic``, ``ChatOpenAI``,
  ``ChatGoogleGenerativeAI``, etc.) takes an explicit ``api_key=``
  kwarg sourced from the KeyStore — no environment lookup at
  construction time.

Sandbox
~~~~~~~

``run_python_analysis`` runs in an isolated subprocess. See
:doc:`sandbox`. Layered protections include subprocess isolation,
``RLIMIT_AS`` / ``RLIMIT_NPROC`` / ``RLIMIT_CPU`` rlimits, in-child
AST + import + dunder + builtin guards, wall-clock timeout, output
cap, figure cap. The generated ``.py`` file is persisted to
``output/{STUDY}/agent/analysis/{ts}.py`` for operator reproduction.

Config
~~~~~~

All paths and settings come from ``config.py`` (env vars + YAML
overlay from ``config/config.yaml``). Never hardcode paths — use
``config.TRIO_BUNDLE_DIR``, ``config.TMP_DIR``, etc.

Key flags: ``STUDY_NAME``, ``LOG_LEVEL``, ``LOG_VERBOSE`` (see
``.env.example``).

Imports
~~~~~~~

* Use ``from __future__ import annotations`` in all modules.
* Lazy-import optional deps (streamlit, langchain) inside functions.
* First-party packages: ``scripts``, ``config``.

Agent tools
~~~~~~~~~~~

Tools live in ``scripts/ai_assistant/agent_tools.py`` as
``@tool``-decorated functions. The docstring becomes the
agent-visible description. All tools are collected in ``ALL_TOOLS``
list. Use ``tool_cache`` for memoization.

Web UI
~~~~~~

* ``scripts/ai_assistant/web_ui.py`` is the main Streamlit app.
* UI modules split into ``scripts/ai_assistant/ui/`` (streaming,
  conversations, providers, session, wizard).
* Sidebar JS in ``scripts/ai_assistant/ui/assets/bridge.js`` — uses
  ``document`` (not ``window.parent.document``).
* Use ``st.iframe()`` for injected JS bridge surfaces so the hidden
  bridge stays isolated and compatible with Streamlit's current runtime.

Tests
~~~~~

* Fixtures in ``tests/conftest.py`` — use ``tmp_path`` +
  ``monkeypatch_config`` to isolate.
* Synthetic data helpers: ``_fake_records(n)``, ``synthetic_excel()``.
* Tests requiring LLM/langchain are excluded from ``make test``
  (included in ``make test-all``).
* Zone markers are patched via ``monkeypatch`` in fixtures.

Web UI architecture
-------------------

The Streamlit web UI implements a Claude Desktop-style dark design
language. It is production-ready with a setup wizard, conversation
history, model switching, and interactive analysis charts.

UI edit-safe files
~~~~~~~~~~~~~~~~~~

Only these paths may be touched by UI work:

* ``scripts/ai_assistant/web_ui.py``
* ``scripts/ai_assistant/ui/{chat,conversations,model_policy,providers,shell,state,streaming,wizard}.py``
* ``scripts/ai_assistant/ui/assets/{theme.css, bridge.js, fonts/}``
* ``.streamlit/config.toml``
* ``pyproject.toml`` (kaleido pin only)

UI edit-forbidden files (hard stop)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``config.py``
* ``scripts/ai_assistant/agent_graph.py`` (read-only; use the three
  entry points only: ``stream_query``, ``invoke_query``,
  ``reset_agent``)
* ``scripts/ai_assistant/agent_tools.py``, ``agent_prompts.py``,
  ``analytical_engine.py``, ``study_knowledge.py``, ``file_access.py``,
  ``tool_cache.py``, ``phi_safe.py``, ``cli.py``
* Everything under ``scripts/extraction/``, ``scripts/security/``,
  ``scripts/utils/``

Design token system
~~~~~~~~~~~~~~~~~~~

All design tokens in ``scripts/ai_assistant/ui/assets/theme.css`` use
the ``--rpln-*`` namespace (canonical primary ``:root`` block). New
CSS must use these — never the deprecated backward-compat scales.

Categories: colors, spacing, type, line-height, radius, z-index,
easing, durations. ``--rpln-accent-orange`` is a compat alias — use
``--rpln-accent`` instead.

Regression gate (run before every UI commit)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   uv run pytest tests/ -x -q

Any red test in a non-UI module = hard stop. Revert the wave, do not
patch the test.

Key files
---------

.. list-table::
   :header-rows: 1

   * - Area
     - Files
   * - Entry point
     - ``main.py``, ``config.py``
   * - Pipeline
     - ``scripts/extraction/dataset_pipeline.py``,
       ``scripts/extraction/build_variables_reference.py``,
       ``scripts/extraction/extract_pdf_data.py``,
       ``scripts/extraction/pdf_pipeline.py`` (orchestrator)
   * - PHI scrub + catalog
     - ``scripts/security/phi_scrub.py``,
       ``scripts/security/phi_scrub.yaml``
   * - PHI gate + k-anon + allowlist
     - ``scripts/security/phi_gate.py``,
       ``scripts/security/kanon_gate.py``,
       ``scripts/security/phi_allowlist.py``,
       ``scripts/security/phi_patterns.py``
   * - Phase-0 staging hardening
     - ``scripts/utils/secure_staging.py``
   * - Integrity + governance
     - ``scripts/utils/lineage.py``,
       ``scripts/utils/log_hygiene.py``
   * - Zone guards
     - ``scripts/security/secure_env.py``
   * - AI Assistant agent
     - ``scripts/ai_assistant/agent_graph.py``,
       ``scripts/ai_assistant/agent_tools.py``,
       ``scripts/ai_assistant/agent_prompts.py``,
       ``scripts/ai_assistant/phi_safe.py``,
       ``scripts/ai_assistant/keystore.py`` (KeyStore)
   * - Sandbox subprocess
     - ``scripts/ai_assistant/sandbox/{replicate,limits,runner}.py``
       (subprocess sandbox)
   * - Telemetry
     - ``scripts/utils/telemetry.py``
   * - Web UI
     - ``scripts/ai_assistant/web_ui.py``,
       ``scripts/ai_assistant/ui/``
   * - Config
     - ``config.py``, ``config/config.yaml``,
       ``config/study_knowledge.yaml``
   * - IRB benchmark dossier
     - ``docs/irb_dossier/conformance_matrix.md``,
       ``docs/irb_dossier/executive_summary.md``,
       ``docs/irb_dossier/phi_walkthrough.md``

Documentation
-------------

* **Architecture** — :doc:`architecture`
* **Testing** — :doc:`testing`
* **Contributing** — :doc:`contributing`
* **Operations** — :doc:`operations` (snapshot maintenance lives here)
* **Sandbox** — :doc:`sandbox`
* **Data pipeline (user view)** — :doc:`../user_guide/data_pipeline`
