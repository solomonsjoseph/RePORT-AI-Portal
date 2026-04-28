PDF Extraction
==============

The PDF orchestrator is the primary path; the legacy raw-PDF API path
is documented as the back-compat fallback.

Two co-existing extraction paths
--------------------------------

Annotated CRF PDFs may carry filled-in patient data, signatures, or
example subject IDs in annotations — they are presumed PHI-bearing.
The pipeline ships two paths with very different egress postures:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Path
     - Module
     - Egress posture
   * - **Orchestrator (default)**
     - :mod:`scripts.extraction.pdf_pipeline`
     - **No raw PDF bytes leave the host.** Text is extracted locally
       via ``pdfplumber``, PHI-redacted in place, then sent to the
       LLM as redacted text only.
   * - **Legacy raw-PDF API**
     - :mod:`scripts.extraction.extract_pdf_data`
     - **Raw PDF bytes are base64-encoded and shipped to the
       provider.** Refused unless the operator opts in twice
       (``REPORTALIN_PDF_PHI_FREE=1`` env flag *plus* a non-empty
       attestation note at ``authorities/phi_free_pdfs.md``).

The wizard's "Load Study" button selects the orchestrator. The
legacy path is the CLI default for back-compat.

Dispatch
--------

Both paths route through
:func:`scripts.extraction.extract_pdf_data.extract_pdfs_to_jsonl`,
which checks the ``REPORTALIN_PDF_EXTRACTION_MODE`` env var:

* ``llm`` → orchestrator path (the wizard always sets this).
* ``snapshot`` → publish ``data/snapshots/{STUDY}/pdfs/`` baseline
  verbatim, no LLM call.
* unset (CLI default) → legacy raw-PDF API path with the two-part
  attestation gate.

Orchestrator path (the default)
-------------------------------

For each PDF, the orchestrator runs:

1. **Code path (always).** ``pdfplumber.open()`` extracts page text +
   a heuristic candidate via ``_candidate_from_text``.
2. **Capability + provider gate.**
   :func:`scripts.utils.llm_capabilities.is_capable_model` enforces a
   model allowlist (Claude Opus/Sonnet 4.6+, GPT-5+, Gemini 2.5 Pro,
   Llama 3.3 405B). The default allowlist is hardcoded; operators can
   override with ``REPORTALIN_PDF_LLM_CAPABLE_MODELS`` (comma-separated
   prefix list) — for example to validate a local Ollama model.
   :data:`scripts.extraction.pdf_pipeline.ORCHESTRATOR_SUPPORTED_PROVIDERS`
   restricts the LLM tier to ``anthropic`` + ``google`` (where the
   actual client wiring exists).
3. **Redact-then-call.** Extracted text is scrubbed via
   ``phi_patterns.BLOCKING_PATTERNS``; a defensive
   ``_assert_no_raw_phi_in_payload`` re-checks and raises if any
   blocking pattern survives. Only the redacted text reaches the
   LLM.
4. **Merge.** :func:`scripts.extraction.pdf_pipeline._merge`
   reconciles the LLM response with the code candidate (LLM wins on
   field-level conflicts; code fills in vars the LLM missed). The
   LLM response is also re-scrubbed via
   :func:`scripts.ai_assistant.phi_safe.guard_text` before merge.
5. **Idempotent cache.** Keyed on
   ``SHA-256(pdf_bytes || provider || model || phi_scrub.yaml hash)``;
   stored at ``tmp/{STUDY}/.pdf_cache/`` (mode ``0600``). Editing the
   PHI scrub config invalidates every cache entry.
6. **Snapshot fallback (per-PDF).** When any of (capable model,
   API key, code-path text non-empty, LLM call success) is missing,
   the orchestrator publishes the reviewed baseline at
   ``data/snapshots/{STUDY}/pdfs/{stem}_variables.json`` instead.
   **Code-only output is never an acceptable result** — heuristic-only
   metadata is too unreliable to publish without LLM oversight.

Output JSON carries an ``extraction_tier`` field
(``merged`` / ``llm`` / ``snapshot`` / ``empty``) so any reader can
tell which path produced it.

The orchestrator never reads raw inputs other than the PDF file
itself. It does not touch ``data/raw/{STUDY}/datasets/`` or
``data/raw/{STUDY}/data_dictionary/``.

Capability gate details
-----------------------

The capability gate is intentionally conservative. The default allowlist
is:

* **Anthropic** — ``claude-opus-4-6+``, ``claude-sonnet-4-6+`` (older
  Sonnet struggles on multi-section CRFs)
* **OpenAI** — ``gpt-5+`` (GPT-4 family is borderline on complex
  CRFs)
* **Google** — ``gemini-2.5-pro+`` (Flash is excluded — good for
  chat, weaker on table-heavy PDFs)
* **NVIDIA NIM** — ``meta/llama-3.3-405b-instruct+`` only

**Ollama is excluded by default** regardless of model name —
historically local Ollama models cannot sustain a JSON-schema
response on a 30-page CRF. Operator opt-in via
``REPORTALIN_PDF_LLM_CAPABLE_MODELS`` if you've validated a specific
local model.

Note that the wizard's UI also enforces
:data:`ORCHESTRATOR_SUPPORTED_PROVIDERS` (anthropic + google),
which is narrower than the capability allowlist. An OpenAI or NVIDIA
model passes ``is_capable_model`` but the wizard will not surface
"Load Study" as an option for it — the underlying
``_extract_via_llm`` only has client wiring for anthropic + google.
This prevents a silent fall-through to snapshot.

Legacy raw-PDF API path
-----------------------

When ``REPORTALIN_PDF_EXTRACTION_MODE`` is unset (CLI default for
back-compat), :func:`scripts.extraction.extract_pdf_data._resolve_pdf_provider`
runs a two-part attestation gate:

1. Env flag ``REPORTALIN_PDF_PHI_FREE=1`` set in the runtime env.
2. Non-empty ``authorities/phi_free_pdfs.md`` operator-signed
   attestation note (under version control, audit-trail material).

If either is missing, the function raises ``ValueError`` with a
remediation message listing three alternatives:

a. Flip both attestations if the source PDFs are verified PHI-free
   (blank CRFs, protocol-only, MOP).
b. Use ``--pdf-source <path>`` with pre-extracted JSON files (no LLM
   call).
c. Skip the PDF leg entirely; the pipeline succeeds without it.

The legacy path then base64-encodes the entire PDF and ships it to
Anthropic's ``messages.stream`` or Google Gemini's
``generate_content`` for native PDF-document understanding. No
redaction is performed on the legacy path — the operator's
attestation is the safety story.

Output schema (per-form)
------------------------

Both paths write per-form JSON files at ``tmp/{STUDY}/pdfs/{stem}_variables.json``
(later atomically promoted to ``trio_bundle/pdfs/``):

.. code-block:: json

   {
     "form_name": "Form 1A - Index Case Screening",
     "source_pdf": "form_1a_index_screening.pdf",
     "version": "v1.0",
     "summary": "<brief>",
     "extraction_tier": "merged",
     "variables": {
       "ABBREVIATION": {
         "description": "...",
         "values": {"1": "Yes", "2": "No"},
         "depends_on": "PARENT_ABBREVIATION_OR_NULL",
         "condition": "Human-readable activation rule",
         "section_context": "<text>"
       }
     },
     "sections": {
       "SECTION_NAME": {
         "context": "<instruction text>",
         "variables": ["ABBREV1", "ABBREV2"]
       }
     }
   }

Within-file dedup (case-insensitive collision handling) and
cross-form dedup run after extraction; both are pure-function helpers
in :mod:`scripts.extraction.dedup`.

CLI usage
---------

.. code-block:: bash

   # Via Makefile (recommended)
   make pdf-extract

   # Via Python (direct module entry point — legacy path)
   uv run python -m scripts.extraction.extract_pdf_data

   # Force orchestrator from CLI
   REPORTALIN_PDF_EXTRACTION_MODE=llm uv run python main.py --pipeline

   # Force snapshot mode (no LLM, no API egress)
   REPORTALIN_PDF_EXTRACTION_MODE=snapshot uv run python main.py --pipeline

   # Skip the PDF leg via pre-extracted JSON
   uv run python main.py --pipeline --pdf-source /path/to/jsons/

Key files
---------

* :mod:`scripts.extraction.pdf_pipeline` — the orchestrator.
  Two-way merge, capability gate, idempotent cache, snapshot
  fallback.
* :mod:`scripts.extraction.extract_pdf_data` — the legacy raw-PDF
  API path; also hosts the dispatcher
  (``extract_pdfs_to_jsonl``).
* :mod:`scripts.utils.llm_capabilities` — model-name allowlist
  (``DEFAULT_CAPABLE_MODEL_PREFIXES``,
  :func:`scripts.utils.llm_capabilities.is_capable_model`).
* :mod:`scripts.security.phi_patterns` — the BLOCKING_PATTERNS used
  by the orchestrator's redaction step (and by the agent-output PHI
  gate).
* ``config.STUDY_SNAPSHOTS_DIR`` — the reviewed baseline location
  (``data/snapshots/{STUDY}/``); see
  :doc:`operations` for the maintenance protocol.

Testing
-------

PHI-critical tests for this surface:

* ``tests/security/test_pdf_redaction_pipeline.py`` — pre-LLM
  redaction, post-LLM re-scrub, merge contract, idempotent cache,
  two-way decision (LLM-merged or snapshot, never code-only).
* ``tests/security/test_llm_capabilities.py`` — capability allowlist,
  Ollama-excluded-by-default, env-override semantics.
* ``tests/test_pdf_phi_flag.py`` — legacy two-part attestation gate.
* ``tests/test_extract_pdf_data.py`` — dispatcher mode selection.

Downstream usage
----------------

* :func:`scripts.extraction.build_variables_reference.build_variables_reference`
  merges PDF extractions with the data dictionary into
  ``trio_bundle/variables.json`` (the consolidated schema the agent
  uses to validate variable names).
* The agent's ``cohort_builder`` and ``query_dataset`` tools read
  ``variables.json`` plus per-form ``*_variables.json`` to map
  user-friendly names back to dataset columns.

Licensing
---------

PDF extraction uses open-source libraries (``pypdf``, ``pdfplumber``)
under permissive licenses. No proprietary PDF tools are required.
