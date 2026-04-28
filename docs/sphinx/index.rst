RePORT AI Portal Documentation
==============================

**RePORT AI Portal** is a single-study, privacy-first, local-first AI Assistant system for
clinical research data. The runtime implements a four-tier honest-broker
architecture: raw study artifacts (Excel datasets, annotated PDFs, data
dictionaries) are extracted into a hardened AMBER staging zone, scrubbed
via an 8-action PHI catalog (keep / birthdate / drop / cap / generalize /
suppress_small_cell / date jitter / id pseudonymize), and atomically
published as a PHI-free Trio bundle for the ReAct agent. Every run emits ``audit/lineage_manifest.json`` — the single
evidence artifact pairing every raw input SHA-256 with every published
trio artifact SHA-256. See ``docs/irb_dossier/conformance_matrix.md`` for
the active IRB conformance matrix.

Choose Your Path
----------------

Start with the profile that matches the task:

* **Clinical researcher** — use :doc:`user_guide/overview` to understand
  the problem solved, then :doc:`user_guide/quickstart` to run the portal.
* **Data manager or site operator** — use
  :doc:`user_guide/data_pipeline` and :doc:`user_guide/configuration` to
  understand custody boundaries, inputs, outputs, and runtime flags.
* **IRB or IEC reviewer** — use :doc:`user_guide/faq` and the IRB
  dossier in ``docs/irb_dossier/`` for control evidence and residual
  risks.
* **Developer or maintainer** — use :doc:`developer_guide/index` for
  architecture, source entry points, invariants, and PR gates.
* **Documentation contributor** — use :doc:`audience_profiles` and
  :doc:`developer_guide/documentation_style` before changing public docs.

For a profile-by-profile map, see :doc:`audience_profiles`.

Contents
--------

.. toctree::
   :maxdepth: 1
   :caption: Start Here

   audience_profiles

.. toctree::
   :maxdepth: 2
   :caption: For Researchers & Data Managers

   user_guide/index

.. toctree::
   :maxdepth: 2
   :caption: For Developers & Maintainers

   developer_guide/index

Reference
---------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
