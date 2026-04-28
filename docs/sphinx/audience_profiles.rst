Audience Profiles
=================

Use this page to choose the shortest route through the documentation.
The docs are organized by reader intent, not by repository layout.

Documentation Model
-------------------

The Sphinx documentation follows five current technical-writing rules:

* Define the reader before writing the page.
* Put the task or decision first.
* Keep procedural pages short, concrete, and testable.
* Keep developer reference pages predictable: purpose, entry points,
  invariants, failure modes, tests.
* Prefer a smaller set of accurate pages over a large set of stale pages.

This model comes from the same public guidance used by major developer
documentation programs:

* `Diataxis <https://diataxis.fr/>`_ for separating tutorials, how-to
  guides, reference, and explanation.
* `Google Technical Writing: Audience <https://developers.google.com/tech-writing/one/audience>`_
  for matching vocabulary and detail to the reader's role and proximity
  to the subject.
* `Microsoft Learn style quick start <https://learn.microsoft.com/en-us/contribute/content/style-quick-start>`_
  for intent-first, concise, scannable technical content.
* `Google documentation best practices <https://google.github.io/styleguide/docguide/best_practices.html>`_
  for keeping documentation fresh, short, and useful.
* `MDN writing style guide <https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines/Writing_style_guide>`_
  for inclusive, accessible wording and descriptive links.
* `Sphinx documentation <https://www.sphinx-doc.org/>`_ for structured
  cross-references, generated API reference, and multiple output formats.

User Profiles
-------------

These readers use the portal to answer study questions, operate a local
study bundle, or review PHI protections. They usually do not need module
names or internal call graphs.

.. list-table::
   :header-rows: 1
   :widths: 22 28 28 22

   * - Profile
     - Primary question
     - Start here
     - Page style
   * - Clinical researcher
     - Can I ask cohort questions without waiting for a custom data cut?
     - :doc:`user_guide/overview`, then :doc:`user_guide/quickstart`
     - Plain-language task flow, expected outputs, limitations.
   * - Data manager
     - How does this reduce routine request load without exposing raw PHI?
     - :doc:`user_guide/data_pipeline`, then :doc:`user_guide/configuration`
     - Custody boundaries, file locations, audit artifacts.
   * - IRB or IEC reviewer
     - What exactly prevents the LLM, logs, and operator workflows from
       touching raw PHI?
     - :doc:`user_guide/overview`, :doc:`user_guide/faq`, then the IRB
       dossier in ``docs/irb_dossier/``
     - Evidence-first prose, named controls, explicit residual risks.
   * - Site PI or local operator
     - What can run on one laptop, what must be configured, and what must
       never leave the institution?
     - :doc:`user_guide/installation`, :doc:`user_guide/configuration`,
       :doc:`user_guide/quickstart`
     - Prerequisites, commands, success checks, operational warnings.

Developer Profiles
------------------

These readers change, review, or operate the code. They need source
entry points, invariants, test commands, and failure modes.

.. list-table::
   :header-rows: 1
   :widths: 22 28 28 22

   * - Profile
     - Primary question
     - Start here
     - Page style
   * - Pipeline developer
     - Where do extraction, PHI scrub, cleanup, publish, and lineage fit?
     - :doc:`developer_guide/architecture`,
       :doc:`developer_guide/data_extraction_datasets`,
       :doc:`developer_guide/data_extraction_pdfs`
     - Step order, source modules, inputs, outputs, tests.
   * - Agent/tool developer
     - How do I add or change a tool without crossing the agent boundary?
     - :doc:`developer_guide/agents`, :doc:`developer_guide/api_reference`
     - Tool contract, zone guards, PHI gates, k-anonymity checks.
   * - Privacy or security reviewer
     - Which controls are load-bearing and how are they verified?
     - :doc:`developer_guide/phi_architecture`,
       :doc:`developer_guide/sandbox`, :doc:`developer_guide/testing`
     - Threat model, invariants, evidence, regression tests.
   * - Maintainer or release reviewer
     - What must be checked before a branch becomes production code?
     - :doc:`developer_guide/contributing`,
       :doc:`developer_guide/operations`,
       :doc:`developer_guide/project_status`
     - Branch rules, PR gates, current state, known follow-ups.
   * - Documentation contributor
     - How should a new page be structured for each reader type?
     - :doc:`developer_guide/documentation_style`
     - Audience, outcome, headings, links, drift checks.

How To Choose A Page
--------------------

Use this decision path:

1. If the reader needs to run the portal, use the user guide.
2. If the reader needs to approve PHI handling, use the user guide first
   and then the IRB dossier.
3. If the reader needs to modify code, use the developer guide.
4. If the reader needs to verify a claim, use the page that names the
   artifact and test that prove the claim.
5. If no current page answers the task, add the smallest page that does.
