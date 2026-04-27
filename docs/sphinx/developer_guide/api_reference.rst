API Reference
=============

This page documents the active public module surface for the current
RePORT AI Portal runtime using Sphinx ``automodule`` directives.

The options used below are part of Sphinx autodoc's documented module
introspection surface:

- ``:members:`` includes documented members.
- ``:undoc-members:`` also includes members without docstrings.
- ``:show-inheritance:`` shows inheritance details where applicable.

Core Modules
------------

Configuration
~~~~~~~~~~~~~

.. automodule:: config
   :members:
   :undoc-members:
   :show-inheritance:

Main Pipeline
~~~~~~~~~~~~~

.. automodule:: main
   :members:
   :undoc-members:
   :show-inheritance:

Version
~~~~~~~

.. automodule:: __version__
   :members:
   :undoc-members:
   :show-inheritance:

Extraction Modules
------------------

Dictionary Loading
~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.load_dictionary
   :members:
   :undoc-members:
   :show-inheritance:

Dataset Extraction
~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.dataset_pipeline
   :members:
   :undoc-members:
   :show-inheritance:

PDF Extraction
~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.extract_pdf_data
   :members:
   :undoc-members:
   :show-inheritance:

PDF Orchestrator (two-way pipeline — PR #15)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.pdf_pipeline
   :members:
   :undoc-members:
   :show-inheritance:

LLM Capability Gate (PR #15)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.llm_capabilities
   :members:
   :undoc-members:
   :show-inheritance:

Security Modules
----------------

The ``scripts.security`` package groups every module that participates in
PHI handling — the four-tier architecture boundaries, the 8-action
offline scrubber, the agent-boundary gates, the shared regex catalog,
the clinical-phrase allowlist, and the Stage-5 NER design stub. See
:doc:`phi_architecture` for the narrative.

Secure Environment (Zone Guard)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.secure_env
   :members:
   :undoc-members:
   :show-inheritance:

PHI Scrubber (Step 1.6)
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.phi_scrub
   :members:
   :undoc-members:
   :show-inheritance:

Shared PHI Regex Catalog
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.phi_patterns
   :members:
   :undoc-members:
   :show-inheritance:

Clinical-Phrase Allowlist
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.phi_allowlist
   :members:
   :undoc-members:
   :show-inheritance:

PHI Gate (Agent-Boundary)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.phi_gate
   :members:
   :undoc-members:
   :show-inheritance:

k-Anonymity Gate
~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.kanon_gate
   :members:
   :undoc-members:
   :show-inheritance:

Narrative NER Stub (future work)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.security.phi_ner
   :members:
   :undoc-members:
   :show-inheritance:

Utility Modules
---------------

Logging System
~~~~~~~~~~~~~~

.. automodule:: scripts.utils.logging_system
   :members:
   :undoc-members:
   :show-inheritance:

Phase-0 Secure Staging
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.secure_staging
   :members:
   :undoc-members:
   :show-inheritance:

Integrity Helpers (SHA-256)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.integrity
   :members:
   :undoc-members:
   :show-inheritance:

Lineage Manifest
~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.lineage
   :members:
   :undoc-members:
   :show-inheritance:

Log Hygiene (PHI Redactor)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.log_hygiene
   :members:
   :undoc-members:
   :show-inheritance:

Extraction Modules (continued)
------------------------------

Deduplication
~~~~~~~~~~~~~

.. automodule:: scripts.extraction.dedup
   :members:
   :undoc-members:
   :show-inheritance:

Dataset Cleanup
~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.dataset_cleanup
   :members:
   :undoc-members:
   :show-inheritance:

AI Assistant Modules
--------------------

KeyStore (in-memory API-key registry — PR #3)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.keystore
   :members:
   :undoc-members:
   :show-inheritance:

Agent Graph
~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.agent_graph
   :members:
   :undoc-members:
   :show-inheritance:

Agent Prompts
~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.agent_prompts
   :members:
   :undoc-members:
   :show-inheritance:

File-Access Validator
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.file_access
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: ZoneViolationError

Agent Tools
~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.agent_tools
   :members:
   :undoc-members:
   :show-inheritance:

Tool Cache
~~~~~~~~~~

.. automodule:: scripts.ai_assistant.tool_cache
   :members:
   :undoc-members:
   :show-inheritance:

Agent-Boundary PHI Safety
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.phi_safe
   :members:
   :undoc-members:
   :show-inheritance:

Web UI
~~~~~~

.. automodule:: scripts.ai_assistant.web_ui
   :members:
   :undoc-members:
   :show-inheritance:

CLI
~~~

.. automodule:: scripts.ai_assistant.cli
   :members:
   :undoc-members:
   :show-inheritance:

Telemetry
---------

.. automodule:: scripts.utils.telemetry
   :members:
   :undoc-members:
   :show-inheritance:

Analytical Engine
~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.analytical_engine
   :members:
   :undoc-members:
   :show-inheritance:

Study Knowledge
~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.study_knowledge
   :members:
   :undoc-members:
   :show-inheritance:

Web UI Modules
~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.ui.chat
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.conversations
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.model_policy
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.providers
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.shell
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.state
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.streaming
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: scripts.ai_assistant.ui.wizard
   :members:
   :undoc-members:
   :show-inheritance:

Extraction Modules (continued)
------------------------------

Build Variables Reference
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.build_variables_reference
   :members:
   :undoc-members:
   :show-inheritance:

Cleanup Propagation
~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.cleanup_propagation
   :members:
   :undoc-members:
   :show-inheritance:

Utility Modules (continued)
----------------------------

Errors
~~~~~~

.. automodule:: scripts.utils.errors
   :members:
   :show-inheritance:

Snapshot Manager
~~~~~~~~~~~~~~~~

.. automodule:: scripts.utils.snapshots
   :members:
   :undoc-members:
   :show-inheritance:

Step Cache
~~~~~~~~~~

.. automodule:: scripts.utils.step_cache
   :members:
   :undoc-members:
   :show-inheritance:

Artifact Version Registry
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.artifact_versions
   :members:
   :undoc-members:
   :show-inheritance:

Sandbox Subprocess (PR #2 — OS-isolated ``run_python_analysis``)
----------------------------------------------------------------

The sandbox subpackage executes LLM-generated code in a fresh
subprocess with OS-level rlimits and an in-child AST guard. See
:doc:`sandbox` for the conceptual overview.

Sandbox Public API
~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.sandbox.replicate
   :members:
   :undoc-members:
   :show-inheritance:

Sandbox Resource Limits
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.sandbox.limits
   :members:
   :undoc-members:
   :show-inheritance:

Sandbox Child Runner
~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.ai_assistant.sandbox.runner
   :members:
   :undoc-members:
   :show-inheritance:

Extraction I/O Helpers
----------------------

Clinical Date Parsing
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.io.clinical_dates
   :members:
   :undoc-members:
   :show-inheritance:

File Discovery
~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.io.file_discovery
   :members:
   :undoc-members:
   :show-inheritance:

File I/O Primitives
~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.extraction.io.file_io
   :members:
   :undoc-members:
   :show-inheritance:

JSONL Reader
~~~~~~~~~~~~

.. automodule:: scripts.extraction.io.jsonl_reader
   :members:
   :undoc-members:
   :show-inheritance:

Doc-Freshness Linter
~~~~~~~~~~~~~~~~~~~~

.. automodule:: scripts.lint_doc_freshness
   :members:
   :undoc-members:
   :show-inheritance:


Indices and Tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
