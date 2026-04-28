Project Status
==============

Current implementation status of RePORT AI Portal components.

.. note::

   This is a living document. Update it when components are added, verified,
   or deprecated.

.. contents:: On this page
   :local:
   :depth: 2

Implemented and Verified
------------------------

Runtime Foundations
~~~~~~~~~~~~~~~~~~~

The foundational modules (``config.py``, ``main.py``, ``Makefile``,
``dedup.py``, ``logging_system.py``) and typing bounds have been hardened
for strict boundaries.

* ``LLM_PROVIDER`` config structured.
* CLI ``--chat`` and ``--web`` Makefile targets added.
* Migration to ``pathlib`` strict enforcement.
* **Claude Desktop-parity web UI** (``web_ui.py`` + ``scripts/ai_assistant/ui/``):
  dark design language with model pill, conversation history, streaming
  chat, wizard setup flow, artifact download, and interactive analysis
  charts. Styles live in ``scripts/ai_assistant/ui/assets/theme.css``
  (``--rpln-*`` token namespace; see *Design Token System* below).

Extraction and Registry
~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Component
     - Location
   * - Data dictionary loader
     - ``scripts/extraction/load_dictionary.py``
   * - Dataset extractor
     - ``scripts/extraction/dataset_pipeline.py``
   * - PDF extractor
     - ``scripts/extraction/extract_pdf_data.py``
   * - Dataset cleanup
     - ``scripts/extraction/dataset_cleanup.py``
   * - Cleanup propagation (cross-leg variable pruning)
     - ``scripts/extraction/cleanup_propagation.py``
   * - Deduplication
     - ``scripts/extraction/dedup.py``
   * - Variables reference builder
     - ``scripts/extraction/build_variables_reference.py``
   * - Pipeline staging helpers (``_prepare_staging``, ``_publish_staging``, ``_cleanup_staging``)
     - ``main.py``

Security — Four-Tier Honest-Broker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Component
     - Location
   * - Zone guards (RED / AMBER / GREEN / GREEN-PROTECT boundaries)
     - ``scripts/security/secure_env.py``
   * - PHI scrubber (8-action catalog — keep / birthdate / drop / cap /
       generalize / suppress_small_cell / date jitter / id pseudonymize)
     - ``scripts/security/phi_scrub.py`` + ``phi_scrub.yaml``
   * - Shared regex catalog (BLOCKING / WARN / SUBJECT_ID) used by
       agent gate AND log redactor
     - ``scripts/security/phi_patterns.py``
   * - Clinical-phrase allowlist (suppresses warn-tier false positives)
     - ``scripts/security/phi_allowlist.py``
   * - Query-time PHI gate (regex + allowlist, Presidio-rejected)
     - ``scripts/security/phi_gate.py``
   * - k-anonymity gate (equivalence-class check, small-cell suppression)
     - ``scripts/security/kanon_gate.py``
   * - Narrative NER design stub (feature-flagged; future work)
     - ``scripts/security/phi_ner.py``
   * - Agent-boundary safety decorator + k-anon row guard
     - ``scripts/ai_assistant/phi_safe.py``
   * - Phase-0 hardened staging (mode 0700, umask 0077, tmpfs opt-in,
       secure_remove_tree with overwrite + fsync + unlink)
     - ``scripts/utils/secure_staging.py``
   * - Per-run lineage manifest (raw→trio SHA-256 chain, IRB evidence)
     - ``scripts/utils/lineage.py``
   * - PHI log redactor (per-subject HMAC + regex catalog)
     - ``scripts/utils/log_hygiene.py``
   * - Integrity helpers (streamed SHA-256, single source of truth)
     - ``scripts/utils/integrity.py``

AI Assistant
~~~~~~~~~~~~

UI memory notes:

* Preserve the restored Streamlit composer shortcut mapping:
  ``Enter = send`` and ``Shift+Enter = newline`` unless an operator
  explicitly asks to change it.
* Preserve the transient in-chat loading pill as the text-plus-moving-dots
  variant: ``Working on it...`` rendered only while live loading/streaming
  is actually happening. Keep the text label visibly rendered for the whole
  active stream, not just the pre-token phase.
* Preserve the composer pending-state affordance: when streaming is active,
  the send button must switch from the normal send arrow to a centered
  stop-square busy state instead of a spinner. Keep it visually honest:
  do not imply mid-stream cancellation until a real interrupt path exists.
  Keep the glyph optically centered and properly scaled for the 40px button;
  prefer a centered SVG stop glyph over a tiny pseudo-element.
* Preserve the UI bridge/runtime rule: use ``st.iframe`` for the injected
  zero-height HTML/JS bridge surfaces, and do not force
  ``server.enableCORS = false`` while XSRF protection is enabled.
* Preserve the scroll-safety rule for UI bridges: every hidden
  ``st.iframe`` bridge host must be wrapped in a keyed container that is
  collapsed to zero layout footprint in CSS, otherwise the chat scrollport
  can get stuck mid-page.
* Append every future user-requested UI behavior change to this memory block
  so the next UI pass does not silently undo it.

.. list-table::
   :header-rows: 1

   * - Component
     - Location
   * - ReAct agent (LangChain ``create_agent`` + 12 tools, live streaming)
     - ``scripts/ai_assistant/agent_graph.py``
   * - Agent prompts (system prompt with disclosure rules)
     - ``scripts/ai_assistant/agent_prompts.py``
   * - Agent tools (12 structured-data tools, v3 23-field schema)
     - ``scripts/ai_assistant/agent_tools.py``
   * - Per-session LRU tool cache
     - ``scripts/ai_assistant/tool_cache.py``
   * - Interactive REPL CLI
     - ``scripts/ai_assistant/cli.py``
   * - Streamlit web UI (3-step setup wizard, live streaming chat, Claude Desktop-parity UI)
     - ``scripts/ai_assistant/web_ui.py``
   * - ``_StreamError`` typed dataclass — cross-thread streaming error envelope
     - ``scripts/ai_assistant/agent_graph.py``
   * - JS bridge (delegated click handling, appearance token hydration)
     - ``scripts/ai_assistant/ui/assets/bridge.js``

Utilities
~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Component
     - Location
   * - Centralized logging
     - ``scripts/utils/logging_system.py``
   * - Structured error envelopes
     - ``scripts/utils/errors.py``
   * - Self-improvement telemetry
     - ``scripts/utils/telemetry.py``
   * - Step cache (incremental-run manifest + skip semantics)
     - ``scripts/utils/step_cache.py``
   * - Restore points (trio-bundle copy/restore CLI; live under
       ``output/{STUDY}/agent/restore_points/``, gitignored). Distinct
       from the version-controlled tracked baseline at ``snapshots/{STUDY}/``.
     - ``scripts/utils/snapshots.py`` + ``python -m scripts.utils.snapshots``
   * - Artifact versioning
     - ``scripts/artifact_versions.py``

(Security-classed utilities — ``secure_staging``, ``lineage``,
``log_hygiene``, ``integrity`` — are listed under the Security section
above.)

Design Token System
~~~~~~~~~~~~~~~~~~~~

All design tokens in ``scripts/ai_assistant/ui/assets/theme.css`` use the
``--rpln-*`` namespace (single source of truth — the primary ``:root`` block).

.. list-table::
   :header-rows: 1

   * - Category
     - Tokens
     - Notes
   * - Colors
     - ``--rpln-bg``, ``--rpln-text``, ``--rpln-accent``, ``--rpln-accent-orange`` (compat),
       ``--rpln-text-muted``, ``--rpln-hairline``, ``--rpln-good``, ``--rpln-bad``
     - ``--rpln-accent`` is the semantic alias; ``--rpln-accent-orange`` preserved for compat
   * - Spacing
     - ``--rpln-space-{0-5…10}`` (2 px–48 px, 4 px base; 2 px / 6 px half-steps)
     - 7 px and 42 px stay raw — pixel-perfect Streamlit calibrations
   * - Type scale
     - ``--rpln-text-{2xs…5xl}`` (10 px–44 px)
     - 13 px / 15 px / 17 px stay raw — Streamlit emotion-cache tuning
   * - Line-height
     - ``--rpln-leading-{tight/snug/body/loose}`` (1.2 / 1.35 / 1.6 / 1.7)
     -
   * - Radius
     - ``--rpln-radius-{xs/sm/md/lg/pill/bubble}``
     -
   * - Z-index
     - ``--rpln-z-{base/raise/topbar/overlay/menu/popover}`` (1–1010)
     -
   * - Easing
     - ``--rpln-ease-out: cubic-bezier(0.2, 0.8, 0.2, 1)``
     - Canonical definition in primary ``:root``; backward-compat ``--ease-out`` aliases it
   * - Durations
     - ``--rpln-dur-{fast/base/slow}`` (150 ms / 200 ms / 280 ms)
     - 120 ms / 160 ms stay raw — micro-tuned Streamlit overrides

A backward-compat ``:root`` block (line ~1297) contains deprecated
``--fs-*`` / ``--sp-*`` / ``--r-*`` / ``--dur-*`` scales annotated
``@deprecated`` — do **not** use these in new CSS; use ``--rpln-*`` instead.

LLM Provider Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

LLM providers are configured via LangChain's ``init_chat_model()`` and
the ``LLM_PROVIDER`` / ``LLM_MODEL`` settings in ``config.py``.
No bespoke adapter modules are required.

Supported providers: OpenAI, Anthropic, Google, Ollama, vLLM.

**Qwen3 downgrade ladder (Ollama OOM resilience).**
``config.QWEN3_DOWNGRADE_LADDER = ("qwen3:8b", "qwen3:4b", "qwen3:1.7b")``
and ``config.preferred_or_installed_downgrade()`` drive an OOM-aware init
path in ``agent_graph._init_llm``. For the ``ollama`` provider on a qwen3
model, the initializer probes each rung with a one-token ``invoke("ok")``
— LangChain's ``ChatOllama`` doesn't trigger an Ollama model load during
construction, so OOM only surfaces on the first real request. When the
probe hits Ollama's ``model requires more system memory ... than is
available`` 500, the walker logs a warning, steps to the next rung, and
retries. On success it mutates ``config.LLM_MODEL`` so the wizard, error
cards, and telemetry show the rung actually in use. Non-qwen3 models
and remote providers (Anthropic, OpenAI, Gemini, NVIDIA) skip the probe.
If all rungs refuse, the resulting ``RuntimeError`` is routed to the new
OOM branch in ``streaming.py``'s ``_stream_response`` classifier (matched
via the module-level ``_MEMORY_KEYWORDS`` tuple), which surfaces a
``💾 Out of memory`` card with actionable guidance instead of the generic
"Query failed" catch-all. **Classifier ordering is load-bearing**: the
OOM elif must precede the ``_conn_keywords`` elif because the
all-rungs-fail RuntimeError text contains both "refused" and "insufficient
memory".

**Sticky-downgrade caveat.** After the walker steps down, ``config.LLM_MODEL``
is mutated in place — a subsequent ``reset_agent()`` / UI **Reset** re-runs
``_init_llm`` starting from the downgraded rung, not the originally-requested
one. Freeing RAM mid-session does not automatically re-climb the ladder. To
intentionally revert, restart the Streamlit process (or re-set ``LLM_MODEL``
via env + Reset).

Test Coverage
-------------

.. list-table::
   :header-rows: 1

   * - Test File
     - Module Under Test
   * - ``test_config.py``
     - ``config.py``
   * - ``test_smoke.py``
     - End-to-end smoke tests
   * - ``test_dataset_pipeline.py``
     - ``scripts/extraction/dataset_pipeline.py``
   * - ``test_dataset_cleanup.py``
     - ``scripts/extraction/dataset_cleanup.py``
   * - ``test_cleanup_propagation.py``
     - ``scripts/extraction/cleanup_propagation.py``
   * - ``test_main_helpers.py``
     - ``main.py`` staging helpers (``_prepare_staging``, ``_publish_staging``, ``_cleanup_staging``)
   * - ``test_extract_pdf_data.py``
     - ``scripts/extraction/extract_pdf_data.py``
   * - ``test_load_dictionary.py``
     - ``scripts/extraction/load_dictionary.py``
   * - ``test_dedup.py``
     - ``scripts/extraction/dedup.py``
   * - ``test_file_discovery.py``
     - File discovery utilities
   * - ``test_file_io.py``
     - File I/O utilities
   * - ``test_jsonl_reader.py``
     - JSONL reader
   * - ``test_secure_env.py``
     - ``scripts/security/secure_env.py``
   * - ``test_step_cache.py``
     - ``scripts/utils/step_cache.py``
   * - ``test_artifact_versions.py``
     - ``scripts/artifact_versions.py``
   * - ``test_date_transform.py``
     - Date transformation logic
   * - ``test_logging_system.py``
     - ``scripts/utils/logging_system.py``
   * - ``test_agent_graph.py`` *(requires langchain_core)*
     - ``scripts/ai_assistant/agent_graph.py``
   * - ``test_agent_tools.py`` *(requires langchain_core)*
     - ``scripts/ai_assistant/agent_tools.py``
   * - ``test_cli.py`` *(requires langchain_core)*
     - ``scripts/ai_assistant/cli.py``
   * - ``test_telemetry.py`` *(requires langchain_core)*
     - ``scripts/utils/telemetry.py``
   * - ``test_build_variables_reference.py``
     - ``scripts/extraction/build_variables_reference.py`` (v3 schema, 4 loaders)
   * - ``test_file_access.py``
     - ``scripts/ai_assistant/file_access.py``
   * - ``test_study_knowledge.py``
     - ``scripts/ai_assistant/study_knowledge.py``
   * - ``test_web_ui.py``
     - ``scripts/ai_assistant/web_ui.py``
   * - ``test_analytical_engine.py``
     - ``scripts/ai_assistant/analytical_engine.py``
   * - ``test_cohort_builder.py``
     - Cohort builder logic
   * - ``test_run_study_analysis.py``
     - Study analysis runner

The current verification gate is ``make test-all`` plus ``make verify``.
The full suite includes PHI architecture + agent-boundary gates + log
hygiene + lineage + secure-staging + PDF PHI-flag tests. See
``docs/irb_dossier/conformance_matrix.md`` for the 31-criterion
benchmark (plus four follow-ups added in patches 2026-04-23a/b) with
the specific pytest case backing each claim.

Additional PHI-specific test modules:

.. list-table::
   :header-rows: 1

   * - Test File
     - Module Under Test
   * - ``test_phi_scrub.py``
     - ``scripts/security/phi_scrub.py`` (104 tests incl. catalog coverage)
   * - ``test_phi_gate.py``
     - ``scripts/security/phi_gate.py``, ``kanon_gate.py``,
       ``phi_allowlist.py``, ``scripts/ai_assistant/phi_safe.py``
   * - ``test_secure_staging.py``
     - ``scripts/utils/secure_staging.py`` (mode 0700, umask, secure-remove,
       tmpfs resolver)
   * - ``test_pipeline_provenance.py``
     - ``scripts/extraction/dataset_pipeline._build_provenance`` extensions
   * - ``test_lineage_manifest.py``
     - ``scripts/utils/lineage.emit_lineage_manifest``
   * - ``test_log_hygiene.py``
     - ``scripts/utils/log_hygiene.PHIRedactingFilter``
   * - ``test_pdf_phi_flag.py``
     - ``scripts/extraction/extract_pdf_data._resolve_pdf_provider``
       (REPORTALIN_PDF_PHI_FREE flag)

IRB Benchmark Conformance
-------------------------

The 31-criterion IRB-grade benchmark (plus four follow-ups added in
patches 2026-04-23a/b, totalling 35 architecturally satisfied) in
``docs/irb_dossier/conformance_matrix.md`` is satisfied as follows:

* **31 / 31** original criteria fully green — every claim has a passing
  test and a cited authority.
* **4 / 4** patch-2026-04-23a/b follow-ups (Pillars 2.5 / 2.6 / 2.7 /
  2.8: input-side prompt-injection guard, untrusted-snippet sanitiser,
  redact-PHI-in-text helper, traceback sanitiser) — all green.
* **4 known follow-ups** (separate from the patch-added criteria) —
  district pop≥20k mapping (configuration extension), pdfplumber hybrid
  (future work), breach-response runbook (study-team owned), and
  ``config/consent_scope.yaml`` (operator-owned). See
  ``conformance_matrix.md`` for the full breakdown.

See :doc:`phi_architecture` for the module map, :doc:`decisions` for
per-decision rationale, :doc:`references` for authority URLs.

Planned (Not Yet Implemented)
-----------------------------

.. note::

   These items are explicitly out of scope for the current honest-broker
   architecture. They are documented so a future contributor knows what
   has been considered.

* **Local-Ollama narrative NER sweep**
  (``scripts/security/phi_ner.py`` design stub). Enable via
  ``REPORTALIN_OLLAMA_NER=1`` once the prompt + model are calibrated
  against the Indo-VAP narrative corpus.
* **Local-only PDF extraction** (pdfplumber + local multimodal
  fallback) to replace the ``REPORTALIN_PDF_PHI_FREE=1`` external-API
  path.
* **District-population lookup table** for HIPAA-analog pop≥20k
  generalization.
* **``config/consent_scope.yaml``** operator-owned field allowlist.
* **Standalone ``app/``** Streamlit pages.
* **Vector DB integration** (if chunking + retrieval is ever needed —
  today the agent reads the trio bundle directly, no vector index).
* **Multi-study support** beyond Indo-VAP.
