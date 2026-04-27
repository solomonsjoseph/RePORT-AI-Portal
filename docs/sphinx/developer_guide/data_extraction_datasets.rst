Dataset Extraction
==================

Extracts tabular clinical data from Excel and CSV files into JSONL format
via the study's staging workspace, which is then atomically promoted into
the trio bundle.

.. contents:: On this page
   :local:
   :depth: 2

Overview
--------

Dataset extraction reads raw study data files from
``data/raw/{STUDY_NAME}/datasets/``, normalises their rows, and writes the
resulting JSONL into the study's **AMBER staging workspace**
(``tmp/{STUDY_NAME}/datasets/`` by default; or ``/dev/shm/{STUDY}/`` when
``REPORTALIN_TMPFS_STAGING=1``). The staged JSONL is then run through
``phi_scrub.run_scrub`` (**Step 1.6**, eight-action catalog defined in
``scripts/security/phi_scrub.yaml``) before any audit artifact is
written. A subsequent publish step atomically promotes the now-PHI-free
staging bundle into ``output/{STUDY_NAME}/trio_bundle/datasets/``. PHI
handling is fully covered by ``scripts/security/`` (rule + allowlist;
not Presidio, not NER-by-default — see ADR-004 in
``developer_guide/decisions.rst``).

Data Flow
---------

.. code-block:: text

   data/raw/{STUDY}/datasets/*.xlsx
                 │
                 ▼
   dataset_pipeline.py  →  tmp/{STUDY}/datasets/*.jsonl   (staging)
                 │
                 ▼  (atomic publish)
   output/{STUDY}/trio_bundle/datasets/*.jsonl

Source
------

- **Path:** ``data/raw/{STUDY_NAME}/datasets/``
- **Formats:** ``.xlsx``, ``.xls``, ``.csv``
- Auto-discovered — no manual file list needed

Output
------

- **Location:** ``output/{STUDY_NAME}/trio_bundle/datasets/`` (``config.TRIO_DATASETS_DIR``)
- **Format:** One JSONL file per source file/sheet
- **Deterministic:** ``sort_keys=True, ensure_ascii=False``
- **Provenance:** Every record includes ``__source_file__``, ``__sheet__``,
  ``__row_index__`` metadata

JSONL Record Schema
-------------------

Each line is a JSON object:

.. code-block:: json

   {
     "FIELD_A": "value",
     "FIELD_B": 42,
     "__source_file__": "enrollment.xlsx",
     "__sheet__": "Sheet1",
     "__row_index__": 0
   }

Zone Enforcement
----------------

- Extraction outputs must live under ``output/`` — never under ``data/``
- ``secure_env.assert_not_raw()`` rejects any write path that resolves
  inside ``data/raw/``
- ``secure_env.assert_output_not_in_data()`` prevents accidental writes
  into the raw data tree

CLI Usage
---------

.. code-block:: bash

   # Via Makefile (recommended)
   make extract-datasets

   # Via Python
   uv run python -c "from scripts.extraction.dataset_pipeline import extract_datasets; extract_datasets()"

Downstream Handoff
------------------

JSONL files are written first to ``tmp/{STUDY}/datasets/`` (the AMBER
staging workspace created by ``scripts/utils/secure_staging.prepare_staging``
with mode 0700 + umask 0077). Every row carries a full ``_provenance``
dict (raw_sha256, pipeline_version, extraction_engine, source_file,
sheet_name, row_index, study_name, extraction_utc). The PHI scrubber
(:mod:`scripts.security.phi_scrub`) then runs in place as Step 1.6
BEFORE any audit is emitted. After cleanup (Step 1.7) and cleanup
propagation (Step 1.8), ``_publish_staging`` atomically renames the
staging datasets dir into ``output/{STUDY}/trio_bundle/datasets/``. A
per-run ``audit/lineage_manifest.json`` then pairs every raw input
SHA-256 with every published JSONL SHA-256.

Key Files
---------

- ``scripts/extraction/dataset_pipeline.py`` — main extraction logic
- ``scripts/extraction/io/`` — atomic write helpers, file discovery
- ``config.py`` — ``TRIO_DATASETS_DIR``, ``DATASETS_DIR``
