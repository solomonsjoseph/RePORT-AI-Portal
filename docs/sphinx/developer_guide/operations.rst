Operations
==========

Operational runbook for running, rebuilding, and verifying the RePORT AI Portal
study-preparation workflow. Source Truth authoring details live in
:doc:`source_truth_build`; the audited dataset-publish child skill lives in
:doc:`extract_to_llm_source`; deployment controls and release gates live in
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
       ``data_dictionary/`` and ``annotated_pdfs/`` when Source Truth is
       required

Authoritative session-notes spec (Notes 1–22, GR-1): the Q&A session notes file
``now-we-are-going-playful-dove.md`` (maintainer copy under ``~/.claude/plans/``).
Implementation tracker: ``docs/plans/pipeline_redesign_implementation_plan.md``.

Plugin Study Preparation
------------------------

The active full workflow is the portable plugin bundle at
``plugins/report-ai-study-pipeline/``. Launch via ``make study STUDY=<name>``
(the 10-phase orchestrator): config preflight → header ∥ dictionary extraction
→ **dataset-deduplication** (raw-file tiers, Note 4) → SoT ∥ PHI classify ∥
extract → scrub → verify → PHI guard gate → promote → snapshot.

Legacy ``excel-duplicate-handler`` is not invoked by the orchestrator
(superseded by ``dataset-deduplication`` at phase 2).

The plugin does not own the data dictionary. Dictionary extraction stays in
``main.py`` and ``scripts.extraction.load_dictionary``.

SoT Set Build
-------------

Runtime Source Truth sets are produced under
``output/{STUDY}/llm_source/SoT/<pair>/``. The generator uses the printed
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

**Outputs (LLM-facing):**
``output/{STUDY}/llm_source/SoT/{pair}/joined/{form}_joined_query_view.yaml``
for each PDF-backed form that passes the checker. Intermediate policy/schema
files under ``pdf/`` and ``dataset/`` are construction artifacts only.

**Re-run policy:** ``make sot-generate-all`` is idempotent and overwrites only
after the generated candidate passes verification.

See :doc:`source_truth_build` for the full behavior reference including
single-form source packs, deterministic verifier gates, duplicate-handling
rules, and the step-by-step Indo-VAP walkthrough.

Study Preparation Run
---------------------

Web UI Entry Point
~~~~~~~~~~~~~~~~~~

Click **Load Study** in the setup wizard to activate the
``report-ai-study-pipeline`` plugin. The plugin runs duplicate handling,
Source Truth generation, and dataset publishing in order, then the wizard
requires a complete ``llm_source/`` bundle before chat can start. If raw
dictionary files are present, the bundle check also requires published
``llm_source/dictionary_mapping/jsonl/`` output from the host dictionary
loader.

Host Publish Path (inside the orchestrator)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make study STUDY=Indo-VAP

There is no standalone host-pipeline entry point. ``make study`` runs the
orchestrator, which drives the raw-data publish via the
``dataset-to-llm-source`` supervisor (``scripts/skills/extract_to_llm_source.py``)
calling ``scripts/pipeline/host_pipeline.py`` in-lock: dictionary ->
dataset extraction -> AMBER scrub (nine-action catalog, rule + allowlist) ->
publish scrubbed dataset files into the ``llm_source/`` GREEN zone -> audit
lineage. Duplicate preflight and Source Truth worker delegation are the
surrounding orchestrator phases.

For a repo-local rebuild of generated outputs, use:

.. code-block:: bash

   make rebuild-llm-source STUDY=Indo-VAP

That removes generated ``llm_source/`` and study staging first, then re-runs
the orchestrator (SoT generation followed by the in-lock host publish path).
The current LLM-visible outputs are ``llm_source/SoT/``,
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
     - Publish the dictionary mapping leg into ``llm_source/`` without
       running dataset extraction
   * - ``make extract-datasets``
     - Dataset extraction into AMBER staging, run through the nine-action
       PHI scrub, then atomically promoted into the GREEN ``llm_source/``
   * - ``make study STUDY=<name>``
     - Run the full 10-phase orchestrator: generate verified SoT
       policy/schema/joined sets, then publish dictionary mappings,
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

   make quickstart  # sync -> host publish path

Artifact Rebuild
----------------

When schemas, SoT policies, the data dictionary, or the nine-action PHI scrub catalog
(``scripts/security/phi_scrub.yaml``) change:

.. code-block:: bash

   # Full generated-output rebuild
   make nuke && make rebuild-llm-source STUDY=Indo-VAP

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

After **Load Study**, ``make study STUDY=<name>``, or the extraction skill
publishes clean JSONL:

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

   make debug       # Host publish path with DEBUG logging

Common issues:

- **No LLM configured:** Set ``LLM_PROVIDER`` env var or ``config/config.yaml``
- **Missing study data:** Ensure ``data/raw/{STUDY}/`` has the required
  subdirectories
- **Dependency issues:** ``uv lock --upgrade && uv sync --all-groups``
- **Stale artifacts:** ``make nuke && make rebuild-llm-source STUDY={STUDY}``

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
