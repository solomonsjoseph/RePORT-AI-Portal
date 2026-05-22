Project Status
==============

This page is the current implementation snapshot for maintainers. It is
not a changelog; historical PR numbers and patch labels belong in GitHub,
not in the operational docs.

Implemented
-----------

Study Preparation
~~~~~~~~~~~~~~~~~

* Portable study-preparation plugin under
  ``plugins/report-ai-study-pipeline/``.
* Plugin phase order: duplicate handling, Source Truth, then
  dataset-to-LLM-source publishing.
* Data dictionary extraction remains in ``main.py`` /
  ``scripts.extraction.load_dictionary``.
* Host publish path still provides parallel extraction legs for dictionary
  and datasets when invoked by the dataset child skill.
* Supported tabular inputs are ``.xlsx`` and ``.csv`` only.
* AMBER staging under ``tmp/{STUDY}/`` with mode ``0700`` and secure
  deletion on successful completion.
* Step 1.6 PHI scrub over staged datasets before publish.
* Dataset cleanup and cleanup propagation into dictionary metadata.
* Atomic publish into ``output/{STUDY}/llm_source/``.
* Verified SoT policy/schema/joined sets published under
  ``output/{STUDY}/llm_source/SoT/<pair>/``.
* Counts-only audit reports and lineage manifest under
  ``output/{STUDY}/audit/``.

PHI And Security Boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* RED raw data is limited to extraction code.
* AMBER staging is never agent-readable.
* GREEN consists of ``output/{STUDY}/llm_source/`` plus
  ``output/{STUDY}/agent/``.
* GREEN-PROTECT is the agent tool boundary: PHI regex gate,
  k-anonymity, and l-diversity before row-level answers are surfaced.
* ``output/{STUDY}/audit/`` is a counts-only audit envelope and is
  rejected by the agent read validator.
* API keys route through the in-memory KeyStore in the Streamlit flow.
* ``run_python_analysis`` executes generated code in a constrained
  subprocess and persists reproducibility artifacts under
  ``output/{STUDY}/agent/analysis/``.

AI Assistant
~~~~~~~~~~~~

* LangChain/LangGraph ReAct agent constructed through
  ``scripts/ai_assistant/agent_graph.py``.
* Ten structured tools registered in
  ``scripts/ai_assistant/agent_tools.ALL_TOOLS``.
* CLI and Streamlit interfaces.
* Grounded-answer prompt contract: resolve variables before analysis,
  use deterministic tools for statistical claims, separate computed
  facts from interpretation, and surface caveats plainly.
* Provider support through OpenAI, Anthropic, Google Gemini, Ollama,
  and NVIDIA AI Endpoints.
* Ollama qwen3 downgrade ladder for local memory pressure.

PDF / Source Truth
~~~~~~~~~~~~~~~~~~

* PDF-derived clinical meaning is captured through the
  ``sot-lean-generator`` plugin skill.
* Source Truth generation may use printed PDFs/page renders and dataset
  row-1 headers only. Dataset row 2+ values are not read into the agent
  context.
* Historical raw-PDF extraction modules are not the active runtime surface.

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

The active IRB/Auditor conformance profile lives in
:doc:`../irb_auditor/conformance`. It maps each claim to:

* the applicable authority,
* the disk artifact an auditor can inspect,
* and the pytest assertion that fails if the claim regresses.

The reviewer-facing PHI handling narrative lives in
:doc:`../irb_auditor/phi_handling`.

Known Follow-Ups
----------------

These are documented gaps or operator-owned extensions; none require the
agent to read raw PHI.

* Study-team breach-response runbook.
* Study-team data-retention and destruction runbook.
* Optional district-population mapping table when a site needs
  population-threshold geography generalization beyond the current drop
  catalog.
* Optional ``config/consent_scope.yaml`` for an IEC-approved field
  allowlist layered above the scrub catalog.
