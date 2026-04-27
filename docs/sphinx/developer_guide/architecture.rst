Architecture
============

This document describes the active technical architecture of the RePORT AI Portal
runtime.

System Overview
---------------

RePORT AI Portal is a **single-study, privacy-first, local-first AI Assistant system**. It
processes one fixed study under ``data/raw/{STUDY_NAME}/`` and is built as a
modular pipeline with clear separation of concerns. The user provides the LLM;
the system provides study-specific extraction, promotion, agentic tools,
and grounded answers. PHI scrubbing happens **inside the pipeline** at Step 1.6
on AMBER-staged JSONL (eight-action catalog, rule + allowlist) — the operator
is **not** required to pre-scrub raw data; raw inputs flow through the
honest-broker boundary unchanged and are never read by the LLM agent.

.. code-block:: text

   ┌────────────────────────────────────────────────────────────────────┐
   │                         RePORT AI Portal Runtime                        │
   ├────────────────────────────────────────────────────────────────────┤
   │                                                                    │
   │   Config / Paths / Logging / Version / Telemetry                   │
   │                                                                    │
   │   data/raw/{STUDY_NAME}/                                           │
   │        │                                                           │
   │        ├── data_dictionary/ ──→ extraction.load_dictionary         │
   │        ├── datasets/        ──→ extraction.dataset_pipeline         │
   │        └── annotated_pdfs/  ──→ extraction.extract_pdf_data        │
   │                                   │                                │
   │                                   ▼                                │
   │            extraction.build_variables_reference                     │
   │                                   │                                │
   │                                   ▼                                │
   │               ai_assistant.agent_graph (ReAct agent)                      │
   │    create_react_agent → 12 structured-data tools                 │
   │                                   │                                │
   │                                   ▼                                │
   │                  ai_assistant.cli / ai_assistant.web_ui (REPL / Streamlit)          │
   │                                                                    │
   │   output/{STUDY_NAME}/                                             │
   │      trio_bundle/  audit/  agent/                                   │
   │      (telemetry sinks under audit/telemetry/)                       │
   │                                                                    │
   └────────────────────────────────────────────────────────────────────┘

Core Components
---------------

Configuration System
~~~~~~~~~~~~~~~~~~~~

**Primary locations:** ``config.py``, ``config/config.yaml``, environment variables

**Responsibilities:**

* define canonical study, raw zone (``RAW_DIR``), AMBER staging
  (``STAGING_DIR``), GREEN trio bundle (``TRIO_BUNDLE_DIR``), agent
  state (``AGENT_STATE_DIR``), audit (``STUDY_AUDIT_DIR`` /
  ``TELEMETRY_DIR``), and HMAC sidecar key paths
* provide runtime defaults for providers and logging
* support environment-variable overrides for secrets and deployment-local settings
* keep path construction centralized so the pipeline, API, and app use the same runtime contract

Logging System
~~~~~~~~~~~~~~

**Location:** ``scripts/utils/logging_system.py``

**Responsibilities:**

* initialize repository logging
* keep operational logs structured and timestamped
* avoid logging raw sensitive values in normal runtime flows

Pipeline Modules
----------------

Dictionary Loader
~~~~~~~~~~~~~~~~~

**Location:** ``scripts/extraction/load_dictionary.py``

**Purpose:** Discover, parse, and normalize study data dictionary or mapping files
into JSONL artifacts for downstream schema and registry generation.

**Supported formats:** ``.xlsx``, ``.xls``, ``.csv``

**Primary responsibilities:**

* discover supported dictionary files in ``data/raw/{STUDY_NAME}/data_dictionary/``
* parse workbook sheets or CSV content
* detect table boundaries where possible
* inject provenance metadata
* write deterministic JSONL outputs into the clean study tree

**Output location:** ``output/{STUDY_NAME}/trio_bundle/dictionary/``

Dataset Extraction
~~~~~~~~~~~~~~~~~~

**Location:** ``scripts/extraction/dataset_pipeline.py``

**Purpose:** Extract structured row data from tabular study source files into a
sensitive temporary JSONL workspace for downstream promotion into the Trio bundle.

**Supported formats:** ``.xlsx``, ``.xls``, ``.csv``

**Primary responsibilities:**

* discover supported dataset files in ``data/raw/{STUDY_NAME}/datasets/``
* read rows and preserve source provenance
* emit ``original/`` and duplicate-column-cleaned ``cleaned/`` JSONL views
* mark the workspace as sensitive with explicit provenance and marker files
* promote clean extracted output into the Trio bundle

**Important boundary rule:** dataset extraction output is still sensitive and is
not part of the permanent clean output surface.

PDF Extraction
~~~~~~~~~~~~~~

**Location:** ``scripts/extraction/extract_pdf_data.py``

**Purpose:** Extract structured content from annotated study PDFs into JSONL
with provenance suitable for schema generation, tool-based querying via
``search_pdf_context``, and citation back to the source page. The extracted
JSONL lives in the trio bundle's ``pdfs/`` sub-zone and is queried directly
by the agent — no vector index, no chunking, no embedding step.

**Primary responsibilities:**

* read PDFs from ``data/raw/{STUDY_NAME}/annotated_pdfs/``
* extract page text and metadata
* detect form codes, form titles, and section headers where possible
* extract tables or form-field-like structures when available
* write flat JSONL outputs to ``output/{STUDY_NAME}/trio_bundle/pdfs/``

**Backend (current runtime, v0.20.0):**

* The legacy raw-PDF API path (``scripts/extraction/extract_pdf_data.py``)
  uses ``pypdf`` for text extraction when the
  ``REPORTALIN_PDF_PHI_FREE`` two-part attestation gate is satisfied.
* The two-way PDF orchestrator
  (``scripts/extraction/pdf_pipeline.py``, shipped in PR #15) uses
  ``pdfplumber`` as the always-on code path. Extracted text is
  PHI-redacted before any LLM call; the LLM response is merged with
  the code candidate. When the LLM tier is unavailable, the orchestrator
  falls back to the version-controlled snapshot baseline at
  ``snapshots/{STUDY}/pdfs/`` per-PDF.

Dataset Promotion
~~~~~~~~~~~~~~~~~

**Location:** ``scripts/extraction/dataset_pipeline.py``

**Purpose:** Promote pre-scrubbed dataset JSONL from the temporary extraction
workspace into the study clean zone.

**Primary responsibilities:**

* consume extracted dataset JSONL
* write clean dataset outputs into the study clean zone
* emit provenance metadata alongside the promoted artifacts

PHI Scrub (Step 1.6)
~~~~~~~~~~~~~~~~~~~~

**Location:** ``scripts/security/phi_scrub.py``;
config ``scripts/security/phi_scrub.yaml``

**Purpose:** 8-action honest-broker PHI pass over staged datasets — keep /
birthdate / drop / cap / generalize / suppress_small_cell / date jitter
(SANT method) / id pseudonymize (HMAC-SHA256) — **before** any audit
output is written, so raw PHI never lands in ``output/``.

**Primary responsibilities:**

* load the HMAC key from the sidecar ``~/.config/report_ai_portal/phi_key`` (mode
  ``0600``); hard-fail on missing/wrong-mode/non-hex key
* scrub every ``tmp/{STUDY_NAME}/datasets/*.jsonl`` file in place
* pseudonymize ID fields (configurable regex list) via
  ``HMAC-SHA256(key, id)[:12]`` → ``SUBJ_<12hex>``
* shift date fields (configurable regex list) by a per-subject deterministic
  offset in ``[-max_days, +max_days]`` derived from
  ``HMAC-SHA256(key, subject_id)`` — preserves intra-subject date intervals
* apply the configured ``compliance_posture``: ``safe_harbor`` (default) drops
  the birthdate field; ``limited_dataset`` shifts it alongside other dates and
  requires an authority note at ``authorities/phi_limited_dataset.md``
* quarantine rows with missing ``subject_id`` to
  ``tmp/{STUDY_NAME}/quarantine/{file}.jsonl`` (fail-fast if quarantine rate
  exceeds the configured threshold)
* write ``_phi_scrubbed: "v1"`` row marker + sentinel
  ``tmp/{STUDY_NAME}/.phi_scrub_complete`` for idempotent re-runs
* emit ``output/{STUDY_NAME}/audit/phi_scrub_report.json`` with the list of
  scrubbed fields per dataset (counts only — no raw values)

.. note::

   The HMAC key lives **outside the repo** at
   ``~/.config/report_ai_portal/phi_key`` (overridable via ``XDG_CONFIG_HOME``).
   Bootstrap via ``python -m scripts.security.phi_scrub bootstrap-key``. Key
   rotation requires full re-ingestion — all pseudonyms and date offsets
   change. Agent code never reads this path.

Cleanup Propagation and Publish
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Location:** ``scripts/extraction/cleanup_propagation.py`` (propagation);
``main.py`` (``_publish_staging``, ``_publish_leg``, ``_prepare_staging``,
``_cleanup_staging``)

**Purpose:** Propagate variable-drop decisions from the dataset cleanup audit
into the staged dictionary and PDF artifacts, then atomically publish all three
legs to ``trio_bundle/``.

**Primary responsibilities:**

* read ``output/{STUDY_NAME}/audit/dataset_cleanup_report.json`` produced by
  ``dataset_cleanup``
* compute the pruning set: variables that were removed from datasets and do not
  survive in any remaining dataset (case-insensitive, provenance fields excluded)
* rewrite staged dictionary JSONL in ``tmp/{STUDY_NAME}/dictionary/`` to remove
  entries for pruned variables (side-effect only — no audit report, dictionary
  leg carries no PHI)
* rewrite staged PDF JSONL in ``tmp/{STUDY_NAME}/pdfs/`` to remove entries for
  pruned variables (side-effect only — no audit report, PDF leg carries no PHI)
* ``_publish_staging`` iterates the three staging legs and calls ``_publish_leg``
  for each, which attempts an atomic ``os.rename`` and falls back to
  ``shutil.copytree`` when source and destination are on different filesystems
* ``build_variables_reference`` runs after publish and reads the now-published
  ``trio_bundle/``
* on success, ``_cleanup_staging`` removes ``tmp/{STUDY_NAME}/``; on failure the
  staging root is left in place for operator inspection

.. note::

   ``tmp/{STUDY_NAME}/`` is a **transient** workspace. It is not a durable
   artifact and is not committed to version control. Its presence after a failed
   run is intentional — operators can inspect staged files before retrying.

Supporting Services
-------------------

AI Assistant Agent Layer
~~~~~~~~~~~~~~~~~~~~~~~~

**Primary location:** ``scripts/ai_assistant/``

**Purpose:** Autonomous ReAct agent for grounded question answering with
12 structured-data tools, sandboxed code execution, and privacy-aware prompts.

**Key modules:**

* ``agent_graph.py``: ReAct agent via ``create_react_agent`` with 12 @tool functions
* ``agent_prompts.py``: system prompt with baked-in disclosure rules
* ``agent_tools.py``: zone-guarded tool registry (search_variables, get_variable_details, list_forms, get_form_variables, query_dataset, get_dataset_stats, run_python_analysis, get_study_overview, cross_reference_variables, run_study_analysis, find_variable_candidates, search_pdf_context)
* ``tool_cache.py``: per-session LRU result cache
* ``cli.py``: interactive REPL with feedback commands
* ``web_ui.py``: Streamlit web interface

**LLM provider:** configured via ``config.LLM_PROVIDER`` and ``config.LLM_MODEL``,
using ``langchain.chat_models.init_chat_model()`` for provider-agnostic initialization.
Supports OpenAI, Anthropic, Google Generative AI, and Ollama.

Analytical Engine
~~~~~~~~~~~~~~~~~

**Location:** ``scripts/ai_assistant/analytical_engine.py``, ``scripts/ai_assistant/study_knowledge.py``

**Responsibilities:**

* provide deterministic epidemiological analysis (no LLM involvement in computations)
* build analytic cohorts from multiple JSONL datasets via knowledge-base-driven joins
* run univariate, multivariate (backward stepwise), and interaction logistic regressions
* generate publication-quality violin and scatter plots
* produce narrative interpretations with caveats and clinical context

**Knowledge Base:** ``config/study_knowledge.yaml`` defines the ground truth
mapping between human concepts (e.g., "smoking") and actual dataset columns,
value encodings, join strategies, and outcome definitions.

**Orchestration:** The agent graph (``scripts/ai_assistant/agent_graph.py``) supports
hybrid orchestration — single-agent for capable models (>14B / API) and
multi-agent fan-out for smaller local models (≤14B).

File-Access Validator
~~~~~~~~~~~~~~~~~~~~~

**Location:** ``scripts/ai_assistant/file_access.py``

**Responsibilities:**

* single chokepoint for agent-world file I/O
* ``validate_agent_read`` / ``validate_agent_write`` / ``is_agent_readable``
* resolves each path with ``os.path.realpath`` and confines reads to
  ``trio_bundle/`` + ``agent/`` (plus the repo-tracked
  ``config/study_knowledge.yaml`` read-allowlist)
* confines writes to ``agent/`` — audit, telemetry, staging, raw, and
  arbitrary filesystem paths raise ``ZoneViolationError``
* the routing layer (question → tool) is delegated to the LLM itself
  via the system prompt — no keyword-based Python classifier

Telemetry
~~~~~~~~~

**Primary location:** ``scripts/utils/telemetry.py``

**Purpose:** Append-only event logging with conservative field masking for
agent observability. Implements ``BaseCallbackHandler`` for
LangChain/LangGraph integration. Events are appended atomically in JSONL format.

Data Flow
---------

End-to-End Runtime Flow
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   data/raw/{STUDY_NAME}/data_dictionary/ ──┐  ┐
                                            ├──→ load_dictionary ────────┐ │
   data/raw/{STUDY_NAME}/datasets/ ─────────┼──→ dataset_pipeline ────────┤ ├ Phase 1 PARALLEL
                                            │                            │ │ (3-worker pool;
   data/raw/{STUDY_NAME}/annotated_pdfs/ ───┴──→ pdf_pipeline ───────────┤ ┘ join → cleanup)
                                              (orchestrator: pdfplumber  │
                                              code path + redacted-text  │
                                              LLM merge + snapshot       │
                                              fallback at                │
                                              snapshots/{STUDY}/pdfs/)   │
                                                                         │
                                               (all legs → staging)      ▼
                                          tmp/{STUDY_NAME}/{datasets,dictionary,pdfs}/
                                                                         │
                                                phi_scrub.run_scrub (Step 1.6 — date jitter +
                                                   ID pseudonymization on staged datasets;
                                                   emits phi_scrub_report.json)
                                                                         │
                                                  dataset_cleanup (emits dataset audit)
                                                                         │
                                              cleanup_propagation (prunes dict+pdf in staging,
                                                  emits dict+pdf audits)
                                                                         │
                                                _publish_staging (atomic rename → trio_bundle/)
                                                                         │
                                                                         ▼
                                       output/{STUDY_NAME}/trio_bundle/...
                                                                         │
                                              ai_assistant.agent_graph (ReAct)
                                     create_react_agent → 12 tools
                                                         │
                                                         ▼
                                          ai_assistant.cli / ai_assistant.web_ui

Security Boundaries
-------------------

Zone Enforcement
~~~~~~~~~~~~~~~~

**Primary location:** ``scripts/security/secure_env.py``

The zone guard enforces the runtime boundary model:

* raw study data is not allowed into retrieval/indexing paths
* processed output must remain inside the output-zone contract
* clean-zone requirements are enforced where needed

Design Principles
-----------------

Modularity
~~~~~~~~~~

Each component has a narrow, explicit responsibility and a clear runtime role.

Determinism where possible
~~~~~~~~~~~~~~~~~~~~~~~~~~

Variable validation, warnings, promotion rules, and zone enforcement are code-driven
rather than delegated to the LLM.

Security-first boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~

Raw study data, sensitive extracted dataset output, and clean study artifacts
are treated as distinct runtime zones.

Testability
~~~~~~~~~~~

Core modules are structured so extraction, promotion, retrieval, query
orchestration, and UI-adjacent helpers can be tested independently.

Technology Stack
----------------

Core Libraries
~~~~~~~~~~~~~~

* **Python 3.11+**: runtime language
* **pandas**: tabular parsing and transformation
* **openpyxl**: Excel workbook handling
* **xlrd**: legacy ``.xls`` handling
* **pypdf**: lightweight PDF text/metadata path (legacy raw-PDF API path in
  ``scripts/extraction/extract_pdf_data.py``)
* **pdfplumber**: shipped in PR #15 (v0.19.0) — the always-on code path
  inside the two-way PDF orchestrator
  (``scripts/extraction/pdf_pipeline.py``); paired with a redacted-text
  LLM call merged via ``_merge``, with per-PDF fallback to
  ``snapshots/{STUDY}/pdfs/``

Retrieval / Agent
~~~~~~~~~~~~~~~~~~~~

* **LangChain**: orchestration framework (core, community, provider packages)
* **LangGraph**: stateful ReAct agent with 12 structured-data tools
* **Streamlit**: web interface for the AI Assistant

Documentation / Developer Tooling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Sphinx**: documentation generation
* **pytest**: test execution
* **ruff**: linting and formatting
* **mypy**: static type checking

Deployment Considerations
-------------------------

Local Runtime Contract
~~~~~~~~~~~~~~~~~~~~~~

The active documented runtime is local-first and single-study only.

Expected source tree:

.. code-block:: text

   data/raw/{STUDY_NAME}/
   ├── datasets/
   ├── annotated_pdfs/
   └── data_dictionary/

   snapshots/{STUDY_NAME}/                     # tracked baseline
   ├── datasets/                               # cleaned + verified, version-controlled,
   ├── dictionary/                             # LLM-INVISIBLE; PDF orchestrator reads
   ├── pdfs/                                   # ``pdfs/{stem}_variables.json`` as
   └── variables.json                          # per-PDF fallback (PR #18)

Expected processed tree:

.. code-block:: text

   output/{STUDY_NAME}/
   ├── trio_bundle/
   ├── audit/                                  # dataset-only (PHI-bearing leg)
   │   ├── dataset_cleanup_report.json
   │   ├── phi_scrub_report.json
   │   └── telemetry/
   │       └── events.jsonl
   └── agent/                                  # analysis / conversations / restore_points

Transient staging root (not a durable artifact):

.. code-block:: text

   tmp/{STUDY_NAME}/
   ├── datasets/
   ├── dictionary/
   ├── pdfs/
   └── quarantine/        # rows with missing subject_id (from phi_scrub)

Resource Notes
~~~~~~~~~~~~~~

* CPU, memory, and disk requirements depend mostly on study size, PDF volume,
  and provider selection.
* Hosted-provider workflows require working provider credentials and network access.
* Local ``ollama`` workflows require a running Ollama server and suitable host resources.

Out-of-Scope / Deferred Areas
-----------------------------

These are not part of the active local architecture contract described here:

* upload-driven multi-study workflows
* HPC / Slurm deployment surfaces
* distributed processing claims
* historical phase-based roadmap promises
