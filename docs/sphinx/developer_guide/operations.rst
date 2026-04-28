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
     - ``data/raw/{STUDY}/`` with ``datasets/``, ``annotated_pdfs/``,
       ``data_dictionary/``

Pipeline Run
------------

Full Pipeline (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make pipeline

Runs all steps in order: dictionary → dataset extraction → AMBER scrub
(eight-action catalog, rule + allowlist) → atomic publish into the
``trio_bundle/`` GREEN zone → variables-reference build. PDFs are
processed in a separate leg (``make pdf-extract``); they are gated
behind ``REPORTALIN_PDF_PHI_FREE=1`` because annotated CRFs are
PHI-bearing by default.

Individual Steps
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Command
     - What it does
   * - ``make dictionary``
     - Load data dictionary
   * - ``make pdf-extract``
     - PDF extraction to JSONL (gated by ``REPORTALIN_PDF_PHI_FREE``;
       see :doc:`data_extraction_pdfs`)
   * - ``make extract-datasets``
     - Dataset extraction into AMBER staging, run through the eight-action
       PHI scrub, then atomically promoted into the GREEN ``trio_bundle/``
   * - ``make bundle``
     - Re-assemble the trio bundle from already-scrubbed staging artifacts
   * - ``make snapshot``
     - Save the reviewed snapshot baseline at
       ``data/snapshots/{STUDY}/`` after human review.
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

When schemas, the data dictionary, or the eight-action PHI scrub catalog
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

1. Manually inspect ``output/{STUDY}/trio_bundle/`` for any unexpected
   residual content
2. Spot-check dataset records:
   ``head -20 output/{STUDY}/trio_bundle/datasets/*.jsonl``
3. Step 1.6 of the pipeline scrubs staged datasets in place via the
   8-action honest-broker catalog (:mod:`scripts.security.phi_scrub`).
   If residual content is found in trio_bundle, inspect
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
pattern. It reads from the trio bundle and provides study-specific
answers grounded in the data dictionary, PDF extractions, and dataset
metadata.

.. code-block:: bash

    make chat-cli   # CLI interactive REPL (or `python main.py --chat` directly)
    make chat       # Streamlit web UI (or `python main.py --web` directly)

.. _snapshot-baseline-protocol:

Trio-Bundle Snapshot Maintenance
--------------------------------

A *snapshot baseline* is the single cleaned-and-verified trio bundle
saved under ``data/snapshots/{STUDY_NAME}/`` after human review. It
mirrors the layout of the live ``output/{STUDY}/trio_bundle/``:

.. code-block:: text

   data/snapshots/
   └── {STUDY_NAME}/             # e.g. data/snapshots/Indo-VAP/ — must match
       ├── datasets/             # config.STUDY_NAME exactly
       ├── dictionary/
       ├── pdfs/
       └── variables.json

**Purpose.** The reviewed baseline is the deterministic fallback
source for the portal:

1. **PDF orchestrator fallback.** When the wizard's "Load Study"
   runs and the PDF orchestrator's LLM tier is unavailable for a
   particular PDF (no API key, image-only PDF, capability gate fails,
   LLM call errors), the orchestrator reads
   ``data/snapshots/{STUDY}/pdfs/{stem}_variables.json`` instead of
   publishing a code-only heuristic guess.
2. **Failed or skipped PDF leg.** If the PDF extraction leg fails,
   is skipped, or creates no files during a full pipeline run, the
   pipeline restores ``data/snapshots/{STUDY}/`` over the live
   ``output/{STUDY}/trio_bundle/``.
3. **Use Existing Study.** The setup wizard's button restores the
   same reviewed baseline over the live trio bundle before enabling
   chat. The rest of ``output/{STUDY}/`` is left in place.

**Read posture.** The LLM agent must NOT read this directory. The
agent's read zone is restricted to ``output/{STUDY}/trio_bundle/``
and ``output/{STUDY}/agent/`` only (see
:func:`scripts.ai_assistant.file_access.validate_agent_read`).
Putting snapshots outside both zones is intentional — a stale
snapshot must never be served as live data.

The wizard and pipeline subprocess are the only legitimate readers.
Both use ``config.STUDY_SNAPSHOTS_DIR`` as the snapshot lookup root.

**Maintenance protocol.**

* Snapshots are PHI-scrubbed. Only files that have been through the
  full ``phi_scrub`` + ``kanon_gate`` chain belong here. Adding raw
  subject IDs or unscrubbed dates to a snapshot would defeat the
  entire purpose.
* Update by promoting from a verified production run. A maintainer
  runs ``make snapshot`` after manual review and references the
  ``lineage_manifest.json`` hash in the commit message for audit trail
  when the baseline is committed.
* Do not generate snapshots from ``--force`` runs without manual
  review. The whole value of a snapshot is the human verification
  step.

The repo's ``.gitignore`` allows ``data/snapshots/`` to be committed.
Files under this directory are study-team reviewed artifacts and are
not agent-owned state.

Lifecycle:

.. code-block:: bash

   make snapshot              # save current trio bundle as the reviewed baseline
   make snapshot FORCE=1      # overwrite the reviewed baseline after review
   make list-snapshots        # show whether the reviewed baseline exists
   make restore-study         # restore the reviewed baseline into trio_bundle/

Bumping the baseline is a maintainer action with audit-trail
implications. Restore points are intentionally not part of the
architecture.
