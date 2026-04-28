Audience Profiles
=================

Use this page to choose where to start.

Users
-----

User pages are brief. They explain how the portal helps, how to set it
up, how to load a study, and how to ask questions. They do not explain
how the project is built.

.. list-table::
   :header-rows: 1
   :widths: 24 44 32

   * - Profile
     - Need
     - Start here
   * - Clinical researcher
     - Understand what the portal can answer and how it helps research
       workflows.
     - :doc:`user_guide/overview`, then :doc:`user_guide/quickstart`
   * - Data manager
     - Prepare study files, load the study, and review output folders.
     - :doc:`user_guide/data_pipeline`, then :doc:`user_guide/configuration`
   * - Site operator
     - Install the portal, configure the model provider, and launch the
       web UI.
     - :doc:`user_guide/installation`, then :doc:`user_guide/quickstart`
   * - PI or reviewer
     - Understand the privacy posture and know where evidence lives.
     - :doc:`irb_auditor/index`

Developers
----------

Developer pages hold the details: architecture, source files, test
contracts, operational runbooks, and implementation decisions.

.. list-table::
   :header-rows: 1
   :widths: 24 44 32

   * - Profile
     - Need
     - Start here
   * - Pipeline developer
     - Change extraction, scrub, publish, or lineage behavior.
     - :doc:`developer_guide/architecture`
   * - Agent developer
     - Change assistant tools or model wiring.
     - :doc:`developer_guide/agents`
   * - Privacy or security reviewer
     - Inspect controls, invariants, tests, and threat boundaries.
     - :doc:`developer_guide/phi_architecture`
   * - Maintainer
     - Run releases, restore bundles, and review project status.
     - :doc:`developer_guide/operations`
   * - Documentation contributor
     - Add or edit pages without mixing user and developer detail.
     - :doc:`developer_guide/documentation_style`

Rule of Thumb
-------------

If the reader is trying to **use** the portal, keep them in the user
guide. If the reader is trying to **audit PHI handling**, send them to
the IRB/Auditor profile. If the reader is trying to **change or
maintain** the code, send them to the developer guide.
