Glossary
========

Short definitions for terms users will see in the portal and docs.

.. glossary::

   audit files
     Counts and lineage files under ``output/{STUDY}/audit/``. They help
     the study team review what the pipeline processed and published.

   data dictionary
     A study file that explains variables, labels, forms, and allowed
     values. The assistant uses it to ground questions in study meaning.

   hosted LLM
     A model provider outside the user's machine, such as Anthropic,
     OpenAI, or Google. Hosted providers require an API key and local
     study-team approval.

   local LLM
     A model running on the user's own machine, usually through Ollama.
     This is the recommended starting point when the team wants to avoid
     external model calls.

   PHI
     Protected health information. In this project, raw study files are
     treated as PHI-bearing unless the study team has verified otherwise.

   PHI key
     A local secret used by the scrubber to create stable pseudonyms and
     date shifts. It lives outside the repository.

   published bundle
     The scrubbed study output under ``output/{STUDY}/llm_source/``.
     This is the main bundle the assistant uses for study questions.

   raw study files
     The source files placed under ``data/raw/{STUDY}/``. These files are
     treated as sensitive and are not the assistant's normal working
     material.

   scrub
     The step that removes, masks, caps, generalizes, or pseudonymizes
     sensitive dataset fields before publishing the bundle.

   study name
     The folder name for the study, such as ``Indo-VAP``. It is used to
     find ``data/raw/{STUDY}/`` and write ``output/{STUDY}/``.

   llm_source bundle
     The published bundle directory ``output/{STUDY}/llm_source/``. It
     contains the dataset schema, dictionary mapping, study metadata,
     and concept index used by the assistant.

Developer Terms
---------------

If you need definitions for code-level privacy controls or pipeline
internals, use the :doc:`../developer_guide/index`. For IRB/IEC or
auditor review, use :doc:`../irb_auditor/index`. Those details are
intentionally kept out of the user guide.
