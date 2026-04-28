User Guide
==========

The user guide is for readers who need to run the portal, ask questions
of a locked study, or review the PHI protections without changing the
code. Contributors who need source entry points belong in the
:doc:`../developer_guide/index`.

Reader Profiles
---------------

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Reader
     - Goal
     - Best first page
   * - Clinical researcher
     - Ask cohort questions in English against a PHI-free published
       study bundle.
     - :doc:`overview`, then :doc:`quickstart`
   * - Data manager
     - Understand how raw data stays behind the RED/AMBER boundary and
       how published artifacts are produced.
     - :doc:`data_pipeline`, then :doc:`configuration`
   * - IRB or IEC reviewer
     - Verify PHI handling, audit evidence, and residual risks.
     - :doc:`faq`, then ``docs/irb_dossier/``
   * - Site PI or local operator
     - Install the stack locally, configure the model and PHI flags, and
       run a locked study.
     - :doc:`installation`, then :doc:`quickstart`

How These Pages Are Written
---------------------------

User pages are task-first. Each procedural page names the reader, lists
prerequisites, gives concrete commands, shows expected outputs when
stable, and links to the next operational step. PHI-sensitive pages name
the file path, audit artifact, or control that proves the claim.

What's in the User Guide
-------------------------

* :doc:`overview` — the pain this project addresses, what it is, who
  benefits, and when not to use it.
* :doc:`installation` — system requirements and one-shot install.
* :doc:`quickstart` — ten-minute walkthrough from clone to first answer,
  with expected output at every step.
* :doc:`configuration` — every runtime knob including the three
  PHI-safety environment flags.
* :doc:`data_pipeline` — the full eight-step extract → scrub → publish
  flow with the honest-broker 4-tier architecture.
* :doc:`glossary` — authoritative definitions for AMBER / GREEN /
  trio bundle / SANT jitter / k-anonymity / Safe Harbor / Limited
  Dataset / etc.
* :doc:`faq` — trust, PHI scope, leak-response, and operational questions.

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
   :caption: User Documentation

   configuration
   data_pipeline
   glossary
   faq

Suggested Reading Order
-----------------------

1. :doc:`overview` — decide whether this project solves your pain.
2. :doc:`installation` — get ``uv`` and the Python 3.11+ baseline in place.
3. :doc:`quickstart` — run the pipeline once, ten minutes.
4. :doc:`faq` — browse trust + PHI-scope questions before inviting
   collaborators onto the stack.
5. :doc:`data_pipeline` + :doc:`configuration` — go deep when you're
   ready to customise.

Getting Help
------------

* Check the :doc:`faq` for common questions and the PHI-scope /
  leak-response playbook.
* Read the :doc:`glossary` if an unfamiliar term blocks comprehension.
* Open an issue on GitHub with the relevant audit-report excerpt (never
  paste raw subject data).
* For technical deep dives — the PHI architecture, decisions, references —
  cross into the :doc:`../developer_guide/index`.
