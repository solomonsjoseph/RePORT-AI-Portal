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

- Text-based extraction for searchable PDFs (pypdf/pdfplumber)
- Form field identification and structure parsing
- Variable name detection from form labels
- Section and page tracking for provenance

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
