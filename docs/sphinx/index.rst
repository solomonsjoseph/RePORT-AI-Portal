RePORT AI Portal Documentation
==============================

**RePORT AI Portal** is a local-first assistant for one clinical
research study. It helps a study team load local source files, publish a
PHI-scrubbed study bundle, and ask grounded questions through a chat
interface.

User pages explain what the portal does, how it helps, how to set it up,
and how to run a study. Developer pages hold the architecture, source
entry points, test contracts, and implementation details.

Choose Your Path
----------------

Start with the profile that matches the task:

* **Clinical researcher** — use :doc:`user_guide/overview` to understand
  how the portal helps, then :doc:`user_guide/quickstart`.
* **Data manager or site operator** — use :doc:`user_guide/installation`,
  :doc:`user_guide/configuration`, and :doc:`user_guide/data_pipeline`.
* **IRB or IEC reviewer** — use :doc:`user_guide/faq` first, then the
  IRB dossier in ``docs/irb_dossier/`` for detailed evidence.
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
