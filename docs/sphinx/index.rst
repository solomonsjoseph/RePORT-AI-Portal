RePORT AI Portal Documentation
==============================

RePORT AI Portal is a local-first assistant for one clinical research
study. It helps a study team load local source files, publish a
PHI-scrubbed study bundle, and ask grounded questions from that bundle.

This site is the project documentation. The GitHub README is a short
entry point; the Sphinx pages hold the user instructions, IRB/auditor
evidence, and developer detail.

Choose Your Path
----------------

.. list-table::
   :header-rows: 1
   :widths: 24 46 30

   * - Reader
     - Goal
     - Start here
   * - Clinical researcher
     - Understand what the portal can answer and how it helps a study
       workflow.
     - :doc:`user_guide/overview`, then :doc:`user_guide/quickstart`
   * - Data manager
     - Prepare study files, load one study, and review the published
       bundle.
     - :doc:`user_guide/data_pipeline`, then
       :doc:`user_guide/configuration`
   * - Site operator
     - Install dependencies, choose a model provider, and launch the UI.
     - :doc:`user_guide/installation`, then
       :doc:`user_guide/quickstart`
   * - IRB, IEC, or auditor
     - Review what PHI is handled, why the controls exist, and what
       evidence verifies them.
     - :doc:`irb_auditor/index`
   * - Developer or maintainer
     - Change code, review architecture, run tests, or prepare a PR.
     - :doc:`developer_guide/index`

Documentation Map
-----------------

.. list-table::
   :header-rows: 1
   :widths: 24 46 30

   * - Section
     - Contains
     - Does not contain
   * - :doc:`user_guide/index`
     - Setup, first run, configuration, study loading, normal use, FAQ,
       and glossary.
     - Code architecture or implementation history.
   * - :doc:`irb_auditor/index`
     - PHI handling, India-USA privacy alignment, conformance evidence,
       and required attestations.
     - Developer workflow or build details.
   * - :doc:`developer_guide/index`
     - Architecture, source entry points, operational contracts, tests,
       decisions, and contribution workflow.
     - Basic user onboarding.
   * - :doc:`release_notes`
     - User-readable summaries of notable changes.
     - Commit dumps or implementation history.

Contents
--------

.. toctree::
   :maxdepth: 2
   :caption: For Researchers & Data Managers

   user_guide/index

.. toctree::
   :maxdepth: 2
   :caption: For IRB & Auditors

   irb_auditor/index

.. toctree::
   :maxdepth: 2
   :caption: For Developers & Maintainers

   developer_guide/index

.. toctree::
   :maxdepth: 1
   :caption: Project Updates

   release_notes

Reference
---------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
