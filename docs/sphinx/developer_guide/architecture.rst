Architecture
============

The active technical architecture of the RePORT AI Portal runtime. For the
PHI-handling deep dive see :doc:`phi_architecture`; for the
decisions behind the choices see :doc:`decisions`; for the
operator's view see :doc:`../user_guide/data_pipeline`.

System Overview
---------------

RePORT AI Portal is a **two-world** system. The two worlds run in
separate processes and never share mutable state.

**World 1 — Deterministic Pipeline** (``main.py`` + ``scripts/extraction/`` +
``scripts/security/`` + ``scripts/utils/``).

Reads raw clinical data from ``data/raw/{STUDY}/``, stages and scrubs
dataset records, then builds the source-truth-backed LLM source
surface under ``output/{STUDY}/llm_source/``. The current
LLM-visible outputs are the scrubbed dataset files under
``llm_source/dataset_schema/files/``, dictionary mappings under
``llm_source/dictionary_mapping/jsonl/``, and verified lean Source
Truth YAML under ``llm_source/source_truth/``. The legacy PDF extraction,
Study Metadata Catalog / Evidence Pack build, and consolidated
``variables.json`` outputs are no longer active runtime surfaces.
``main.py --pipeline`` is the canonical entry point; ``make
pipeline`` is the Makefile alias; the wizard's "Load Study" button
spawns this as a subprocess.

**World 2 — AI Assistant** (``scripts/ai_assistant/``).

A LangGraph ReAct agent with 10 tools that reads the published
llm_source bundle and answers researcher queries. Provider-agnostic via
``init_chat_model``; runs against Anthropic / OpenAI / Google /
NVIDIA / Ollama. Never accesses raw data. Three independent gates
on every tool return (PHI regex catalog, k=5 anonymity, l=2
diversity).

The two worlds communicate through the ``output/{STUDY}/`` tree
only:

.. code-block:: text

   World 1 writes:                          World 2 reads:
   - llm_source/  (sanitised data)     →   - llm_source/  (LLM data surface)
   - audit/        (counts only)            (LLM hard-rejected for audit/)
   - agent/        (state subdirs)     →   - agent/  (LLM session memory)

The Streamlit wizard is the operator's entry point; it routes API
keys through the in-memory KeyStore, spawns the pipeline subprocess
on demand, and then hands off to the agent for chat.

The Zone Model
--------------

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
     - ``output/{STUDY}/{llm_source,agent}/``
     - LLM read zone. PHI-free.
   * - **GREEN-PROTECT**
     - Agent tool boundary
     - PHI regex + k-anonymity + l-diversity gates before answers.
   * - **AUDIT**
     - ``output/{STUDY}/audit/``
     - Counts-only IRB evidence. LLM-rejected.

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
* ``STUDY_LLM_SOURCE_DIR`` — the canonical published-bundle path.

Logging System
~~~~~~~~~~~~~~

:mod:`scripts.utils.logging_system` — root-logger setup with a
verbose "tree" mode for ``--verbose`` runs.
:mod:`scripts.utils.log_hygiene` — ``logging.Filter`` that scrubs
API keys + PHI patterns from every log line. Both filters attach
to the root logger so ``World 1`` and ``World 2`` emit scrubbed
logs by default.

``VerboseLogger`` keeps indentation in thread-local state, so
``--verbose`` tree output remains readable while the extraction and
source-truth build steps run.

Pipeline Modules
----------------

The pipeline is structured as a sequence of step functions in
``main.py``, each importing its operative module from
``scripts/extraction/``, ``scripts/security/``, or
``scripts/utils/``. Dictionary and dataset extraction run in parallel;
the cleanup chain, publish, and lineage steps are sequential. The
Makefile-level ``build-llm-source`` target first generates verified
PDF-backed lean SoT YAMLs, then runs the main pipeline so dictionary
mappings, PHI-scrubbed datasets, source-truth YAMLs, and audit lineage
land together under ``output/{STUDY}/``.

Dictionary Loader
~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.load_dictionary.load_study_dictionary`
* **Step:** Step 0
* **Reads:** ``data/raw/{STUDY}/data_dictionary/*.{xlsx,csv}``
* **Writes:** ``tmp/{STUDY}/dictionary/*.json``
* **PHI posture:** Carries no PHI by design (the dictionary defines
  variables, not subject values).

Dataset Extraction
~~~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.extraction.dataset_pipeline.process_datasets`
* **Step:** Step 1 (extract)
* **Reads:** ``data/raw/{STUDY}/datasets/*.{xlsx,csv}``
* **Writes:** ``tmp/{STUDY}/datasets/*.jsonl``
* **PHI posture:** Records carry full PHI here. Every record gets
  a ``_provenance`` dict (raw_sha256, pipeline_version,
  extraction_engine, source_file, sheet_name, row_index,
  study_name, extraction_utc).
* **Skip semantics:** Hash-based step cache at
  ``output/{STUDY}/audit/manifests/dataset_processing.json``.

PDF Extraction (Historical)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``scripts.extraction.pdf_pipeline`` and
``scripts.extraction.extract_pdf_data`` paths are historical. They are
preserved in ADRs and old test context, but they are not the active LLM
source flow. PDF-derived evidence now enters through the reviewed SoT
policy YAMLs and is published through the Study Metadata Catalog and
Evidence Packs.

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
  trees. Keeps the published llm_source bundle internally consistent.

Publish
~~~~~~~

* **Function:** ``_publish_staging`` in ``main.py``
* **Step:** Step 2
* **Atomic per-leg rename** ``tmp/{STUDY}/{leg}/`` →
  ``output/{STUDY}/llm_source/{leg}/``. Same-filesystem rename =
  single inode swap; cross-filesystem (e.g. tmpfs staging + disk
  output) falls back to ``shutil.copytree`` + ``shutil.rmtree``.
* **Zone guard:** ``assert_output_zone(llm_source_dir)`` runs before
  the rename.
* **Pre-publish:** if the destination exists,
  ``secure_remove_tree`` (zero-fill + fsync + unlink) so old
  bytes aren't recoverable.

Source-Truth YAML Creation (sot-lean-generator skill)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SoT YAML production is a **5-stage pipeline** that mixes deterministic
scripts with LLM reasoning. The entry points are cross-LLM by design:
the same rules files and verifier work regardless of whether the
operator is using Claude Code, ChatGPT, Gemini, or Cursor.

**Stage 0 — Source pack (deterministic)**

* **CLI wrapper:** ``scripts/source_truth/study_intake.py``
  (``python -m scripts.source_truth.study_intake --study <STUDY> --form <FORM>``)
* **Delegates to:** ``skills/sot-lean-generator/scripts/extract_sources.py``
* **Inputs:** ``data/raw/{STUDY}/annotated_pdfs/{FORM}.pdf`` and
  ``data/raw/{STUDY}/datasets/{FORM}.xlsx``
* **Outputs:**

  * ``/tmp/sot_source_pack_{FORM}.json`` — dataset row-1 header array +
    PDF SHA-256. **Row 2+ bytes never enter Python.**
  * ``/tmp/sot_render_{FORM}/{FORM}.pdf.png`` — 600 DPI Ghostscript render
    of the PDF (visual ground truth for LLM sweep)

* **Makefile alias:** ``make sot-source-pack STUDY=… FORM=…``

**Stages 1–3 — LLM-driven YAML authoring**

These stages require LLM reasoning and cannot be automated by a
deterministic script.

* Stage 1 — Exhaustive YAML write: LLM follows
  ``skills/sot-lean-generator/references/exhaustive_yaml_rules.md`` to
  produce a full draft YAML for the form.
* Stage 2 — 5-iteration visual sweep: LLM compares the 600 DPI render
  against the draft, correcting any widget or field mismatches.
* Stage 3 — Lean trim: LLM trims the exhaustive draft to the canonical
  lean schema per ``skills/sot-lean-generator/references/lean_yaml_rules.md``.
  Output written to ``/tmp/{FORM}_lean.yaml``.

**Claude Code users** invoke these stages via
``skills/sot-lean-generator/SKILL.md``.

**Other LLM tools (ChatGPT, Gemini, Cursor)** read
``skills/sot-lean-generator/references/exhaustive_yaml_rules.md`` then
``lean_yaml_rules.md`` directly and follow those rules. All LLM tools
share the same rules files and the same verifier; only the orchestration
shell differs.

**Stage 4 — Verifier (deterministic)**

* **Script:** ``skills/sot-lean-generator/scripts/check_lean_policy.py``
* **Makefile alias:** ``make sot-verify STUDY=… FORM=…``
* **Gates on:** forbidden text tokens, forbidden keys,
  instruction-block whitelist, header-equality against the source pack.
* **Exit codes:** 0 = ready to promote; 2 = SHA mismatch (re-run
  Stage 0); 3 = script gap (human intervention required).

**Stage 5 — Promote (deterministic)**

* Copies ``/tmp/{FORM}_lean.yaml`` →
  ``output/{STUDY}/llm_source/source_truth/{FORM}_policy.lean.yaml``.
* This path is the **canonical SoT output** — the runtime input for
  the LLM source builder.

**Reference data**

``data/SoT/{STUDY}/`` holds gold-example YAMLs that can be diffed
against new output to catch regressions. It is **reference-only** —
not a runtime input, not a build output.

* **Cross-LLM portability:** The full end-to-end flow is documented in
  ``AGENTS.md`` (``## SoT creation`` section) and
  ``docs/runbook_sot_build.md`` so it is reachable from any agentic LLM
  tool without requiring Claude Code.

Source-Truth Runtime Builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Step:** ``make build-llm-source``
* **Inputs:** annotated PDFs, row-1 dataset headers for the PDF-backed
  forms, raw dataset files, and the data dictionary.
* **Outputs:** verified lean YAMLs under
  ``output/{STUDY}/llm_source/source_truth/``, scrubbed dataset JSONL
  under ``output/{STUDY}/llm_source/dataset_schema/files/``,
  dictionary JSONL under
  ``output/{STUDY}/llm_source/dictionary_mapping/jsonl/``, and declared
  PHI / cleanup reports under ``output/{STUDY}/audit/``.
* **Staging:** SoT candidates live under ``/tmp`` until the checker
  passes; dataset and dictionary staging live under ``tmp/{STUDY}/`` and
  are securely removed after successful publish.

Lineage Manifest
~~~~~~~~~~~~~~~~

* **Module:** :func:`scripts.utils.lineage.emit_lineage_manifest`
* **Step:** Step 4
* **Output:** ``output/{STUDY}/audit/lineage_manifest.json`` —
  pairs every raw input SHA-256 with every published llm_source
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
* :mod:`scripts.ai_assistant.keystore` — in-memory API-key registry.
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
``os.path.commonpath``. Reads accept ``llm_source/`` ∪ ``agent/``
(plus ``config/study_knowledge.yaml`` via an explicit allowlist).
Writes accept ``agent/`` only. Sandbox writes narrow further to
``agent/analysis/``. Audit, telemetry, staging, and raw paths are
hard-rejected.

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
  (use an existing valid ``llm_source/`` bundle or run Load Study).
  Step 3 = Confirm + start chat.
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

   data/raw/{STUDY_NAME}/datasets/ ───────────→ dataset_pipeline ────────┐
                                                                          │
                                                   (records → staging)    ▼
                                          tmp/{STUDY_NAME}/datasets/
                                                                         │
                                                phi_scrub.run_scrub (Step 1.6 — date jitter +
                                                   ID pseudonymization on staged datasets;
                                                   emits phi_scrub_report.json)
                                                                         │
                                                  dataset_cleanup (emits dataset audit)
                                                                         │
                                              publish scrubbed dataset files
                                                                         │
                                                                         ▼
                                          output/{STUDY_NAME}/llm_source/dataset_schema/files/
                                                                         │
                                              SoT-backed LLM source build (SoT YAMLs →
                                                  catalog + evidence packs;
                                                  staging candidates under
                                                  tmp/{STUDY_NAME}/staging/llm_source/)
                                                                         │
                                              emit_lineage_manifest (Step 4 — raw SHA-256
                                                  ↔ llm_source SHA-256 + PHI-key fingerprint)
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
                                                       reads llm_source/ + agent/ only

Source tree
~~~~~~~~~~~

Expected source tree:

.. code-block:: text

   data/raw/{STUDY_NAME}/
   ├── datasets/
   └── data_dictionary/

Canonical SoT lean YAMLs live under
``output/{STUDY_NAME}/llm_source/source_truth/`` (produced by the
sot-lean-generator pipeline). ``data/SoT/{STUDY_NAME}/`` holds
gold-example reference YAMLs for regression diffs only and is not
read by the pipeline at runtime.

Expected processed tree:

.. code-block:: text

   output/{STUDY_NAME}/
   ├── llm_source/                   # GREEN — LLM read zone
   │   ├── dataset_schema/files/*.jsonl  # PHI-scrubbed
   │   ├── dictionary_mapping/jsonl/**/*.jsonl
   │   └── source_truth/*_policy.lean.yaml
   ├── audit/                        # AUDIT — counts only; LLM hard-rejected
   │   ├── lineage_manifest.json
   │   ├── phi_scrub_report.json
   │   ├── dataset_cleanup_report.json
   │   ├── dataset_cleanup_ledger.as_written.json
   │   ├── phi_handling_ledger.as_written.json
   │   └── telemetry/
   │       └── events.jsonl
   └── agent/                        # analysis / conversations

Transient staging root (not a durable artifact):

.. code-block:: text

   tmp/{STUDY_NAME}/
   ├── datasets/
   └── dictionary/

Security Boundaries
-------------------

Zone Enforcement
~~~~~~~~~~~~~~~~

Two complementary chokepoints:

* **Pipeline-side directory guards** —
  :mod:`scripts.security.secure_env`. Functions: ``assert_not_raw``,
  ``assert_output_zone``, ``assert_write_zone``,
  ``assert_clean_zone``. Used at pipeline boundaries.
* **Agent-runtime path validator** —
  :mod:`scripts.ai_assistant.file_access`. Functions:
  ``validate_agent_read``, ``validate_agent_write``,
  ``validate_sandbox_write``, ``is_agent_readable``. Used by every
  agent tool before any file I/O.

Both raise ``ZoneViolationError`` (a ``PermissionError`` subclass)
on any zone violation. The agent's read zone is strictly
``llm_source/`` + ``agent/`` (plus the ``config/study_knowledge.yaml``
allowlist); audit, telemetry, staging, and raw paths are
hard-rejected.

Three independent gates on every tool return
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

See :doc:`phi_architecture`. PHI regex catalog + k=5 + l=2.

API keys never in os.environ
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ADR-011. The wizard routes pasted keys into the in-memory
``KeyStore``; ``*_API_KEY`` env vars are scrubbed from
``os.environ``. Keys re-injected only into the short-lived
pipeline subprocess via ``KeyStore.env_for_subprocess``.

Subprocess sandbox for ``run_python_analysis``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ADR-010. ``RLIMIT_AS`` / ``RLIMIT_NPROC`` / ``RLIMIT_CPU``
clamps + sanitised env + read-only ``llm_source/`` + AST guards
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
* :doc:`decisions` — ADRs for the security and agent-boundary
  decisions, including historical PDF extraction records.
* :doc:`sandbox` — subprocess sandbox.
* :doc:`operations` — operational playbook.
* :doc:`agents` — instructions for AI coding assistants.
