User Guide
==========

This guide is for people who need to use RePORT AI Portal, not maintain
its internals. It explains what the portal does, how it helps a study
team, how to set it up, and how to run a study safely.

For implementation details, architecture, source entry points, tests,
and contributor workflow, use the :doc:`../developer_guide/index`.

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
   * - PI or reviewer
     - Understand the privacy posture and what evidence exists.
     - :doc:`faq`

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

The technical detail stays in:

* :doc:`../developer_guide/index` - architecture, source layout, tests,
  operational runbooks, and contributor guidance.
* :doc:`../irb_auditor/index` - reviewer-facing PHI handling and
  conformance material.
