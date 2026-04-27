Operations
==========

Operational runbook for running, rebuilding, and verifying the RePORT AI Portal
pipeline.

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
     - Save a restore point of the current trio bundle to
       ``output/{STUDY}/agent/restore_points/`` (gitignored). The
       version-controlled tracked baseline at ``snapshots/{STUDY}/`` is
       maintainer-curated by hand — see :ref:`snapshot-baseline-protocol`
       below.
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

   make test          # pytest — 703 deterministic tests (excludes agent-tools, agent-graph, CLI, telemetry)
   make test-all      # pytest — 775 full suite
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

This project keeps two distinct copy-of-the-trio-bundle tiers. They
look similar on disk and the CLI commands sound interchangeable, but
they serve different purposes and have different lifecycles. **Do not
confuse the two.**

Tier 1 — Tracked baseline (``snapshots/{STUDY}/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A *snapshot baseline* is a cleaned-and-verified trio bundle committed
to version control at ``snapshots/{STUDY_NAME}/``. Each
per-study subdirectory mirrors the layout of the live
``output/{STUDY}/trio_bundle/``:

.. code-block:: text

   snapshots/
   └── {STUDY_NAME}/             # e.g. snapshots/Indo-VAP/ — must match
       ├── datasets/             # config.STUDY_NAME exactly
       ├── dictionary/
       ├── pdfs/
       └── variables.json

**Purpose.** The tracked baseline is the **deterministic fallback
source** for the pipeline:

1. **PDF orchestrator fallback.** When the wizard's "Load Study"
   runs and the PDF orchestrator's LLM tier is unavailable for a
   particular PDF (no API key, image-only PDF, capability gate fails,
   LLM call errors), the orchestrator reads
   ``snapshots/{STUDY}/pdfs/{stem}_variables.json`` instead of
   publishing a code-only heuristic guess.
2. **Network-isolated runs.** Operators on hardened hosts without
   LLM access can run ``python main.py --pipeline`` and the
   pipeline will populate ``trio_bundle/pdfs/`` from these snapshots
   so the agent has something to answer questions against.

**Read posture.** The LLM agent must NOT read this directory. The
agent's read zone is restricted to ``output/{STUDY}/trio_bundle/``
and ``output/{STUDY}/agent/`` only (see
:func:`scripts.ai_assistant.file_access.validate_agent_read`).
Putting snapshots outside both zones is intentional — a stale
snapshot must never be served as live data.

The wizard's "Load Study" subprocess is the only legitimate reader.
The pipeline's PDF orchestrator imports
``config.STUDY_SNAPSHOTS_DIR`` and uses it as the snapshot lookup
root.

**Maintenance protocol.**

* Snapshots are PHI-scrubbed. Only files that have been through the
  full ``phi_scrub`` + ``kanon_gate`` chain belong here. Adding raw
  subject IDs or unscrubbed dates to a snapshot would defeat the
  entire purpose.
* Update by promoting from a verified production run. A maintainer
  copies ``output/{STUDY}/trio_bundle/`` →
  ``snapshots/{STUDY}/`` after manual review, commits, and references
  the ``lineage_manifest.json`` hash in the commit message for audit
  trail.
* Do not generate snapshots from ``--force`` runs without manual
  review. The whole value of a snapshot is the human verification
  step.

The repo's ``.gitignore`` explicitly tracks ``snapshots/``. Files
under this directory ARE committed.

Tier 2 — Operator restore points (``output/{STUDY}/agent/restore_points/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Multi-named, gitignored, agent-writable copies of the trio bundle
saved by ``scripts.utils.snapshots.create_snapshot``. Used for
crash-recovery during dev. Lifecycle:

.. code-block:: bash

   make snapshot                          # save current trio bundle as a restore point
   make list-snapshots                    # list available restore points
   make restore-study SNAPSHOT=<name>     # roll trio_bundle/ back to a restore point

Restore points are scratch storage — never read by the pipeline,
never the source of truth, never committed to git. They sit beside
``analysis/`` and ``conversations/`` under the agent state tree
because they are agent-owned operational state.

When NOT to use which
~~~~~~~~~~~~~~~~~~~~~

* Need a per-PDF fallback for "Load Study" on a network-isolated
  host? → Tracked baseline (Tier 1).
* Want to roll back a bad pipeline run during local development? →
  Restore point (Tier 2).
* Need an IRB-reviewable provenance reference? → Tracked baseline
  (Tier 1) plus the corresponding ``lineage_manifest.json`` from the
  audit envelope.

Bumping the baseline is a maintainer action with audit-trail
implications. Saving a restore point is a developer convenience.

