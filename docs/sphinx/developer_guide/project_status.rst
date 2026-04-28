Project Status
==============

This page is the current implementation snapshot for maintainers. It is
not a changelog; historical PR numbers and patch labels belong in GitHub,
not in the operational docs.

Implemented
-----------

Pipeline
~~~~~~~~

* Single-study pipeline entry point in ``main.py``.
* Parallel extraction legs for dictionary, datasets, and PDFs.
* Supported tabular inputs are ``.xlsx`` and ``.csv`` only.
* AMBER staging under ``tmp/{STUDY}/`` with mode ``0700`` and secure
  deletion on successful completion.
* Step 1.6 PHI scrub over staged datasets before publish.
* Dataset cleanup and cleanup propagation into dictionary and PDF
  metadata.
* Atomic publish into ``output/{STUDY}/trio_bundle/``.
* ``variables.json`` build from the published trio bundle.
* Counts-only audit reports and lineage manifest under
  ``output/{STUDY}/audit/``.

PHI And Security Boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* RED raw data is limited to extraction code.
* AMBER staging is never agent-readable.
* GREEN consists of ``output/{STUDY}/trio_bundle/`` plus
  ``output/{STUDY}/agent/``.
* GREEN-PROTECT is the agent tool boundary: PHI regex gate,
  k-anonymity, and l-diversity before row-level answers are surfaced.
* ``output/{STUDY}/audit/`` is a counts-only audit envelope and is
  rejected by the agent read validator.
* ``snapshots/{STUDY}/`` is a version-controlled baseline used by the
  PDF orchestrator fallback; it is outside the agent read surface.
* API keys route through the in-memory KeyStore in the Streamlit flow.
* ``run_python_analysis`` executes generated code in a constrained
  subprocess and persists reproducibility artifacts under
  ``output/{STUDY}/agent/analysis/``.

AI Assistant
~~~~~~~~~~~~

* LangChain/LangGraph ReAct agent constructed through
  ``scripts/ai_assistant/agent_graph.py``.
* Twelve structured tools registered in
  ``scripts/ai_assistant/agent_tools.ALL_TOOLS``.
* CLI and Streamlit interfaces.
* Grounded-answer prompt contract: resolve variables before analysis,
  use deterministic tools for statistical claims, separate computed
  facts from interpretation, and surface caveats plainly.
* Provider support through OpenAI, Anthropic, Google Gemini, Ollama,
  and NVIDIA AI Endpoints.
* Ollama qwen3 downgrade ladder for local memory pressure.

PDF Extraction
~~~~~~~~~~~~~~

* Default wizard path uses the two-way PDF orchestrator:
  ``pdfplumber`` text extraction, PHI redaction before any LLM call,
  re-scrubbed LLM response, merge with code candidate, and per-PDF
  fallback to ``snapshots/{STUDY}/pdfs/``.
* Legacy raw-PDF API path remains available for CLI compatibility, but
  is refused unless ``REPORTALIN_PDF_PHI_FREE=1`` and a non-empty
  ``authorities/phi_free_pdfs.md`` attestation are both present.

Verification
------------

Use the command output for the commit under review as the source of
truth. Current gates:

.. code-block:: bash

   make verify
   make test-all
   make docs-quality
   make security

The CI workflow runs Ruff, mypy, and the full pytest suite on Python
3.11, 3.12, and 3.13. The docs-quality workflow runs doc-freshness,
Sphinx build, linkcheck, and documentation metrics.

IRB Conformance
---------------

The active IRB conformance matrix lives in
``docs/irb_dossier/conformance_matrix.md``. It maps each claim to:

* the applicable authority,
* the disk artifact an auditor can inspect,
* and the pytest assertion that fails if the claim regresses.

The line-by-line PHI handling narrative lives in
``docs/irb_dossier/phi_walkthrough.md``. The plain-language IEC/IRB
summary lives in ``docs/irb_dossier/executive_summary.md``.

Known Follow-Ups
----------------

These are documented gaps or operator-owned extensions; none require the
agent to read raw PHI.

* OS-level run lock around the staging root to prevent two simultaneous
  operator-triggered pipeline runs for the same study.
* Study-team breach-response runbook.
* Study-team data-retention and destruction runbook.
* Optional district-population mapping table when a site needs
  population-threshold geography generalization beyond the current drop
  catalog.
* Optional ``config/consent_scope.yaml`` for an IEC-approved field
  allowlist layered above the scrub catalog.
* Local narrative NER sweep in ``scripts/security/phi_ner.py`` once a
  model and prompt are calibrated against the study corpus.
