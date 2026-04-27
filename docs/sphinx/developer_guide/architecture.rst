Architecture
============

Rewritten 2026-04-27 against the v0.20.0 code state. The active
technical architecture of the RePORT AI Portal runtime. For the
PHI-handling deep dive see :doc:`phi_architecture`; for the
decisions behind the choices see :doc:`decisions`; for the
operator's view see :doc:`../user_guide/data_pipeline`.

System Overview
---------------

RePORT AI Portal is a **two-world** system. The two worlds run in
separate processes and never share mutable state.

**World 1 — Deterministic Pipeline** (``main.py`` + ``scripts/extraction/`` +
``scripts/security/`` + ``scripts/utils/``).

Reads raw clinical data from ``data/raw/{STUDY}/``, runs three
extraction legs in parallel, scrubs PHI from the dataset leg,
mirrors dataset drops into the dictionary + PDF legs, atomically
publishes per-leg into ``output/{STUDY}/trio_bundle/``, builds a
consolidated ``variables.json``, emits a lineage manifest.
``main.py --pipeline`` is the canonical entry point; ``make
pipeline`` is the Makefile alias; the wizard's "Load Study" button
spawns this as a subprocess.

**World 2 — AI Assistant** (``scripts/ai_assistant/``).

A LangGraph ReAct agent with 12 tools that reads the published
trio bundle and answers researcher queries. Provider-agnostic via
``init_chat_model``; runs against Anthropic / OpenAI / Google /
NVIDIA / Ollama. Never accesses raw data. Three independent gates
on every tool return (PHI regex catalog, k=5 anonymity, l=2
diversity).

The two worlds communicate through the ``output/{STUDY}/`` tree
only:

.. code-block:: text

   World 1 writes:                          World 2 reads:
   - trio_bundle/  (sanitised data)    →   - trio_bundle/  (LLM data surface)
   - audit/        (counts only)            (LLM hard-rejected for audit/)
   - agent/        (state subdirs)     →   - agent/  (LLM session memory)

The Streamlit wizard is the operator's entry point; it routes API
keys through the in-memory KeyStore, spawns the pipeline subprocess
on demand, and then hands off to the agent for chat.

The Five-Tier Zone Model
------------------------

See :doc:`phi_architecture` for the full discussion. Briefly:

.. list-table::
   :header-rows: 1
   :widths: 16 28 56

   * - Tier
     - Path
     - Posture
   * - **RED**
     - ``data/raw/{STUDY}/``
     - Raw, presumed PHI. Read by extraction subprocess only.
   * - **AMBER**
     - ``tmp/{STUDY}/``
     - Per-run scratch. Mode 0700. Securely deleted on success.
   * - **GREEN**
     - ``output/{STUDY}/{trio_bundle,agent}/``
     - LLM read zone. PHI-free.
   * - **GREEN-PROTECT**
     - ``output/{STUDY}/audit/``
     - Counts-only. LLM-rejected.
   * - *out-of-zone*
     - ``snapshots/{STUDY}/``
     - Tracked baseline (LLM-invisible). PDF orchestrator
       per-PDF fallback.

Core Components
---------------

Configuration System
~~~~~~~~~~~~~~~~~~~~

:mod:`config` resolves every path and most behaviour flags from env
vars + a YAML overlay (``config/config.yaml``). Read by every module
that needs a path or knob. See :doc:`../user_guide/configuration` for
the full env-var table; key constants:

* ``STUDY_NAME`` — e.g. ``Indo-VAP``. Pins the single study.
* ``BASE_DIR`` — repo root, used as the anchor for all path
  derivation.
* ``TRIO_BUNDLE_DIR`` — the canonical published-bundle path.
* ``STUDY_SNAPSHOTS_DIR`` — the tracked baseline path
  (``snapshots/{STUDY}/``).
* ``STUDY_RESTORE_POINTS_DIR`` — the gitignored restore-point path
  (``output/{STUDY}/agent/restore_points/``).

Logging System
~~~~~~~~~~~~~~

:mod:`scripts.utils.logging_system` — root-logger setup with a
verbose "tree" mode for ``--verbose`` runs.
:mod:`scripts.utils.log_hygiene` — ``logging.Filter`` that scrubs
API keys + PHI patterns from every log line. Both filters attach
to the root logger so ``World 1`` and ``World 2`` emit scrubbed
logs by default.

Known limitation (PR #18): ``VerboseLogger._indent`` is mutated by
overlapping ``file_processing`` context managers. Under
``--verbose`` mode, parallel-extraction tree output may interleave.
Cosmetic only — log emissions are correct.

Pipeline Modules
----------------

The pipeline is structured as a sequence of step functions in
``main.py``, each importing its operative module from
``scripts/extraction/``, ``scripts/security/``, or
``scripts/utils/``. Steps 0/1/1.5 run in parallel; the cleanup
chain (1.6/1.7/1.8) and Steps 2/3/4/5 are sequential.

Dictionary Loader
~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.load_dictionary.load_study_dictionary`
* **Step:** Step 0
* **Reads:** ``data/raw/{STUDY}/data_dictionary/*.xlsx``
* **Writes:** ``tmp/{STUDY}/dictionary/*.json``
* **PHI posture:** Carries no PHI by design (the dictionary defines
  variables, not subject values).

Dataset Extraction
~~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.dataset_pipeline.process_datasets`
* **Step:** Step 1 (extract)
* **Reads:** ``data/raw/{STUDY}/datasets/*.{xlsx,xls,csv}``
* **Writes:** ``tmp/{STUDY}/datasets/*.jsonl``
* **PHI posture:** Records carry full PHI here. Every record gets
  a ``_provenance`` dict (raw_sha256, pipeline_version,
  extraction_engine, source_file, sheet_name, row_index,
  study_name, extraction_utc).
* **Skip semantics:** Hash-based step cache at
  ``output/{STUDY}/audit/manifests/dataset_processing.json``.

PDF Extraction
~~~~~~~~~~~~~~

Two co-existing paths as of v0.19.0:

* **Orchestrator path** (default):
  :mod:`scripts.extraction.pdf_pipeline`. ``pdfplumber`` code path
  + redacted-text LLM merge + per-PDF fallback to
  ``snapshots/{STUDY}/pdfs/``. **No raw PDF bytes leave the host.**
  See :doc:`data_extraction_pdfs` for the per-step pipeline.
* **Legacy raw-PDF API path:**
  :mod:`scripts.extraction.extract_pdf_data`. Refused unless the
  operator opts in twice (``REPORTALIN_PDF_PHI_FREE=1`` env flag +
  non-empty ``authorities/phi_free_pdfs.md`` attestation note).

Dispatch happens in
:func:`scripts.extraction.extract_pdf_data.extract_pdfs_to_jsonl` based
on ``REPORTALIN_PDF_EXTRACTION_MODE``. The wizard always sets
``llm`` (orchestrator); the CLI default is unset (legacy gate).

PHI Scrub
~~~~~~~~~

* **Module:** :func:`scripts.security.phi_scrub.run_scrub`
* **Step:** Step 1.6 (BEFORE Step 1.7 cleanup so no raw PHI
  reaches the audit envelope)
* **Reads/writes:** ``tmp/{STUDY}/datasets/*.jsonl`` in place
* **Audit:** ``output/{STUDY}/audit/phi_scrub_report.json``
  (counts-only)
* **Eight action classes:** keep / birthdate / drop / cap /
  generalize / suppress_small_cell / date_jitter / hmac_pseudonymize.
  Configured in ``scripts/security/phi_scrub.yaml`` (~200
  Indo-VAP-calibrated rules).
* **HMAC key:** ``~/.config/report_ai_portal/phi_key`` (mode 0600,
  outside the repo).

Dataset Cleanup
~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.dataset_cleanup.clean_trio_datasets`
* **Step:** Step 1.7
* **Reads/writes:** ``tmp/{STUDY}/datasets/*.jsonl`` in place
* **Audit:** ``output/{STUDY}/audit/dataset_cleanup_report.json``
* Removes junk rows, merges duplicate records, propagates
  Step 1's drop events into the cleanup record.

Cleanup Propagation
~~~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.cleanup_propagation.run_propagation`
* **Step:** Step 1.8
* **Reads/writes:** ``tmp/{STUDY}/{dictionary,pdfs}/`` in place
* **Audit:** ``output/{STUDY}/audit/{dictionary,pdfs}_cleanup_report.json``
* Computes the propagation drop-set from the dataset audit and
  prunes matching rows/keys from the staged dictionary + PDF
  trees. Keeps the published trio bundle internally consistent.

Publish
~~~~~~~

* **Function:** ``_publish_staging`` in ``main.py``
* **Step:** Step 2
* **Atomic per-leg rename** ``tmp/{STUDY}/{leg}/`` →
  ``output/{STUDY}/trio_bundle/{leg}/``. Same-filesystem rename =
  single inode swap; cross-filesystem (e.g. tmpfs staging + disk
  output) falls back to ``shutil.copytree`` + ``shutil.rmtree``.
* **Zone guard:** ``assert_output_zone(trio_dir)`` runs before
  the rename.
* **Pre-publish:** if the destination exists,
  ``secure_remove_tree`` (zero-fill + fsync + unlink) so old
  bytes aren't recoverable.

Variables Reference Builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.build_variables_reference.build_variables_reference`
* **Step:** Step 3 (AFTER publish; reads the populated
  ``trio_bundle/``, not staging)
* **Output:** ``output/{STUDY}/trio_bundle/variables.json`` —
  the consolidated variable schema the agent uses to validate
  variable names in queries.

Lineage Manifest
~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.utils.lineage.emit_lineage_manifest`
* **Step:** Step 4
* **Output:** ``output/{STUDY}/audit/lineage_manifest.json`` —
  pairs every raw input SHA-256 with every published trio
  artifact SHA-256, plus PHI-key fingerprint, compliance posture,
  and pipeline version. **The single artifact an IRB reviewer
  reads to verify the entire raw → scrub → publish chain.**

Output Signpost + AMBER cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Step:** Step 5
* Re-emits ``output/{STUDY}/README.md`` describing the layout.
* On success, ``secure_remove_tree`` over ``tmp/{STUDY}/``. On
  failure, AMBER is preserved for forensic inspection.

Supporting Services
-------------------

AI Assistant Agent Layer
~~~~~~~~~~~~~~~~~~~~~~~~

* :mod:`scripts.ai_assistant.agent_graph` — LangGraph ReAct agent;
  the only module that constructs an LLM client. Provider keys
  flow in via the explicit ``api_key=`` kwarg, sourced from the
  KeyStore (no ``os.environ`` lookup).
* :mod:`scripts.ai_assistant.agent_tools` — 12 ``@tool``-decorated
  functions. ``ALL_TOOLS`` is the canonical list; the
  doc-freshness lint ties prose docs to this list.
* :mod:`scripts.ai_assistant.agent_prompts` — system prompt with
  CONVERSATIONAL WORLD section that tells the LLM to answer
  greetings without tool calls.
* :mod:`scripts.ai_assistant.phi_safe` — agent-side PHI helpers:
  ``phi_safe_return``, ``guard_text``, ``guard_user_prompt``,
  ``sanitise_untrusted_snippet``, ``redact_phi_in_text``,
  ``sanitise_traceback``.
* :mod:`scripts.ai_assistant.file_access` — agent-runtime path
  validator (the canonical chokepoint for every tool's file I/O).
* :mod:`scripts.ai_assistant.keystore` — in-memory API-key
  registry (PR #3, ADR-011).
* :mod:`scripts.ai_assistant.tool_cache` — per-tool memoisation.

Analytical Engine
~~~~~~~~~~~~~~~~~

:mod:`scripts.ai_assistant.analytical_engine` — deterministic
epidemiology helpers (logistic regression, survival, descriptive
stats) called from the ``run_python_analysis`` tool. Pre-loaded
DataFrames come from ``config.TRIO_DATASETS_DIR`` only (GREEN
zone).

Subprocess Sandbox
~~~~~~~~~~~~~~~~~~

* :mod:`scripts.ai_assistant.sandbox.replicate` — public API
  (``run_in_subprocess``).
* :mod:`scripts.ai_assistant.sandbox.runner` — child-process
  entry point; carries the AST + import + dunder + builtin guards.
* :mod:`scripts.ai_assistant.sandbox.limits` — cross-platform
  rlimits.

Generated ``.py`` files persisted to
``output/{STUDY}/agent/analysis/{ts}.py``. See :doc:`sandbox` for
the full layered story.

File-Access Validator
~~~~~~~~~~~~~~~~~~~~~

:mod:`scripts.ai_assistant.file_access` — unified chokepoint that
every agent tool calls before any file I/O. Resolves with
``os.path.realpath``, verifies containment with
``os.path.commonpath``. Reads accept ``trio_bundle/`` ∪ ``agent/``
(plus ``config/study_knowledge.yaml`` via an explicit allowlist).
Writes accept ``agent/`` only. Sandbox writes narrow further to
``agent/analysis/``. Audit, telemetry, staging, raw, and the
snapshot baseline are hard-rejected.

Telemetry
~~~~~~~~~

:mod:`scripts.utils.telemetry` — agent event logger, attached as a
LangChain callback. Lands in
``output/{STUDY}/audit/telemetry/events.jsonl`` (LLM-rejected via
``validate_agent_read``). Non-string event payloads are
force-stringified + masked before write.

Web UI
~~~~~~

* :mod:`scripts.ai_assistant.web_ui` — Streamlit entry.
* :mod:`scripts.ai_assistant.ui.wizard` — three-step setup flow.
  Step 1 = LLM config (KeyStore routing). Step 2 = Data load
  (two-button: Use Existing Study + Load Study). Step 3 = Confirm
  + start chat.
* :mod:`scripts.ai_assistant.ui.chat` — chat surface.
* :mod:`scripts.ai_assistant.ui.streaming` — token stream + error
  expander (with traceback sanitiser).
* :mod:`scripts.ai_assistant.ui.conversations` — at-rest
  conversation persistence with PHI redaction.
* :mod:`scripts.ai_assistant.ui.providers` — provider catalog
  (Anthropic, OpenAI, Google, Ollama, NVIDIA).
* :mod:`scripts.ai_assistant.ui.model_policy` — capability floor
  enforcement on UI selection.

Data Flow
---------

End-to-End Runtime Flow
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   data/raw/{STUDY_NAME}/data_dictionary/ ──┐  ┐
                                            ├──→ load_dictionary ────┐ │
   data/raw/{STUDY_NAME}/datasets/ ─────────┼──→ dataset_pipeline ────┤ ├ Phase 1 PARALLEL
                                            │                         │ │ (3-worker pool;
   data/raw/{STUDY_NAME}/annotated_pdfs/ ───┴──→ pdf_pipeline ────────┤ ┘ join → cleanup)
                                                (orchestrator: pdfplumber│
                                                code path + redacted-    │
                                                text LLM merge +         │
                                                snapshot fallback at     │
                                                snapshots/{STUDY}/pdfs/) │
                                                                         │
                                              (all legs → staging)       ▼
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
                                                _publish_staging (atomic per-leg rename)
                                                                         │
                                                                         ▼
                                          output/{STUDY_NAME}/trio_bundle/{datasets,
                                                                          dictionary,
                                                                          pdfs,
                                                                          variables.json}
                                                                         │
                                              build_variables_reference (Step 3 — reads
                                                  the published trio bundle)
                                                                         │
                                              emit_lineage_manifest (Step 4 — raw SHA-256
                                                  ↔ trio SHA-256 + PHI-key fingerprint)
                                                                         │
                                                                         ▼
                                          output/{STUDY_NAME}/audit/lineage_manifest.json
                                                                         │
                                              _emit_output_signpost (Step 5)
                                                                         │
                                              _cleanup_staging (success only — secure_remove_tree)
                                                                         │
                                                                         ▼
                                                             World 2: AI Assistant
                                                       reads trio_bundle/ + agent/ only

Source tree
~~~~~~~~~~~

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
   ├── trio_bundle/                  # GREEN — LLM read zone
   │   ├── datasets/*.jsonl          # PHI-scrubbed
   │   ├── dictionary/*.json
   │   ├── pdfs/*_variables.json     # tier: merged | snapshot | empty
   │   └── variables.json            # consolidated schema
   ├── audit/                        # GREEN-PROTECT — counts only
   │   ├── lineage_manifest.json
   │   ├── phi_scrub_report.json
   │   ├── dataset_cleanup_report.json
   │   ├── dictionary_cleanup_report.json
   │   ├── pdfs_cleanup_report.json
   │   └── telemetry/
   │       └── events.jsonl
   └── agent/                        # analysis / conversations / restore_points

Transient staging root (not a durable artifact):

.. code-block:: text

   tmp/{STUDY_NAME}/
   ├── datasets/
   ├── dictionary/
   ├── pdfs/
   └── .pdf_cache/                   # idempotent LLM-response cache (PR #15)

Security Boundaries
-------------------

Zone Enforcement
~~~~~~~~~~~~~~~~

Two complementary chokepoints:

* **Pipeline-side directory guards** —
  :mod:`scripts.security.secure_env`. Functions: ``assert_not_raw``,
  ``assert_output_zone``, ``assert_write_zone``,
  ``assert_trio_bundle_zone``. Used at pipeline boundaries.
* **Agent-runtime path validator** —
  :mod:`scripts.ai_assistant.file_access`. Functions:
  ``validate_agent_read``, ``validate_agent_write``,
  ``validate_sandbox_write``, ``is_agent_readable``. Used by every
  agent tool before any file I/O.

Both raise ``ZoneViolationError`` (a ``PermissionError`` subclass)
on any zone violation. The agent's read zone is strictly
``trio_bundle/`` + ``agent/`` (plus the ``config/study_knowledge.yaml``
allowlist); audit, telemetry, staging, raw, and the snapshot
baseline are hard-rejected.

Three independent gates on every tool return
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

See :doc:`phi_architecture`. PHI regex catalog + k=5 + l=2.

API keys never in os.environ
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ADR-011 / PR #3. The wizard routes pasted keys into the in-memory
``KeyStore``; ``*_API_KEY`` env vars are scrubbed from
``os.environ``. Keys re-injected only into the short-lived
pipeline subprocess via ``KeyStore.env_for_subprocess``.

Subprocess sandbox for ``run_python_analysis``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ADR-010 / PR #2. ``RLIMIT_AS`` / ``RLIMIT_NPROC`` / ``RLIMIT_CPU``
clamps + sanitised env + read-only ``trio_bundle/`` + AST guards
inside the child. See :doc:`sandbox`.

Design Principles
-----------------

Modularity
~~~~~~~~~~

Each pipeline step is a function in ``main.py`` that imports its
operative module from ``scripts/``. The step + its module are the
unit of audit; you can verify Step 1.6 by reading
:func:`scripts.security.phi_scrub.run_scrub` and
``scripts/security/phi_scrub.yaml`` without needing to read any
other code.

Determinism where possible
~~~~~~~~~~~~~~~~~~~~~~~~~~

* HMAC date jitter is deterministic given the key (so re-runs
  produce identical pseudonyms + offsets).
* Step cache uses SHA-256 hashes of inputs (so re-runs skip
  unchanged steps).
* Lineage manifest + per-row provenance dict make every published
  byte traceable to a raw input hash + pipeline version.

Security-first boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~

* Two zone-guard chokepoints (pipeline + agent).
* Three agent-output gates (PHI / k-anon / l-diversity).
* KeyStore + subprocess sandbox + log redactor as orthogonal
  defenses.
* Two-tier snapshot model (tracked baseline + restore points)
  prevents the LLM from reading a stale baseline as live data.

Out-of-scope (explicit non-goals)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These are not part of the active local architecture contract
described here:

* upload-driven multi-study workflows
* HPC / Slurm deployment surfaces
* distributed processing claims
* historical phase-based roadmap promises

If a feature in the codebase contradicts the architecture described
on this page, the **page is the source of truth** for current
behaviour; the feature should either be reconciled or marked as
out-of-scope above. New ADRs in :doc:`decisions` capture genuine
architectural shifts.

See Also
--------

* :doc:`phi_architecture` — full PHI handling story.
* :doc:`decisions` — ADRs (especially 010-015 for the v0.19.0 /
  v0.20.0 work).
* :doc:`sandbox` — subprocess sandbox.
* :doc:`data_extraction_pdfs` — PDF orchestrator deep dive.
* :doc:`operations` — operational playbook + snapshot-baseline
  maintenance.
* :doc:`agents` — instructions for AI coding assistants.
