User Guide
==========

This guide is for people who need to use RePORT AI Portal. It explains
what the portal does, how it helps a study team, how to set it up, and
how to run one study safely. It does not explain how the code is built.

Start Here
----------

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Reader
     - Goal
     - Start with
   * - Clinical researcher
     - Ask questions about a locked, PHI-scrubbed study.
     - :doc:`overview`
   * - Data manager
     - Prepare raw study files and publish a safe bundle.
     - :doc:`quickstart`
   * - Site operator
     - Install, configure, and launch the portal locally.
     - :doc:`installation`
   * - PI
     - Understand the user workflow and where output evidence lives.
     - :doc:`overview`, then :doc:`data_pipeline`

What's Included
---------------

* :doc:`overview` - what the portal is, who it helps, and when to use it.
* :doc:`installation` - system requirements and setup.
* :doc:`quickstart` - first run from raw study files to chat.
* :doc:`configuration` - the small set of settings most users touch.
* :doc:`data_pipeline` - a plain-language view of what happens when you
  load a study.
* :doc:`glossary` - user-facing terms.
* :doc:`faq` - common setup, privacy, and troubleshooting questions.

Contents
--------

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   overview
   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: Using the Portal

   configuration
   data_pipeline
   glossary
   faq

Where Details Live
------------------

User pages stay brief on purpose. They should help a study team operate
the portal without needing to understand how the code is built.

Other audience-specific detail stays in:

* :doc:`../developer_guide/index` - architecture, source layout, tests,
  operational runbooks, and contributor guidance.
* :doc:`../irb_auditor/index` - reviewer-facing PHI handling and
  conformance material.
