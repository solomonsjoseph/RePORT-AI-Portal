Quick Start — Ten Minutes to Your First Answer
==============================================

**What.** A step-by-step walkthrough that takes you from a cloned repo to
an answered epidemiological question in about ten minutes. No prior
familiarity with the pipeline is assumed.

**Why.** The best way to trust this stack is to run it once and see the
audit artifacts drop out. Everything below is reproducible and shows
expected output where it is stable enough to quote.

**How.** Five steps: install ``uv``, sync deps, place data + configure
the PHI key, run the pipeline, open the chat. Each step lists the command
and what "success" looks like.

.. contents:: On this page
   :local:
   :depth: 2

Prerequisites
-------------

* macOS, Linux, or Windows (WSL) with **Python 3.11 or newer**.
* ~2 GB free disk for the virtualenv + dependencies.
* Study data ready to place under ``data/raw/{STUDY_NAME}/`` — the repo
  does not ship with raw data.
* An LLM endpoint. Any of: Ollama running locally (recommended for PHI
  separation), an Anthropic API key, an OpenAI API key, or a Google
  Generative AI key.

Step 1 — Install ``uv`` and clone the repo
------------------------------------------

.. code-block:: bash

   curl -LsSf https://astral.sh/uv/install.sh | sh
   git clone https://github.com/solomonsjoseph/RePORTaLiN-RAG.git
   cd RePORTaLiN-RAG
   uv sync --all-groups

Expected: ``uv sync`` prints a handful of package resolutions and
exits 0. You now have a ``.venv/`` beside the source tree.

Step 2 — Place study data
-------------------------

Copy the raw study tree under ``data/raw/{STUDY_NAME}/`` so that:

.. code-block:: text

   data/raw/Indo-VAP/
   ├── datasets/              # your .xlsx / .csv study forms
   ├── data_dictionary/       # the study data-dictionary workbook
   └── annotated_pdfs/        # optional — annotated CRF templates

Set ``STUDY_NAME`` if it differs from the auto-detected directory:

.. code-block:: bash

   export STUDY_NAME=Indo-VAP

Step 3 — Bootstrap the PHI HMAC key
-----------------------------------

The scrubber uses an HMAC-SHA256 key (32 random bytes) to produce stable
subject pseudonyms and per-subject date offsets. The key lives **outside
the repo tree** at ``~/.config/report_ai_portal/phi_key`` (mode 0600).

Create it once:

.. code-block:: bash

   python -m scripts.security.phi_scrub bootstrap-key

Expected:

.. code-block:: text

   PHI HMAC key written to: /Users/you/.config/report_ai_portal/phi_key
   File mode: 0600. This key is outside the repo tree and agent scope.

If the key already exists the command refuses to overwrite — rotating it
invalidates every previously-scrubbed artifact.

Step 4 — Pick an LLM and run the pipeline
-----------------------------------------

Set the provider that matches the LLM endpoint you plan to use. For local
Ollama (the safest PHI posture — nothing leaves the box):

.. code-block:: bash

   export LLM_PROVIDER=ollama
   export LLM_MODEL=qwen3:8b
   # Ollama must be running at http://localhost:11434

If your machine is tight on RAM, the agent will automatically step the
``qwen3:8b → qwen3:4b → qwen3:1.7b`` ladder at startup and pick the
largest rung Ollama can actually load. Pull all three to give the walker
room to move: ``ollama pull qwen3:8b qwen3:4b qwen3:1.7b``. The resolved
rung is reflected in the wizard and any error cards.

For a hosted API (requires the PHI-safety flag acknowledging the PDF
source is PHI-free — see :doc:`configuration`):

.. code-block:: bash

   export LLM_PROVIDER=anthropic
   export ANTHROPIC_API_KEY=sk-ant-...
   export LLM_MODEL=claude-sonnet-4-20250514
   export REPORTALIN_PDF_PHI_FREE=1   # only if your PDFs are verified PHI-free

Run the pipeline:

.. code-block:: bash

   make pipeline

Expected: progress bars per leg (dictionary → datasets → PDFs), a "Step 1.6:
PHI Scrub" line with a count of rows kept + fields scrubbed, "Step 1.7:
Dataset Cleanup", "Step 1.8: Cleanup Propagation", "Step 2: Publish
Staging → Trio Bundle", "Step 4: Emit Lineage Manifest". Success ends with
an output-locations summary listing ``output/{STUDY}/trio_bundle/`` and
``output/{STUDY}/audit/``.

Step 5 — Verify and open the chat
---------------------------------

Peek at the scrub audit:

.. code-block:: bash

   jq '.scrubbed | group_by(.scope) | map({scope: .[0].scope, total: (map(.count) | add)})' \
       output/Indo-VAP/audit/phi_scrub_report.json

Expected output is a small JSON array of ``{scope, total}`` pairs — counts
only, no raw values. Nonzero numbers under ``phi-scrub-drop``,
``phi-scrub-id``, ``phi-scrub-date``, and (if applicable) ``phi-scrub-cap``
confirm the scrubber did work.

Spot-check the lineage manifest:

.. code-block:: bash

   jq '.steps | keys' output/Indo-VAP/audit/lineage_manifest.json

Expected: ``["phi_scrub", "dataset_cleanup", "dictionary_cleanup", "pdfs_cleanup"]``
or a subset depending on which legs ran.

Launch the chat UI:

.. code-block:: bash

   make chat

A Streamlit window opens at http://localhost:8501. Ask a test question:

.. code-block:: text

   How many Cohort A subjects enrolled between Jan 2014 and Dec 2014
   had a TB recurrence outcome?

The agent routes to structured tools, runs the query against the
trio_bundle, and answers with a count (plus a link back to the source
dataset).

What If Something Breaks?
-------------------------

* **"PHI HMAC key not found"** — run Step 3.
* **"PDF extraction via external LLM API refused"** — you set an external
  API provider but did not set ``REPORTALIN_PDF_PHI_FREE=1``. Either
  flip the flag (if your PDFs really are PHI-free) or use
  ``--pdf-source`` with pre-extracted JSON — see :doc:`configuration`.
* **"tests not found" / missing deps** — ``uv sync --all-groups`` (not
  just ``uv sync``) pulls the test group and the AI Assistant group.
* **The scrub report is empty** — ``scripts/security/phi_scrub.yaml`` is
  the catalog; if it's missing the scrubber no-ops and writes an audit
  with posture ``disabled``. Restore from the repo.

Optional — Save a Restore Snapshot Locally
------------------------------------------

Once you have a clean trio bundle, save a **restore-ready snapshot** so
you can roll back to a known-good cohort after subsequent pipeline
runs::

    make snapshot SNAPSHOT=indovap-2026q1      # default name is UTC timestamp
    make list-snapshots                         # newest first
    make restore-study SNAPSHOT=indovap-2026q1  # overwrites live trio_bundle/

Restore points live under ``output/{STUDY_NAME}/agent/restore_points/<name>/``
and are fully gitignored along with the rest of ``output/``. They are
byte-for-byte copies of the PHI-scrubbed trio bundle and contain no
audit logs, telemetry, or conversations. They support crash-recovery
during dev (rolling back ``trio_bundle/`` to a prior cohort) and are
distinct from the version-controlled tracked baseline at
``snapshots/{STUDY_NAME}/`` (maintainer-curated, used by the pipeline's
PDF orchestrator as a fallback). See ``snapshots/README.md``.

The wizard's step-2 "Use Existing Study" button skips the pipeline and
trusts the live ``trio_bundle/``; "Load Study" runs the pipeline (with
the tracked snapshot baseline as PDF fallback when the LLM tier is
unavailable).

Next Steps
----------

* :doc:`data_pipeline` — the full eight-step flow in depth.
* :doc:`configuration` — every knob including the three PHI-safety flags.
* :doc:`faq` — trust, PHI scope, and leak-response questions.
* :doc:`glossary` — AMBER / GREEN / trio bundle / SANT jitter / k-anonymity.
