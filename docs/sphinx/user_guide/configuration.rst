Configuration
=============

RePORT AI Portal configuration is managed through ``config.py`` (centralized path
and environment config), ``config/config.yaml`` (runtime LLM settings), and
environment variables.

.. contents:: On this page
   :local:
   :depth: 2

Configuration Files
-------------------

config.py
~~~~~~~~~

The centralized configuration module. All paths, constants, and environment
variable access are defined here. Modules import from ``config`` — never
read env vars directly.

config/config.yaml
~~~~~~~~~~~~~~~~~~

Runtime LLM and agent settings. The LLM provider defaults to ``null`` and
must be set explicitly (no silent fallback).

.. code-block:: yaml

   llm:
     provider: null  # openai | anthropic | google | ollama

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

Environment variables override config.py defaults:

.. code-block:: bash

   export LLM_PROVIDER=openai
   export OPENAI_API_KEY=sk-...
   export STUDY_NAME=Indo-VAP

Path Configuration
------------------

All paths are derived from ``BASE_DIR`` in ``config.py``:

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Description
   * - ``DATA_DIR``
     - ``data/``
     - Root data directory
   * - ``RAW_DATA_DIR``
     - ``data/raw/``
     - Raw study data (zone-restricted)
   * - ``OUTPUT_DIR``
     - ``output/``
     - Pipeline outputs
   * - ``TRIO_BUNDLE_DIR``
     - ``output/{STUDY}/trio_bundle/``
     - Clean evidence (clean zone)
   * - ``STUDY_SNAPSHOTS_DIR``
     - ``snapshots/{STUDY}/``
     - **Tracked baseline** at the repo root (version-controlled,
       LLM-INVISIBLE). Used by the PDF orchestrator's per-PDF fallback
       when the LLM tier is unavailable. Maintainer-curated by hand —
       see ``snapshots/README.md``.
   * - ``STUDY_RESTORE_POINTS_DIR``
     - ``output/{STUDY}/agent/restore_points/``
     - Operator-restore tier (gitignored, multi-named runs). Created /
       restored via ``python -m scripts.utils.snapshots {create,list,restore}``.
       Distinct from the tracked baseline above.

LLM Configuration
-----------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Description
   * - ``LLM_PROVIDER``
     - (empty)
     - Required: openai, anthropic, google, ollama
   * - ``OPENAI_API_KEY``
     - (none)
     - OpenAI API key
   * - ``ANTHROPIC_API_KEY``
     - (none)
     - Anthropic API key
   * - ``GOOGLE_API_KEY``
     - (none)
     - Google API key
   * - ``OLLAMA_BASE_URL``
     - ``http://localhost:11434``
     - Ollama server URL

.. note::

   **API keys never persist in the parent process's** ``os.environ``.
   When the wizard's step-1 form is submitted, the key is routed into
   :mod:`scripts.ai_assistant.keystore` (an in-memory ``KeyStore``
   registry) and the corresponding ``*_API_KEY`` variable is scrubbed
   from ``os.environ``. The key is re-injected only into the
   short-lived pipeline subprocess via
   ``KeyStore.env_for_subprocess(...)``. Operators using the CLI may
   still ``export`` keys before invoking ``main.py``; the parent shell
   retains them, but the in-process app does not. This change shipped
   in PR #3 (v0.17.0) and is the reason no test or runtime path reads
   ``os.environ`` for credentials any more.

PHI-Safety Configuration
------------------------

Three operator-controlled flags governing the PHI-safety surface. All
default to **off**; turning them on is an explicit operator choice logged
against the IRB dossier.

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Description
   * - ``REPORTALIN_TMPFS_STAGING``
     - ``0``
     - When ``1`` AND Linux ``/dev/shm`` is writable, the extraction leg
       redirects staging to
       ``/dev/shm/report_ai_portal/{STUDY}/``
       so raw extracted rows never hit physical disk on the extraction
       host. Gracefully falls back to ``tmp/{STUDY}/`` on macOS / Windows
       or when ``/dev/shm`` is not writable.
   * - ``REPORTALIN_PDF_PHI_FREE``
     - ``0``
     - Operator opt-in for external-API PDF extraction. When ``0``,
       ``_resolve_pdf_provider`` refuses to initialise the Anthropic /
       Google Gemini client (raw PDF bytes would egress to a third
       party). Set to ``1`` ONLY if the source PDFs are verified
       PHI-free (blank CRFs, protocol, MOP) — this is your signed
       assertion for the IRB.
   * - ``REPORTALIN_OLLAMA_NER``
     - ``0``
     - Feature flag for the Stage-5 local-Ollama narrative NER sweep
       (:mod:`scripts.security.phi_ner`). Setting this today is a no-op
       because the implementation is a design stub pending prompt
       calibration against the Indo-VAP narrative corpus.

PHI key (out-of-repo sidecar)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The HMAC-SHA256 key that drives per-subject date jitter and ID
pseudonymization lives at ``~/.config/report_ai_portal/phi_key``
(overridable via ``XDG_CONFIG_HOME``). Required properties:

- file mode ``0600`` (owner read/write only)
- 32 random bytes encoded as 64 hex characters
- outside the repo tree; never committed

Bootstrap with:

.. code-block:: bash

   python -m scripts.security.phi_scrub bootstrap-key

Rotating the key (by deleting it + re-bootstrapping) invalidates every
previously-scrubbed artifact — full re-ingestion from raw is required.

Study Detection
---------------

``config.py`` auto-detects the study name by scanning ``data/raw/`` for
directories containing a ``datasets/`` subdirectory. Override with:

.. code-block:: bash

   export STUDY_NAME=Indo-VAP

Best Practices
--------------

1. **Never commit API keys** — use environment variables
2. **LLM provider must be explicit** — no default provider
3. **Use** ``config.py`` **for all path references** — never hardcode paths
4. **Security updates:** ``uv lock --upgrade && uv sync --all-groups``
