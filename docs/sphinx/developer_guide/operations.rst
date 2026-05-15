Operations
==========

Operational runbook for running, rebuilding, and verifying the RePORT AI Portal
pipeline. Deployment controls and release gates live in
:doc:`production_readiness`.

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

The Source-of-Truth policy YAMLs (``data/SoT/{STUDY}/{form}_policy.yaml``)
are produced by a standalone CLI that runs *before* the main pipeline. The
full behavior reference is in ``docs/runbook_sot_build.md``; a brief summary
follows.

.. code-block:: bash

   # Build SoT YAMLs — skips any that already exist on disk
   python -m scripts.source_truth.study_intake Indo-VAP

   # Force-overwrite existing YAMLs (loses human curation — use with care)
   python -m scripts.source_truth.study_intake Indo-VAP --force

   # Show all options
   python -m scripts.source_truth.study_intake --help

**Inputs:**
``data/raw/{STUDY}/annotated_pdfs/*.pdf`` and
``data/raw/{STUDY}/datasets/*.{xlsx,csv}``

**Outputs:**
``data/SoT/{STUDY}/{form}_policy.yaml`` (one per cleanly-aligned pair) and
``data/SoT/{STUDY}/human_review/SoT_intake_review.md`` (checklist for
unpaired / excluded files).

**Re-run policy:** skip-if-exists by default. Pass ``--force`` only when
regenerating after a known schema change or on a fresh checkout.

See ``docs/runbook_sot_build.md`` for the full behavior reference including
exclusion reason codes, threat model, duplicate-handling rules, and a
step-by-step Indo-VAP walkthrough.

Pipeline Run
------------

Full Pipeline (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make pipeline

Runs all steps in order: dictionary → dataset extraction → AMBER scrub
(eight-action catalog, rule + allowlist) → publish scrubbed dataset
files into the ``llm_source/`` GREEN zone → SoT-backed LLM source build
and reconciliation. The current LLM-visible outputs are
``llm_source/dataset_schema/files/`` plus
``llm_source/study_metadata/catalog.json`` and
``llm_source/study_metadata/evidence_packs/``.

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
     - Build Study Metadata Catalog and Evidence Packs from SoT policy
       YAMLs and the published dataset files. Stages promotion candidates
       under ``tmp/{STUDY}/staging/llm_source/``.
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

After ``make pipeline`` produces clean JSONL:

1. Manually inspect ``output/{STUDY}/llm_source/`` for any unexpected
   residual content
2. Spot-check dataset records:
   ``head -20 output/{STUDY}/llm_source/dataset_schema/files/*.jsonl``
3. Step 1.6 of the pipeline scrubs staged datasets in place via the
   8-action honest-broker catalog (:mod:`scripts.security.phi_scrub`).
   If residual content is found in llm_source/, inspect
   ``output/{STUDY}/audit/phi_scrub_report.json`` for the action counts,
   add the offending field pattern to ``scripts/security/phi_scrub.yaml``
   under the appropriate section (``drop_fields``, ``cap_fields``, etc.),
   and re-run ``make pipeline``.
4. Cross-check ``output/{STUDY}/audit/lineage_manifest.json`` — every
   raw input SHA-256 should have a corresponding trio artifact entry.

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
pattern. It reads from the llm_source bundle and provides study-specific
answers grounded in the Study Metadata Catalog, Evidence Packs, and
dataset metadata. Queries are tool-based and local: variable search uses
the catalog, evidence packs, and published dataset schemas; record-level
questions go through deterministic dataset or analysis tools.
Do not document or promise 100% answer accuracy. Improve accuracy by
adding reviewed SoT policy YAMLs, strengthening retrieval tests, and
measuring representative question/answer sets for correctness, grounding,
and retrieval relevance.

.. code-block:: bash

    make chat-cli   # CLI interactive REPL (or `python main.py --chat` directly)
    make chat       # Streamlit web UI (or `python main.py --web` directly)
