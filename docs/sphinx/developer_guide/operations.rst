Operations
==========

Operational runbook for running, rebuilding, and verifying the RePORT AI Portal
pipeline. Source Truth authoring details live in
:doc:`source_truth_build`; the audited cross-LLM extraction entry point
lives in :doc:`extract_to_llm_source`; deployment controls and release
gates live in :doc:`production_readiness`.

.. contents:: On this page
   :local:
   :depth: 2

Prerequisites
-------------

.. list-table::
   :header-rows: 1

   * - Requirement
     - Check
   * - Python 3.11+
     - ``python --version``
   * - uv package manager
     - ``uv --version``
   * - Dependencies synced
     - ``uv sync --all-groups``
   * - LLM provider configured
     - ``echo $LLM_PROVIDER`` (or set in ``config/config.yaml``)
   * - Study data in place
     - ``data/raw/{STUDY}/`` with ``datasets/`` and
       ``data_dictionary/``; reviewed SoT policies under
       ``data/SoT/{STUDY}/`` when rebuilding assistant metadata

SoT YAML Build
--------------

Runtime Source-of-Truth lean YAMLs are produced under
``output/{STUDY}/llm_source/source_truth/``. The generator uses the printed
PDF as the clinical authority and reads only dataset row-1 headers for
binding. Anchored calibration gold, when present, stays under
``data/SoT/{STUDY}/`` and is used only for diff/regression checks.

.. code-block:: bash

   # Generate and verify all PDF-backed runtime lean YAMLs
   make sot-generate-all STUDY=Indo-VAP

   # Single-form source pack and render for manual Stage 1-3 authoring
   make sot-source-pack STUDY=Indo-VAP FORM=6_HIV

   # Show all options
   python -m scripts.source_truth.study_intake --help

**Inputs:**
``data/raw/{STUDY}/annotated_pdfs/*.pdf`` and
``data/raw/{STUDY}/datasets/*.{xlsx,csv}``

**Outputs:**
``output/{STUDY}/llm_source/source_truth/{form}_policy.lean.yaml`` for each
PDF-backed form that passes the lean checker.

**Re-run policy:** ``make sot-generate-all`` is idempotent and overwrites only
after the generated candidate passes verification.

See :doc:`source_truth_build` for the full behavior reference including
single-form source packs, deterministic verifier gates, duplicate-handling
rules, and the step-by-step Indo-VAP walkthrough.

Pipeline Run
------------

Full Pipeline (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make pipeline

Runs the raw-data steps in order: dictionary → dataset extraction → AMBER
scrub (eight-action catalog, rule + allowlist) → publish scrubbed dataset
files into the ``llm_source/`` GREEN zone → audit lineage.

For a complete runtime bundle from scratch, prefer:

.. code-block:: bash

   make build-llm-source STUDY=Indo-VAP

That adds the SoT generation step before the raw-data pipeline. The current
LLM-visible outputs are ``llm_source/source_truth/``,
``llm_source/dataset_schema/files/``, and
``llm_source/dictionary_mapping/jsonl/``.

Audited Cross-LLM Extraction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use the skill CLI when a human or another LLM agent needs the
fail-closed wrapper around raw workbook extraction, PHI key preflight,
header-only approval, post-run verification, destruction attestation,
and terminal run status:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
     --study Indo-VAP

   uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
     --study Indo-VAP

The complete contract, exit codes, evidence files, and open hardening
items are in :doc:`extract_to_llm_source`.

Individual Steps
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Command
     - What it does
   * - ``make dictionary``
     - Load data dictionary
   * - ``make extract-datasets``
     - Dataset extraction into AMBER staging, run through the eight-action
       PHI scrub, then atomically promoted into the GREEN ``llm_source/``
   * - ``make build-llm-source``
     - Generate verified lean SoT YAMLs, then publish dictionary mappings,
       PHI-scrubbed dataset JSONL, audit ledgers, lineage, and the output
       signpost.
   * - ``make bundle``
     - Legacy compatibility alias for preparing the ``llm_source`` dictionary leg
   * - ``make chat``
     - Launch the Streamlit research-assistant UI (with setup wizard)
   * - ``make chat-cli``
     - Launch the CLI research-assistant (interactive REPL)

Serve
~~~~~

.. note::

   The AI Assistant chat interface is available via ``make chat-cli``
   (interactive CLI REPL) or ``make chat`` (Streamlit web UI). See
   AI Assistant below.

.. code-block:: bash

   make chat-cli   # CLI Research Assistant (interactive REPL)
   make chat       # Streamlit Research Assistant UI (with setup wizard)

Quickstart
~~~~~~~~~~

.. code-block:: bash

   make quickstart  # sync → pipeline

Artifact Rebuild
----------------

When schemas, SoT policies, the data dictionary, or the eight-action PHI scrub catalog
(``scripts/security/phi_scrub.yaml``) change:

.. code-block:: bash

   # Full rebuild
   make nuke && make quickstart

Cleanup
-------

.. code-block:: bash

   make clean       # Caches, sessions, stale logs (safe)
   make nuke        # Full reset: venv, output, indexes (confirmation required)

**Retention rules:**

- ``tests/fixtures/`` — never touched by cleanup
- ``data/raw/`` — manual cleanup only (source of truth)

Security Verification
---------------------

Dataset Promotion Protocol
~~~~~~~~~~~~~~~~~~~~~~~~~~

After ``make pipeline`` or the extraction skill publishes clean JSONL:

1. Run the deterministic verifier:
   ``uv run --all-groups python scripts/skills/extract_to_llm_source.py verify --study {STUDY}``.
2. Inspect ``output/{STUDY}/runs/{run_id}/verifier_report.json``.
3. Inspect ``output/{STUDY}/audit/phi_scrub_report.json`` for scrub
   action counts when a PHI finding blocks publish or verification.
4. Cross-check ``output/{STUDY}/audit/lineage_manifest.json``. Every
   raw input SHA-256 should have corresponding published ``llm_source``
   artifact hashes.
5. If residual PHI-like content is found in ``llm_source/``, update
   ``scripts/security/phi_scrub.yaml`` or the extraction logic, rebuild,
   and rerun verification. Do not hand-edit published JSONL.

Zone Enforcement
~~~~~~~~~~~~~~~~

Verify zone guards are active:

.. code-block:: bash

   uv run pytest tests/security/test_zone_guard.py -v

Quality Checks
--------------

.. code-block:: bash

   make test          # deterministic pytest subset
   make test-all      # full pytest suite
   make lint          # ruff
   make typecheck     # mypy
   make ci            # All quality checks
   make doc-freshness # Catch stale prose (vector-DB / "only zone" / wrong tool count / …)
   make docs          # Build sphinx HTML
   uv run pip-audit   # Dependency security audit

Debug and Troubleshooting
-------------------------

.. code-block:: bash

   make debug       # Pipeline + serve with DEBUG logging

Common issues:

- **No LLM configured:** Set ``LLM_PROVIDER`` env var or ``config/config.yaml``
- **Missing study data:** Ensure ``data/raw/{STUDY}/`` has the required
  subdirectories
- **Dependency issues:** ``uv lock --upgrade && uv sync --all-groups``
- **Stale artifacts:** ``make nuke && make quickstart``

Known Limitations
-----------------

- Single-study mode only — one study directory under ``data/raw/``
- LLM provider must be explicitly configured (no default)


AI Assistant
------------

The AI Assistant research assistant uses LangGraph with a ReAct agent
pattern. It reads from the ``llm_source`` bundle and provides
study-specific answers grounded in verified Source Truth YAML, dictionary
mappings, and published dataset schemas. Queries are tool-based and
local; record-level questions go through deterministic dataset or
analysis tools.
Do not document or promise 100% answer accuracy. Improve accuracy by
adding reviewed SoT policy YAMLs, strengthening retrieval tests, and
measuring representative question/answer sets for correctness, grounding,
and retrieval relevance.

.. code-block:: bash

    make chat-cli   # CLI interactive REPL (or `python main.py --chat` directly)
    make chat       # Streamlit web UI (or `python main.py --web` directly)
