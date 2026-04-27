PDF Extraction
==============

Extracts structured data from annotated PDF case report forms (CRFs) into
JSONL format.

.. contents:: On this page
   :local:
   :depth: 2

Overview
--------

PDF extraction reads annotated clinical forms from
``data/raw/{STUDY_NAME}/annotated_pdfs/`` and writes structured JSONL records
to ``output/{STUDY_NAME}/trio_bundle/pdfs/``.

PDF forms contain clinical field definitions and form structures, **not raw
patient data**. They are forwarded directly to the Trio bundle.

Data Flow
---------

.. code-block:: text

   data/raw/{STUDY}/annotated_pdfs/*.pdf
                 │
                 ▼
   extract_pdf_data.py  →  output/{STUDY}/trio_bundle/pdfs/

Source
------

- **Path:** ``data/raw/{STUDY_NAME}/annotated_pdfs/``
- **Format:** Annotated PDF forms (CRFs)
- Auto-discovered — no manual file list needed

Output
------

- **Path:** ``output/{STUDY_NAME}/trio_bundle/pdfs/``
- **Format:** One JSONL file per PDF
- **Deterministic:** ``sort_keys=True, ensure_ascii=False``

JSONL Record Schema
-------------------

Each line represents a field or section extracted from the PDF:

.. code-block:: json

   {
     "variable_name": "IS_AGE",
     "label": "Age at enrollment",
     "form_name": "Baseline CRF",
     "section": "Demographics",
     "field_type": "numeric",
     "__source_file__": "baseline_form.pdf",
     "__page__": 2
   }

Extraction Capabilities
-----------------------

- Text-based extraction for searchable PDFs (pypdf in the legacy
  raw-PDF API path; pdfplumber in the orchestrator's code path)
- Form field identification and structure parsing
- Variable name detection from form labels
- Section and page tracking for provenance

Two-way PDF Orchestrator (``pdf_pipeline.py``, PR #15)
-------------------------------------------------------

The wizard's "Load Study" button selects this path; CLI users opt in
via ``REPORTALIN_PDF_EXTRACTION_MODE=llm``. Per PDF:

1. **Code path (always runs).** ``pdfplumber.open()`` extracts text +
   a heuristic variable candidate.
2. **Capability + provider gate.**
   :func:`scripts.utils.llm_capabilities.is_capable_model` enforces a
   model allowlist (Claude Opus/Sonnet 4.6+, GPT-5+, Gemini 2.5 Pro,
   Llama 3.3 405B) AND
   :data:`scripts.extraction.pdf_pipeline.ORCHESTRATOR_SUPPORTED_PROVIDERS`
   restricts the LLM tier to anthropic + google (where the actual
   client wiring exists).
3. **Redact-then-call.** Extracted text is scrubbed via the existing
   PHI catalog (``phi_patterns.BLOCKING_PATTERNS``); a defensive
   ``_assert_no_raw_phi_in_payload`` re-checks and raises if any
   blocking pattern survives. Only the redacted text reaches the LLM.
4. **Merge.** :func:`scripts.extraction.pdf_pipeline._merge` reconciles
   the LLM response with the code candidate (LLM wins on field-level
   conflicts; code fills in vars the LLM missed). The LLM response is
   also re-scrubbed via :func:`phi_safe.guard_text` before merge.
5. **Idempotent cache.** Keyed on
   ``SHA-256(pdf_bytes || provider || model || phi_scrub.yaml hash)``;
   stored at ``tmp/{STUDY}/.pdf_cache/`` (mode 0600). Editing the PHI
   scrub config invalidates every cache entry.
6. **Snapshot fallback per-PDF.** When any of (capable model, API
   key, code-path text non-empty, LLM call success) is missing, the
   orchestrator publishes the version-controlled baseline at
   ``snapshots/{STUDY}/pdfs/{stem}_variables.json`` instead. **Code-only
   output is never an acceptable result** — heuristic-only metadata is
   considered too unreliable to publish without LLM oversight.

CLI Usage
---------

.. code-block:: bash

   # Via Makefile (recommended)
   make pdf-extract

   # Via Python (direct module entry point)
   uv run python -m scripts.extraction.extract_pdf_data

Downstream Usage
----------------

PDF extractions are consumed by:

1. **Variables reference builder** (``scripts/extraction/build_variables_reference.py``)
   — merged with the data dictionary to produce ``variables.json``
2. **Agent tools** (``scripts/ai_assistant/agent_tools.py``) — accessed via structured-data tools

Key Files
---------

- ``scripts/extraction/extract_pdf_data.py`` — main PDF extraction
- ``config.py`` — ``ANNOTATED_PDFS_DIR``, ``PDF_EXTRACTIONS_DIR``

Licensing
---------

PDF extraction uses open-source libraries (pypdf, pdfplumber) with
permissive licenses. No proprietary PDF tools are required.
