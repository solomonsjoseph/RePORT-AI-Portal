User Guide
==========

Welcome to the RePORT AI Portal user guide. This side of the docs is
written for **researchers, data managers, and IRB reviewers** — the
humans who want answers from the study data, not the humans who want to
modify the pipeline itself. Contributors looking to extend the runtime
belong in the :doc:`../developer_guide/index`.

.. note::

   **Where to start.** If you want to know *why this exists and whether
   it solves your pain*, start with :doc:`overview`. If you want to
   **run it once** and see the audit artifacts drop out, jump straight
   to :doc:`quickstart`. If you've already done both and want to know
   the full eight-step pipeline, go to :doc:`data_pipeline`.

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
