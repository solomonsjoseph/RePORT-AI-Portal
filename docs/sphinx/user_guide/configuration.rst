Configuration
=============

Configuration in RePORT AI Portal is split across:

* :mod:`config` — central Python module that resolves every path and
  most behaviour flags from env vars + a YAML overlay. Read by every
  module that needs a path or knob.
* ``config/config.yaml`` — runtime LLM settings (provider, model
  name, temperature, max tokens). Operator-edited.
* ``config/study_knowledge.yaml`` — study-specific knowledge that the
  agent uses for grounding (the only YAML the LLM is allowed to read,
  via an explicit allowlist in
  :func:`scripts.ai_assistant.file_access.validate_agent_read`).
* ``.env.example`` — the template for the operator's ``.env`` file.

Environment variables override YAML; YAML overrides hardcoded
defaults.

Quick start: minimum viable config
----------------------------------

A fresh checkout works with just two env vars (one if you use Ollama
locally):

.. code-block:: bash

   # Required
   export STUDY_NAME=Indo-VAP                      # the only study name supported by default

   # LLM provider — pick one
   export LLM_PROVIDER=ollama                      # local, no API key (default)
   # OR
   export LLM_PROVIDER=anthropic                   # remote, needs key
   export ANTHROPIC_API_KEY=sk-ant-...
   # OR
   export LLM_PROVIDER=google-genai
   export GOOGLE_API_KEY=...
   # OR
   export LLM_PROVIDER=openai
   export OPENAI_API_KEY=sk-...

The Streamlit wizard (``make chat``) will pick up the env var if
present and pre-fill the step-1 form. Otherwise paste it in the form
— the wizard routes the key through the in-memory ``KeyStore``
(see :ref:`config-keystore` below) and scrubs the corresponding
``*_API_KEY`` from ``os.environ`` for the lifetime of the app.

Path configuration
------------------

All paths are derived from ``BASE_DIR`` in :mod:`config` (the repo
root):

.. list-table::
   :header-rows: 1
   :widths: 26 30 44

   * - Variable
     - Default
     - Description
   * - ``DATA_DIR``
     - ``data/``
     - Root data directory
   * - ``RAW_DATA_DIR``
     - ``data/raw/``
     - Raw study data — RED zone (read-only by the extraction leg)
   * - ``OUTPUT_DIR``
     - ``output/``
     - Pipeline outputs root (gitignored)
   * - ``TRIO_BUNDLE_DIR``
     - ``output/{STUDY}/trio_bundle/``
     - Published, sanitised study bundle — GREEN zone (LLM-readable)
   * - ``STUDY_AUDIT_DIR``
     - ``output/{STUDY}/audit/``
     - Counts-only IRB audit envelope (LLM-rejected)
   * - ``AGENT_STATE_DIR``
     - ``output/{STUDY}/agent/``
     - Agent-owned operational state (analysis, conversations,
       restore points) — GREEN, LLM-readable
   * - ``STUDY_RESTORE_POINTS_DIR``
     - ``output/{STUDY}/agent/restore_points/``
     - Multi-named restore points from
       ``python -m scripts.utils.snapshots create``. Gitignored.
   * - ``STUDY_SNAPSHOTS_DIR``
     - ``snapshots/{STUDY}/``
     - **Tracked baseline** at the repo root (version-controlled,
       LLM-INVISIBLE). Used by the PDF orchestrator's per-PDF
       fallback when the LLM tier is unavailable.
       Maintainer-curated by hand — see
       :doc:`../developer_guide/operations` (Trio-Bundle Snapshot
       Maintenance section).
   * - ``TMP_DIR``
     - ``tmp/``
     - Per-run scratch workspace root — AMBER zone
   * - ``STUDY_STAGING_DIR``
     - ``tmp/{STUDY}/``
     - The actual staging tree for the current run

LLM configuration
-----------------

.. list-table::
   :header-rows: 1
   :widths: 30 24 46

   * - Variable
     - Default
     - Description
   * - ``LLM_PROVIDER``
     - (empty)
     - Required: ``openai``, ``anthropic``, ``google-genai``,
       ``ollama``, or ``nvidia-ai-endpoints``
   * - ``LLM_MODEL``
     - (provider default)
     - e.g. ``claude-opus-4-7``, ``gpt-5``, ``gemini-2.5-pro``,
       ``qwen3:8b``, ``meta/llama-3.3-405b-instruct``
   * - ``ANTHROPIC_API_KEY``
     - (none)
     - Anthropic API key. NOTE: see :ref:`config-keystore` below for
       how the wizard removes this from the parent process's
       ``os.environ`` after first read.
   * - ``OPENAI_API_KEY``
     - (none)
     - OpenAI API key (same KeyStore note applies)
   * - ``GOOGLE_API_KEY``
     - (none)
     - Google Gemini API key (same KeyStore note applies)
   * - ``OLLAMA_BASE_URL``
     - ``http://127.0.0.1:11434``
     - Ollama server URL used by the chat agent and UI model discovery.
       ``OLLAMA_HOST`` is also accepted as an Ollama-compatible alias.

Capability + model selection for the PDF orchestrator
-----------------------------------------------------

The PDF orchestrator gates its LLM tier with a model-name allowlist;
only "capable" models can run the LLM tier. Defaults are
hardcoded in
:func:`scripts.utils.llm_capabilities.is_capable_model`:

* Claude **Opus 4.6+** / **Sonnet 4.6+** (older Sonnet excluded)
* OpenAI **GPT-5+** (GPT-4 family excluded)
* Google **Gemini 2.5 Pro** (Flash excluded)
* NVIDIA **Llama 3.3 405B** (smaller variants excluded)

Override via the env var below to validate a local Ollama model.

.. _config-phi-flags:

PHI-Safety configuration
------------------------

Three operator-controlled flags governing the PHI-safety surface, plus
two env vars that select PDF extraction behaviour. All default to
**off** / **unset**; turning them on is an explicit operator choice
logged against the IRB dossier.

.. list-table::
   :header-rows: 1
   :widths: 36 14 50

   * - Variable
     - Default
     - Description
   * - ``REPORTALIN_TMPFS_STAGING``
     - ``0``
     - When ``1`` AND Linux ``/dev/shm`` is writable, the extraction
       leg redirects staging to
       ``/dev/shm/report_ai_portal/{STUDY}/`` so raw extracted rows
       never hit physical disk on the extraction host. Gracefully
       falls back to ``tmp/{STUDY}/`` on macOS / Windows or when
       ``/dev/shm`` is not writable.
   * - ``REPORTALIN_PDF_PHI_FREE``
     - ``0``
     - Operator opt-in for the **legacy raw-PDF API path**. When
       ``0``, ``_resolve_pdf_provider`` refuses to initialise the
       Anthropic / Google Gemini client (raw PDF bytes would egress to
       a third party). Set to ``1`` ONLY if the source PDFs are
       verified PHI-free (blank CRFs, protocol, MOP) — and ALSO
       create a non-empty attestation note at
       ``authorities/phi_free_pdfs.md``. Both are required.
   * - ``REPORTALIN_PDF_EXTRACTION_MODE``
     - (unset)
     - Selects the PDF extraction path inside
       ``extract_pdfs_to_jsonl``. ``llm`` runs the orchestrator
       (``scripts/extraction/pdf_pipeline.py``) — pdfplumber code path
       + redacted-text LLM merge + per-PDF snapshot fallback.
       ``snapshot`` skips the LLM entirely and publishes
       the ``snapshots/{STUDY}/pdfs/`` baseline verbatim. Unset
       (the CLI default) keeps the legacy raw-PDF API path with its
       two-part PHI-free attestation gate. The wizard's "Load Study"
       button always sets this to ``llm``.
   * - ``REPORTALIN_PDF_LLM_CAPABLE_MODELS``
     - (empty)
     - Comma-separated lowercase model-name prefixes that the PDF
       orchestrator's capability gate
       (:func:`scripts.utils.llm_capabilities.is_capable_model`)
       treats as capable. **Replaces** (not extends) the hardcoded
       default allowlist. Operator opt-in for validated local
       Ollama models — without this override, Ollama is excluded by
       default regardless of model name.

.. _config-keystore:

Credential handling (KeyStore)
------------------------------

API keys never persist in the parent process's ``os.environ`` for
the lifetime of the app. The flow is:

1. Operator pastes the key into the Streamlit wizard's step-1 form
   (or it's already exported in their shell — the wizard pre-fills
   from ``os.environ`` if so).
2. On submit, the wizard routes the key into
   :mod:`scripts.ai_assistant.keystore` (an in-memory ``KeyStore``
   registry indexed by provider slug).
3. The corresponding ``*_API_KEY`` env variable is **removed from
   the parent process's** ``os.environ``.
4. Every LLM client constructor (``ChatAnthropic``,
   ``ChatOpenAI``, ``ChatGoogleGenerativeAI``,
   ``ChatNVIDIA``, ``ChatOllama``) takes an explicit ``api_key=``
   kwarg sourced from the KeyStore. No environment lookup at
   construction time.
5. When the wizard spawns the pipeline subprocess (``make pipeline``
   under the hood), keys are re-injected into the child's env via
   ``KeyStore.env_for_subprocess(...)`` — and the child runs to
   completion, then exits, so the keys live for at most the duration
   of one ``--pipeline`` invocation.

CLI users who invoke ``main.py`` directly use the env-var path
normally; the parent shell retains the keys, but the in-process app
does not.

PHI key (out-of-repo sidecar)
-----------------------------

The HMAC-SHA256 key that drives per-subject date jitter and ID
pseudonymization lives at ``~/.config/report_ai_portal/phi_key``
(overridable via ``XDG_CONFIG_HOME``). Required properties:

* file mode ``0600`` (owner read/write only)
* 32 random bytes encoded as 64 hex characters
* outside the repo tree; never committed

Bootstrap with:

.. code-block:: bash

   python -m scripts.security.phi_scrub bootstrap-key

Key rotation = full re-ingestion (pseudonyms change). The bootstrap
command refuses to overwrite an existing key — explicit deletion is
required.

Logging
-------

.. list-table::
   :header-rows: 1
   :widths: 26 24 50

   * - Variable
     - Default
     - Description
   * - ``LOG_LEVEL``
     - ``INFO``
     - Root logger level. Set to ``DEBUG`` for tree-style verbose
       output; the parallel-extraction phase renders per-leg progress
       in ``--verbose`` mode using thread-local indentation.
   * - ``LOG_VERBOSE``
     - ``0``
     - When ``1``, force-enables the verbose tree-style output
       regardless of ``LOG_LEVEL``.

The PHI log redactor
(:func:`scripts.utils.log_hygiene.install_phi_redactor`) is attached
to the root logger; every log line goes through it before reaching
the file handler. Patterns covered: API keys (``sk-ant-…`` /
``sk-…``), Aadhaar, PAN, phone, email, precise dates.

Where to go next
----------------

* :doc:`quickstart` — ten-minute walkthrough.
* :doc:`data_pipeline` — what ``--pipeline`` actually does.
* :doc:`../developer_guide/operations` — operational playbook,
  including the snapshot-baseline maintenance protocol.
* :doc:`faq` — trust, PHI scope, leak-response questions.
