Developer Guide
===============

The developer guide is for readers who change, review, or operate the
code. It documents the four-tier honest-broker architecture, the
eight-action PHI scrubber, the agent-boundary gates, and the verification
contracts that keep the system IRB-ready.

Reader Profiles
---------------

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Reader
     - Goal
     - Best first page
   * - Pipeline developer
     - Change extraction, PHI scrub, cleanup, publish, or lineage
       behavior without breaking the custody model.
     - :doc:`architecture`, then :doc:`data_extraction_datasets`
   * - Agent/tool developer
     - Add or change an AI Assistant tool while preserving file-zone,
       PHI, and k-anonymity gates.
     - :doc:`agents`, then :doc:`api_reference`
   * - Privacy or security reviewer
     - Audit the load-bearing controls and the tests that prove them.
     - :doc:`phi_architecture`, :doc:`sandbox`, then :doc:`testing`
   * - Maintainer or release reviewer
     - Review branch hygiene, verification gates, operations, and
       current production readiness.
     - :doc:`contributing`, then :doc:`project_status`
   * - Documentation contributor
     - Keep Sphinx and IRB docs accurate for both user and developer
       profiles.
     - :doc:`documentation_style`

How These Pages Are Written
---------------------------

Developer pages are contract-first. Each technical page should name the
surface it owns, source entry points, invariants, data flow, failure
modes, and tests. User-facing explanations stay in the
:doc:`../user_guide/index`.

Contents
--------

.. toctree::
   :maxdepth: 2
   :caption: Architecture & Decisions

   architecture
   phi_architecture
   decisions
   references
   tech_stack

.. toctree::
   :maxdepth: 2
   :caption: Pipeline Components

   data_extraction_datasets
   data_extraction_pdfs
   operations
   sandbox
   versioning

.. toctree::
   :maxdepth: 2
   :caption: Reference & Status

   api_reference
   project_status
   documentation_style

.. toctree::
   :maxdepth: 2
   :caption: Contributing

   contributing
   testing
   agents

Quick Links
-----------

**Getting started**

1. Read :doc:`../user_guide/overview` for the pain narrative and the
   one-paragraph explanation of what this project is.
2. Read :doc:`phi_architecture` for the four-tier honest-broker +
   eight-action catalog story — this is the load-bearing doc.
3. Read :doc:`decisions` for the "why" behind every major call —
   including the alternatives that were considered and rejected.
4. Follow :doc:`contributing` to set up your development environment,
   and :doc:`testing` to write and run tests.
5. Consult :doc:`api_reference` for module-level API details.

**Common development tasks**

- **Add a new PHI rule class**: declare it in
  ``scripts/security/phi_scrub.yaml`` under the matching section
  (drop_fields / cap_fields / etc.); add a case to the
  ``TestCatalogCoverage`` fixture in ``tests/test_phi_scrub.py``.
- **Add a new agent tool**: start with
  :func:`scripts.ai_assistant.file_access.validate_agent_read` (or
  ``validate_agent_write``) for any file I/O — the unified chokepoint
  that accepts only ``trio_bundle/`` + ``agent/`` paths. Wrap with
  ``@phi_safe_return``, and for row-level returns call
  ``guard_rows_with_kanon`` before returning. See
  :doc:`phi_architecture` → "When You Touch This Code".
- **Add a new data source**: modify
  :py:mod:`scripts.extraction.dataset_pipeline` and update the data
  dictionary loader.
- **Extend extraction I/O**: modify modules under
  :py:mod:`scripts.extraction.io`.
- **Update the variables reference**: modify
  :py:mod:`scripts.extraction.build_variables_reference`.
- **Add a new PHI regex class**: declare it in
  :py:mod:`scripts.security.phi_patterns` under ``BLOCKING_PATTERNS``
  (high confidence) or ``WARN_PATTERNS`` (low-confidence heuristic);
  the log redactor and the agent gate pick it up automatically.

Architecture principles
-----------------------

RePORT AI Portal follows these architectural principles:

**Modularity**
   Each component (data extraction, dataset promotion, AI Assistant) is a separate, testable module.

**Privacy-First**
   The runtime implements a four-tier honest-broker architecture: raw (RED) → secure staging (AMBER) → PHI-free trio bundle (GREEN) → agent boundary with PHI + k-anonymity gates (GREEN-PROTECT). The 8-action PHI scrub (:mod:`scripts.security.phi_scrub`) runs as Step 1.6 on staged datasets before any audit output is written. See :doc:`../irb_auditor/conformance` for the active IRB/Auditor conformance profile.

**Extensibility**
   LLM providers (via ``init_chat_model`` from langchain-core), agent tools
   (any new ``@tool``-decorated callable in
   :mod:`scripts.ai_assistant.agent_tools` registered in
   :data:`scripts.ai_assistant.agent_tools.ALL_TOOLS`), and PHI-rule
   overlays (the eight-action catalog in ``scripts/security/phi_scrub.yaml``)
   are designed to be extended without touching the pipeline core.

**Configuration-Driven**
   System behavior is controlled through ``config.py``, not hardcoded values.

**Documentation-First**
   Operator-facing behavior, PHI handling, and verification claims must stay synchronized with code and tests.

Development standards
---------------------

Code quality
~~~~~~~~~~~~

- **Style Guide**: Google Python Style Guide + PEP 8
- **Docstrings**: Google-style docstrings for all public APIs
- **Type Hints**: Use type annotations for function signatures
- **Testing**: Add focused pytest coverage for changed behavior
- **Documentation**: Follow Diátaxis framework (tutorials, how-to guides, reference, explanation)

Documentation standards
~~~~~~~~~~~~~~~~~~~~~~~

All documentation must follow :doc:`documentation_style`. In short:
identify the reader, state the outcome, keep current-facing pages about
current behavior, and tie PHI/IRB claims to implementation or evidence.

Code review process
~~~~~~~~~~~~~~~~~~~

All code changes require:

1. Google-style docstrings with examples
2. Focused tests for changed behavior
3. Updated documentation
4. Passing CI/CD checks
5. At least one approving review

Contributing
------------

Ready to contribute? Start with:

1. :doc:`contributing` - Set up your development environment
2. :doc:`testing` - Write and run tests
3. :doc:`architecture` - Understand the system design
4. :doc:`api_reference` - Browse the API documentation

Additional resources
--------------------

- `Google Python Style Guide <https://google.github.io/styleguide/pyguide.html>`_
- `Diátaxis Documentation Framework <https://diataxis.fr/>`_
- `Sphinx Documentation <https://www.sphinx-doc.org/>`_
- `pytest Documentation <https://docs.pytest.org/>`_

Need Help?
----------

- Check the :doc:`../user_guide/faq` for common questions
- Review :doc:`architecture` for system design questions
- Open an issue on GitHub for bugs or feature requests
- Join discussions on GitHub Discussions for general questions
