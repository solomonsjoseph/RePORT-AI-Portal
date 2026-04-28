Developer Guide
===============

This guide is for people who change, review, operate, or release the
code. User onboarding stays in :doc:`../user_guide/index`; IRB/auditor
evidence stays in :doc:`../irb_auditor/index`.

Start by Role
-------------

.. list-table::
   :header-rows: 1
   :widths: 24 44 32

   * - Reader
     - Goal
     - Start here
   * - Pipeline developer
     - Change extraction, PHI scrub, cleanup, publish, variables, or
       lineage behavior.
     - :doc:`architecture`, then :doc:`data_extraction_datasets`
   * - PDF pipeline developer
     - Change PDF extraction, redaction, merge, or snapshot fallback.
     - :doc:`data_extraction_pdfs`, then :doc:`phi_architecture`
   * - Agent/tool developer
     - Add or change assistant tools without breaking file-zone and PHI
       gates.
     - :doc:`agents`, then :doc:`api_reference`
   * - Privacy or security reviewer
     - Inspect load-bearing controls, invariants, and tests.
     - :doc:`phi_architecture`, :doc:`sandbox`, then :doc:`testing`
   * - Maintainer
     - Run verification, restore reviewed snapshots, and prepare
       releases.
     - :doc:`operations`, then :doc:`project_status`
   * - Documentation contributor
     - Keep README and Sphinx organized by audience.
     - :doc:`documentation_style`, then :doc:`contributing`

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

Working Rules
-------------

* Preserve the raw → staging → published bundle → agent-boundary PHI
  model described in :doc:`phi_architecture`.
* Keep implementation changes, tests, and documentation in the same PR
  when behavior changes.
* Run the smallest focused tests first, then the repo gates required by
  :doc:`testing`.
* Keep README brief. Put durable detail in Sphinx and link to it.
