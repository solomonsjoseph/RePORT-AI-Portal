Data Pipeline
=============

RePORT AI Portal processes clinical study data through a **4-tier honest-broker pipeline**
(see :doc:`../developer_guide/phi_architecture` and
``docs/irb_dossier/conformance_matrix.md``):

1. **RED — raw extraction** from ``data/raw/{STUDY}/`` (read-only by the extraction leg).
2. **AMBER — secure staging** in ``tmp/{STUDY}/`` with mode 0700 + umask 0077 +
   zero-fill teardown; all transformations (extract / scrub / cleanup / propagation)
   happen here before anything reaches the permanent output surface.
3. **GREEN — trio bundle** at ``output/{STUDY}/trio_bundle/`` — PHI-free by
   construction, plus ``audit/lineage_manifest.json`` as the single IRB-review artifact.
4. **GREEN-PROTECT — agent boundary** — every LLM tool return passes through the
   regex-first PHI gate + k-anonymity gate before the response reaches the user.

.. contents:: On this page
   :local:
   :depth: 2

Pipeline Architecture
---------------------

.. code-block:: text

   data/raw/{STUDY}/
   +-- data_dictionary/     --+
   +-- datasets/            --+-- Phase 1: Extract into staging
   +-- annotated_pdfs/      --+
            |
            v (all three legs write here first)
   tmp/{STUDY}/              [transient — removed on success]
   +-- datasets/
   +-- dictionary/
   +-- pdfs/
   +-- quarantine/           [rows missing subject_id, from phi_scrub]
            |
            v phi_scrub → dataset cleanup → propagation → atomic publish
   output/{STUDY}/trio_bundle/
   +-- dictionary/
   +-- datasets/
   +-- pdfs/
   output/{STUDY}/audit/              # dataset-only (PHI-bearing leg)
   +-- phi_scrub_report.json
   +-- dataset_cleanup_report.json
            |
            v
   Agent: --chat / --web             Phase 2: ReAct agent + 12 tools

Running the Pipeline
--------------------

Full pipeline:

.. code-block:: bash

   make pipeline

Individual phases:

.. code-block:: bash

   make dictionary
   make extract-datasets
   make pdf-extract

Quick start (sync + full pipeline):

.. code-block:: bash

   make quickstart

Phase 1: Extract & Promote
--------------------------

Dictionary Extraction
~~~~~~~~~~~~~~~~~~~~~

Discovers and parses all data dictionary and mapping files.

- **Input:** ``data/raw/{STUDY}/data_dictionary/`` (``.xlsx``, ``.xls``, ``.csv``)
- **Staging output:** ``tmp/{STUDY}/dictionary/``
- **Final output:** ``output/{STUDY}/trio_bundle/dictionary/`` (after publish)
- **Process:** Auto-discover files, read every sheet, detect multi-table
  boundaries, inject provenance metadata (``__sheet__``, ``__table__``,
  ``__source_file__``), write one JSONL per table into the staging workspace
- **Command:** ``make extract-dictionary``

Dataset Extraction
~~~~~~~~~~~~~~~~~~

Extracts structured data from Excel/CSV datasets into JSONL.

- **Input:** ``data/raw/{STUDY}/datasets/``
- **Staging output:** ``tmp/{STUDY}/datasets/``
- **Final output:** ``output/{STUDY}/trio_bundle/datasets/`` (after publish)
- **Process:** Extraction into the staging workspace; dataset cleanup then runs
  against staged artifacts (junk removal, duplicate merge) and emits
  ``output/{STUDY}/audit/dataset_cleanup_report.json``
- **Command:** ``make extract-datasets``

See :doc:`../developer_guide/data_extraction_datasets` for details.

PDF Extraction
~~~~~~~~~~~~~~

Extracts variable definitions from annotated CRF PDF forms using
``pypdf`` / ``pdfplumber`` (no LLM involved).

- **Input:** ``data/raw/{STUDY}/annotated_pdfs/``
- **Staging output:** ``tmp/{STUDY}/pdfs/``
- **Final output:** ``output/{STUDY}/trio_bundle/pdfs/`` (after publish)
- **Process:** Text extraction, form-field parsing, variable identification,
  deduplication, clean-PDF-variable normalization into the staging workspace
- **Command:** ``make extract-pdf``

See :doc:`../developer_guide/data_extraction_pdfs` for details.

PHI Scrub (Step 1.6) — 8-Action Honest-Broker Catalog
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Applies a deterministic PHI pass over staged datasets **before** any audit
output is written — so raw PHI never leaves ``tmp/`` into ``output/``. Eight
action classes evaluated in strict priority order (first match wins per field).

- **Input:** ``tmp/{STUDY}/datasets/*.jsonl`` (scrubbed in place)
- **Audit output:** ``output/{STUDY}/audit/phi_scrub_report.json`` (counts only)
- **Quarantine:** ``tmp/{STUDY}/quarantine/*.jsonl`` (rows missing ``subject_id``)
- **Key:** sidecar HMAC-SHA256 key at ``~/.config/report_ai_portal/phi_key``
  (mode ``0600``, outside the repo). Bootstrap with
  ``python -m scripts.security.phi_scrub bootstrap-key``.
- **Catalog:** ``scripts/security/phi_scrub.yaml`` ships ~200
  Indo-VAP-calibrated rules — 80 keep allowlist + 93 drop + 3 cap + 3
  generalize + 3 suppress_small_cell + 25 date + 20 id patterns.

**Priority dispatch** (``_scrub_row`` in ``scripts/security/phi_scrub.py``):

1. **keep** — allowlist short-circuits every other rule (clinical lab /
   medication / time-of-day / categorical indicators).
2. **birthdate** — posture-dependent: ``safe_harbor`` drops the field;
   ``limited_dataset`` falls through to rule 7 (date jitter) and requires
   ``authorities/phi_limited_dataset.md``.
3. **drop** — field removed entirely (names, Aadhaar / ABHA / PAN / voter /
   passport / DL / ration / ESIC / PM-JAY / Nikshay, contact info, exact
   geography, system timestamps, narrative / specify / comment fields, staff
   identifiers, batch / scan metadata).
4. **cap** — numeric values greater than threshold replaced with label
   (default age > 89 → ``"90+"``, HIPAA §164.514(b)(2)(i)(C)).
5. **generalize** — value-level categorical mapping (marital status →
   Married / Single / Other; facility type → Government / Private / Other).
6. **suppress_small_cell** — household-contact counts clamped to the
   configured threshold (default 5, ICMR §11.7 k-anonymity).
7. **date jitter** — per-subject deterministic SANT offset in
   ``[-max_jitter_days, +max_jitter_days]``; preserves every intra-subject
   interval exactly (survival / incidence / person-time analyses unaffected).
8. **id pseudonymize** — ``HMAC-SHA256(key, id)[:12]`` → ``SUBJ_<12hex>``.
   Deterministic cross-file linkage preserved; non-reversible without key.

Scrubbed rows carry ``_phi_scrubbed: "v1"``; sentinel
``tmp/{STUDY}/.phi_scrub_complete`` prevents accidental double-scrubbing on
restart. See :doc:`../developer_guide/architecture` for the full module
contract and ``docs/irb_dossier/conformance_matrix.md`` for the test evidence.

Cleanup Propagation and Publish
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

After all three extraction legs complete and dataset cleanup has run, the
pipeline propagates variable-drop decisions across legs and atomically
publishes staging to ``trio_bundle/``.

Steps (sequential, executed by ``main.py``):

1. **Propagation** (``cleanup_propagation.run_propagation``):

   - Reads ``output/{STUDY}/audit/dataset_cleanup_report.json``.
   - Computes the pruning set: variables removed from datasets that no longer
     survive in any remaining dataset (case-insensitive; provenance fields
     such as ``__sheet__``, ``__source_file__`` are excluded).
   - Rewrites ``tmp/{STUDY}/dictionary/`` JSONL, dropping entries for pruned
     variables (side-effect only — no audit report, dictionary carries no PHI).
   - Rewrites ``tmp/{STUDY}/pdfs/`` JSONL, dropping entries for pruned
     variables (side-effect only — no audit report, PDFs carry no PHI).

2. **Publish** (``_publish_staging``):

   - Iterates the three staging legs (datasets, dictionary, pdfs).
   - For each leg, attempts an atomic ``os.rename``; falls back to
     ``shutil.copytree`` when source and destination span filesystems.
   - ``trio_bundle/`` is fully consistent only after all three legs publish
     successfully.

3. **Variables reference** (``build_variables_reference``):

   - Runs after publish, reading the now-complete ``trio_bundle/``.

4. **Lineage manifest** (``emit_lineage_manifest`` — Step 4):

   - Walks raw inputs + published trio bundle + audit directory, recording
     SHA-256 + size + mtime per file, plus per-leg audit-report references
     and compliance posture.
   - Emits ``output/{STUDY}/audit/lineage_manifest.json`` — the single
     regulator-facing evidence artifact pairing every raw input hash with
     every published trio artifact hash.

5. **Staging cleanup** (``_cleanup_staging``):

   - **Securely** removes ``tmp/{STUDY}/`` on success — each staging file
     is overwritten with random bytes and fsynced before unlink to resist
     filesystem forensics.
   - On failure, ``tmp/{STUDY}/`` is **left in place** for operator inspection
     before the next run.

**Audit report schema** (all three reports share the same envelope):

.. code-block:: json

   {
     "study": "STUDY_NAME",
     "generated_utc": "2024-01-01T00:00:00Z",
     "leg": "datasets",
     "removed": [
       {
         "scope": "column",
         "name": "VAR_X",
         "file": "source_dataset.jsonl",
         "sheet": "Sheet1",
         "reason": "junk_column",
         "kept": false
       }
     ]
   }

The ``leg`` field is one of ``"datasets"``, ``"dictionary"``, or ``"pdfs"``.

Phase 2: Agent (Query-Time)
----------------------------

At query time, the ReAct agent uses 12 structured-data tools to answer
questions directly from the trio bundle. No chunking, embedding, or
vector index is needed. The canonical list lives in
:data:`scripts.ai_assistant.agent_tools.ALL_TOOLS`.

- **Tools:** ``search_variables``, ``find_variable_candidates``,
  ``get_variable_details``, ``list_forms``, ``get_form_variables``,
  ``query_dataset``, ``get_dataset_stats``, ``get_study_overview``,
  ``run_python_analysis``, ``cross_reference_variables``,
  ``run_study_analysis``, ``search_pdf_context``
- **Code runner:** Sandboxed Python runner with pandas, numpy, scipy, statsmodels,
  matplotlib
- **Interfaces:** CLI (``--chat``) and Streamlit web UI (``--web``)

PDF Extraction PHI-Safety Gate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

External-API PDF extraction (Anthropic Claude / Google Gemini) is treated as
a network egress of PHI by default. ``scripts/extraction/extract_pdf_data.py``
refuses to initialise an external client unless the operator explicitly
sets ``REPORTALIN_PDF_PHI_FREE=1`` attesting the source PDFs are PHI-free
(blank CRFs, protocol, MOP, annotation-only pages). Remediation paths:

- Set ``REPORTALIN_PDF_PHI_FREE=1`` (your signed assertion for the IRB).
- Use pre-extracted PHI-free JSON via ``--pdf-source <path>`` (no LLM call).
- Skip the PDF leg entirely — the pipeline succeeds without it.

Runtime Invariants
------------------

These hold at every pipeline boundary:

1. **Zone guards** enforce the four-tier boundary: raw → AMBER staging → GREEN
   trio bundle → GREEN-PROTECT agent. Pipeline-side guards
   (``scripts/security/secure_env.py``: ``assert_not_raw``, ``assert_write_zone``,
   ``assert_output_zone``, ``assert_trio_bundle_zone``) apply at ingest,
   staging, and publish. Agent-side guards
   (``scripts/ai_assistant/file_access.py``: ``validate_agent_read``,
   ``validate_agent_write``, ``validate_sandbox_write``) apply at every
   tool-code file read or write. Both layers use ``os.path.realpath`` +
   ``os.path.commonpath`` containment so symlinks and ``..`` traversal
   cannot escape. Violations raise ``ZoneViolationError`` (a
   ``PermissionError`` subclass).
2. **PHI scrub runs before any audit emission** so the dataset cleanup and
   propagation audits never contain raw subject IDs or raw dates.
3. **Every agent tool return passes through ``phi_gate_check``** — blocking
   findings trigger a redaction message rather than leaking raw PHI tokens.
4. **k-anonymity ≥ 5** is enforced on row-level responses via
   ``scripts/security/kanon_gate.py`` — equivalence classes smaller than the
   threshold suppress to ``"<5"``.
5. **Per-run integrity chain** — every raw input hashed to SHA-256; the hash
   rides in every row's ``_provenance`` + the lineage manifest.
6. **Log PHI hygiene** — ``scripts/utils/log_hygiene.install_phi_redactor``
   redacts subject IDs + Aadhaar / PAN / phone / email / pincode / SSN / dates
   from every log record before emit.

Step Cache
----------

Each pipeline step is cached. Re-running a step with unchanged inputs
is a no-op. To force re-execution:

.. code-block:: bash

   make clean    # Remove all outputs
   make pipeline # Full re-run

Next Steps
----------

- :doc:`configuration` for environment and config settings
- :doc:`../developer_guide/operations` for operational runbook
