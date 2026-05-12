Catalog Cutover and Legacy-Path Rollback
========================================

This page documents the hard cutover performed in issue #81 (parent
PRD #65) and the operator escape hatch for the one release window that
follows.

Cutover Summary
---------------

After issue #81 the assistant's standard runtime path is:

* **Metadata answers** flow through the Study Metadata Catalog
  (``scripts.source_truth.catalog`` / ``scripts.source_truth.retrieval``)
  rather than the old flat ``variables.json`` reference.
* **Analysis bindings** flow through the catalog plus the Dataset
  Schema (``scripts.source_truth.analysis_binding``) rather than the
  old manually curated ``study_knowledge.yaml`` path.
* The verbatim ``AUDIT_ONLY_NOTE`` text continues to be the only chat-
  surface response for audit-only / pseudonymized records.

The previously opt-in environment toggles
(``REPORTALIN_USE_CATALOG_RUNTIME`` and
``REPORTALIN_USE_CATALOG_BINDING``) are now redundant. They are still
accepted for backward compatibility, but the catalog runtime is the
default with no env var set.

Legacy Override -- One Release Window Only
------------------------------------------

If the catalog runtime causes a production regression, the legacy
``StudyKnowledge``-driven path can be re-enabled by setting one
environment variable before starting the chat / agent process:

.. code-block:: bash

   export REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE=1

Behaviour under this override:

* :func:`scripts.ai_assistant.agent_graph.is_catalog_runtime_enabled`
  returns ``False``.
* :func:`scripts.ai_assistant.analytical_engine.is_catalog_binding_enabled`
  returns ``False``.
* The agent uses the legacy ``SYSTEM_PROMPT`` and the legacy lookup
  tools.
* :func:`scripts.ai_assistant.analytical_engine.run_full_analysis`
  accepts a ``StudyKnowledge`` instance again instead of raising.

The override beats an explicitly set
``REPORTALIN_USE_CATALOG_RUNTIME=1`` -- it is a hard kill switch, so
operators only need to set one variable to roll back.

The override is intended to be available for **one release window**
after the cutover ships. After that release window closes the
``study_knowledge.py`` module and its YAML fixture will be retired in
a follow-up slice.

What Stays Reachable
--------------------

* ``scripts/ai_assistant/study_knowledge.py`` and its tests remain on
  disk and importable. The override re-enables the runtime path; the
  module is also still callable directly from migration / replay
  scripts that explicitly opt in.
* Storage paths under ``output/{study}/llm_source/`` are filesystem
  layout, not user-facing language; they are not renamed by the
  cutover. References in tool docstrings to that directory are
  rephrased as "catalog and current dataset" so the LLM no longer
  parrots filesystem-internal terms back to researchers.

What Changed Permanently (No Rollback Path)
-------------------------------------------

* The ``CATALOG_RUNTIME_SYSTEM_PROMPT`` is the new default agent
  system prompt. Tool description docstrings have been reworded to
  drop user-facing "trio bundle" terminology in favour of "catalog
  and current dataset". These changes do not depend on a flag and
  are not undone by the legacy override (the override still selects
  the legacy ``SYSTEM_PROMPT``, but the tool descriptions remain in
  their cutover wording).

Verification After Setting the Override
---------------------------------------

To confirm the override is in effect:

.. code-block:: bash

   uv run --all-groups python -c "from scripts.ai_assistant.agent_graph import is_catalog_runtime_enabled; print(is_catalog_runtime_enabled())"

The expected output with ``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE=1`` set
is ``False``; with the variable unset it is ``True``.

To confirm the catalog binding follows the override:

.. code-block:: bash

   uv run --all-groups python -c "from scripts.ai_assistant.analytical_engine import is_catalog_binding_enabled; print(is_catalog_binding_enabled())"

References
----------

* PRD #65 -- Source Truth architecture rollout.
* Issue #75 -- Analysis binding cutover (catalog plus Dataset Schema).
* Issue #79 -- Catalog runtime feature flag.
* Issue #80 -- Hard cutover validation gate.
* Issue #81 -- Hard cutover default flip (this page).
