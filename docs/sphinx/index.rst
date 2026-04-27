.. RePORT AI Portal documentation root document

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
the 31-criterion IRB benchmark (plus four follow-ups added in patches
2026-04-23a/b, totalling 35 architecturally satisfied).

This root page is the entry point to two audiences:

* **Researchers / data managers / IRB reviewers** — start with the
  :doc:`user_guide/index`. The user guide opens with the pain this
  project solves (months-long data-manager queues), walks through a
  10-minute quickstart, and answers trust / PHI-scope / leak-response
  questions.
* **Developers / contributors / security engineers** — start with the
  :doc:`developer_guide/index`. The developer guide contains the
  canonical PHI architecture page, the architectural decision records,
  the regulatory-reference collection, the tech-stack rationale, and
  the API reference.

Contents
--------

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
