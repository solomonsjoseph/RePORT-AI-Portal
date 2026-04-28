Data Pipeline
=============

Rewritten 2026-04-27 against the v0.20.0 code state. This page
describes what ``python main.py --pipeline`` (or the wizard's "Load
Study" button) actually does, in operator terms. For the deep
architecture see :doc:`../developer_guide/architecture` and
:doc:`../developer_guide/phi_architecture`.

Top-Level Flow
--------------

.. code-block:: text

   data/raw/{STUDY}/                          snapshots/{STUDY}/
   ├── datasets/                              ├── datasets/    (tracked baseline,
   ├── data_dictionary/                       ├── dictionary/   LLM-INVISIBLE,
   └── annotated_pdfs/                        ├── pdfs/         per-PDF fallback
                                              └── variables.json   for the
                                                                  PDF orchestrator)
            │
            │   STEP 0/1/1.5: PARALLEL extraction (3-worker ThreadPoolExecutor — PR #18)
            │     ├── Dictionary leg → tmp/{STUDY}/dictionary/
            │     ├── Datasets leg   → tmp/{STUDY}/datasets/
            │     └── PDFs leg       → tmp/{STUDY}/pdfs/   (orchestrator: pdfplumber
            │                                              code path + redacted-text
            │                                              LLM merge + per-PDF
            │                                              snapshot fallback)
            │   (join: cleanup chain runs serially after all three legs land)
            ▼
   tmp/{STUDY}/   (AMBER zone — mode 0700, umask 0077, optional tmpfs)
            │
            │   STEP 1.6: PHI scrub (datasets only — 8-action catalog)
            │     date jitter (SANT) + HMAC-SHA256 ID pseudonymization +
            │     drop / cap / generalize / suppress_small_cell / birthdate / keep
            │     ↓ emits output/{STUDY}/audit/phi_scrub_report.json
            │
            │   STEP 1.7: Dataset cleanup (junk removal + duplicate merge)
            │     ↓ emits output/{STUDY}/audit/dataset_cleanup_report.json
            │
            │   STEP 1.8: Cleanup propagation (dataset drops mirror into dict + PDF legs)
            │     ↓ emits output/{STUDY}/audit/{dictionary,pdfs}_cleanup_report.json
            ▼
   STEP 2: Atomic publish (per-leg rename) → output/{STUDY}/trio_bundle/
            ├── datasets/             ← LLM read zone (1 of 2; the other is
            ├── dictionary/             output/{STUDY}/agent/)
            └── pdfs/
            │
            │   STEP 3: Build variables.json (variables_reference.py reads
            │   the published trio_bundle/ — must come AFTER publish)
            ▼
   output/{STUDY}/trio_bundle/variables.json
            │
            │   STEP 4: Lineage manifest (raw SHA-256 ↔ published SHA-256 pairs +
            │   PHI-key fingerprint + compliance posture)
            │     ↓ emits output/{STUDY}/audit/lineage_manifest.json
            ▼
   STEP 5: Output signpost (regenerate output/{STUDY}/README.md)
            │
            ▼
   AMBER cleanup: secure_remove_tree(tmp/{STUDY}/) on success
   (preserved on failure for forensic inspection)

The agent reads ``output/{STUDY}/trio_bundle/`` (and
``output/{STUDY}/agent/``) — and **only** those two. Audit, telemetry,
staging, raw, and the snapshot baseline at ``snapshots/{STUDY}/`` are
hard-rejected by
:func:`scripts.ai_assistant.file_access.validate_agent_read`.

Running the pipeline
--------------------

Full pipeline:

.. code-block:: bash

   python main.py --pipeline             # CLI
   make pipeline                         # Makefile alias
   make chat                             # Streamlit wizard → click "Load Study"

Selective skip:

.. code-block:: bash

   python main.py --pipeline --skip-dictionary  # only datasets + PDFs
   python main.py --pipeline --skip-datasets    # only dictionary + PDFs

Force re-run (bypass step-cache):

.. code-block:: bash

   python main.py --pipeline --force

Step-by-step
------------

Step 0 — Dictionary loading
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reads ``data/raw/{STUDY}/data_dictionary/*.{xlsx,csv}`` and writes
per-file ``*.jsonl`` files into ``tmp/{STUDY}/dictionary/``. Carries no PHI.
Implementation: :func:`scripts.extraction.load_dictionary.load_study_dictionary`.

Step 1 — Dataset extraction (with hash-based step cache)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Walks ``data/raw/{STUDY}/datasets/*.{xlsx,csv}`` and converts each row to a
JSON record in ``tmp/{STUDY}/datasets/``. Records that fail validation
are recorded as "dropped events" so Step 1.8 can mirror them into the
dictionary + PDF legs.

Skip semantics: a hash-based manifest at
``output/{STUDY}/audit/manifests/dataset_processing.json`` records
``SHA-256`` of every input file; if the hashes are unchanged AND
``trio_bundle/datasets/`` still has output, this step is skipped.

Implementation: :func:`scripts.extraction.dataset_pipeline.process_datasets`.

Step 1.5 — PDF preparation (three branches)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The most failure-prone leg, with the most diagnostics:

* **--pdf-source <path>** — the operator points at a directory of
  pre-extracted JSON files (e.g. snapshots from another study run).
  ``main.py`` runs ``assert_not_raw()`` to confirm the source is not
  in RED, then atomic-copies each ``*_variables.json`` into
  ``tmp/{STUDY}/pdfs/``. No LLM call.
* **Automatic** (no ``--pdf-source``, ``data/raw/{STUDY}/annotated_pdfs/*.pdf``
  present): :func:`scripts.extraction.extract_pdf_data.extract_pdfs_to_jsonl`
  dispatches based on ``REPORTALIN_PDF_EXTRACTION_MODE``:

  - ``llm`` (the wizard's "Load Study" default) — runs the
    :mod:`scripts.extraction.pdf_pipeline` orchestrator. pdfplumber
    code path + redacted-text LLM call merged via ``_merge`` + per-PDF
    fallback to ``snapshots/{STUDY}/pdfs/`` when LLM unavailable.
  - ``snapshot`` — skip the LLM entirely; publish the snapshot
    baseline verbatim.
  - unset (CLI default for back-compat) — legacy raw-PDF API path.
    Refused unless the operator opts in via ``REPORTALIN_PDF_PHI_FREE=1``
    *and* a non-empty attestation note at ``authorities/phi_free_pdfs.md``.

* **No PDFs available** — log a detailed diagnostic (missing dir vs
  empty dir vs all-failed) and continue without a PDF leg. The
  operator can pass ``--pdf-source`` next time.

PHI scrub does NOT run on PDFs — the orchestrator path redacts before
the LLM call, and the legacy gated path attests PHI-free.

Step 1.6 — PHI scrub (datasets only)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Operates on ``tmp/{STUDY}/datasets/*.jsonl`` BEFORE Step 1.7 cleanup.
Eight action classes evaluated in strict priority order:

1. **keep** — pass through (only for confirmed non-PHI columns)
2. **birthdate** — replace with ``birthyear`` only
3. **drop** — null out
4. **cap** — clamp at a quantile
5. **generalize** — bucket into ranges (e.g. age → age band)
6. **suppress_small_cell** — null when the cohort cell has < N rows
7. **date_jitter** — per-subject deterministic SANT date jitter so
   within-subject visit intervals are preserved but absolute dates
   are shifted
8. **hmac_pseudonymize** — replace IDs with
   ``HMAC-SHA256(key, value)`` truncated; key lives at
   ``~/.config/report_ai_portal/phi_key`` (mode ``0600``, outside the
   repo, never committed)

The scrub writes its sanitised JSONL back into ``tmp/{STUDY}/datasets/``
in place. Audit at ``output/{STUDY}/audit/phi_scrub_report.json``
contains counts only — no row contents.

Implementation: :func:`scripts.security.phi_scrub.run_scrub`.

Step 1.7 — Dataset cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~

Operates on the now-scrubbed staging tree. Removes junk rows, merges
duplicate records, records every action in
``output/{STUDY}/audit/dataset_cleanup_report.json``.

Implementation: :func:`scripts.extraction.dataset_cleanup.clean_trio_datasets`.

Step 1.8 — Cleanup propagation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Whatever variables Step 1.7 dropped get mirrored into the dictionary
leg and the PDF leg in staging. Keeps the published trio bundle
internally consistent — no dictionary entry or PDF form variable
referencing a column that was dropped from the dataset.

Implementation: :func:`scripts.extraction.cleanup_propagation.run_propagation`.

Step 2 — Publish (atomic per-leg rename)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For each leg:

1. Call :func:`scripts.security.secure_env.assert_output_zone` — the
   destination MUST be under ``output/{STUDY}/``.
2. If the existing ``trio_bundle/<leg>/`` exists,
   :func:`scripts.utils.secure_staging.secure_remove_tree` (zero-fill
   + ``fsync`` + unlink). Republishing must not leave forensically
   recoverable old bytes.
3. **Atomic rename** ``staging_dir`` → ``trio_dir``. Same-filesystem
   = a single inode swap. Cross-filesystem (e.g. tmpfs staging + disk
   trio) falls back to ``shutil.copytree`` + ``shutil.rmtree``.
4. Empty staging legs leave the existing trio leg untouched.

Step 3 — Build variables.json
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Walks the published ``trio_bundle/`` and produces
``trio_bundle/variables.json`` — the consolidated variable schema the
agent's ``cohort_builder`` uses to validate variable names in
queries. **Must run after Step 2** because it scans the *published*
tree, not staging.

Implementation:
:func:`scripts.extraction.build_variables_reference.build_variables_reference`.

Step 4 — Lineage manifest
~~~~~~~~~~~~~~~~~~~~~~~~~

Pairs the SHA-256 of every raw input with the SHA-256 of every
published artifact, plus the PHI-scrub posture, plus a SHA-256
fingerprint of the HMAC PHI key. **The single artifact an IRB / IEC
reviewer reads to verify the entire raw → scrub → publish chain
without seeing any patient data.**

Output: ``output/{STUDY}/audit/lineage_manifest.json``.

Step 5 — Output signpost + secure cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Re-emits a plain-English ``output/{STUDY}/README.md`` describing the
layout for someone who opens the directory cold (sysadmin, auditor,
future maintainer). Then runs ``secure_remove_tree`` over the AMBER
``tmp/{STUDY}/`` workspace. **If any earlier step exited non-zero,
this cleanup is skipped on purpose** so staging is preserved for
forensic inspection.

Output structure on success
---------------------------

.. code-block:: text

   data/raw/{STUDY}/      # untouched (RED)
   tmp/{STUDY}/           # gone (securely deleted)
   output/{STUDY}/
   ├── trio_bundle/                      # GREEN — LLM read zone
   │   ├── datasets/*.jsonl              # PHI-scrubbed
   │   ├── dictionary/*.json
   │   ├── pdfs/*_variables.json         # tier: merged | snapshot | empty
   │   └── variables.json                # consolidated schema
   ├── audit/                            # AUDIT — counts only; LLM hard-rejected
   │   ├── lineage_manifest.json
   │   ├── phi_scrub_report.json
   │   ├── dataset_cleanup_report.json
   │   ├── dictionary_cleanup_report.json
   │   ├── pdfs_cleanup_report.json
   │   └── telemetry/events.jsonl
   ├── agent/                            # agent-owned operational state
   │   ├── analysis/                     # generated .py + outputs
   │   ├── conversations/
   │   └── restore_points/               # gitignored multi-named bundle copies
   └── README.md                         # output signpost

Plus the version-controlled tracked baseline at
``snapshots/{STUDY}/`` (LLM-invisible — not under ``output/``, not
inside any LLM-readable zone) that the orchestrator's per-PDF
fallback reads.

The audit envelope
------------------

The ``output/{STUDY}/audit/`` tree is the single point of contact
between the runtime and the IRB-reviewer-facing world. Properties:

* **Counts-only.** No row contents, no before/after pairs, no subject
  identifiers. Every report file records aggregate counts and per-rule
  applications.
* **LLM-invisible.** ``validate_agent_read`` rejects any path under
  ``audit/``.
* **Hash-anchored.**
  ``output/{STUDY}/audit/lineage_manifest.json`` cryptographically
  links every raw input to every published artifact.

Failure semantics
-----------------

* Any extraction-leg crash that is NOT the PDF leg fails the run
  immediately (``sys.exit(1)``). Cleanup chain doesn't run; AMBER
  staging is preserved.
* PDF leg crash is logged at WARNING and the run proceeds without
  PDF outputs. The detailed log line names the failure mode (missing
  source dir, empty dir, all-files-failed with per-file errors,
  exception trace).
* PHI scrub failure (e.g., missing key, malformed YAML config)
  fails the run immediately.
* Publish step assertion failure (e.g., misconfigured destination
  outside ``output/``) fails immediately.
* On any non-zero exit, AMBER staging at ``tmp/{STUDY}/`` is
  preserved — operator inspects, fixes, re-runs.

Where To Go Next
----------------

* :doc:`../developer_guide/architecture` — full system architecture.
* :doc:`../developer_guide/data_extraction_pdfs` — deep dive on the
  PDF orchestrator (PR #15).
* :doc:`configuration` — env flags, including
  ``REPORTALIN_PDF_EXTRACTION_MODE`` and the PHI-safety three.
* :doc:`../developer_guide/operations` — operational playbook,
  including the snapshot-baseline maintenance protocol.
* ``docs/irb_dossier/phi_walkthrough.md`` (outside the Sphinx tree)
  — the IRB-grade walkthrough with regulatory mapping.
