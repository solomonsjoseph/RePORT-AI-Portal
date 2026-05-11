Frequently Asked Questions
==========================

What is RePORT AI Portal?
-------------------------

It is a local-first assistant for one clinical research study. It helps
the study team turn local source files into a PHI-scrubbed bundle and
ask questions about that bundle through a chat interface.

Who is it for?
--------------

Clinical researchers, data managers, site operators, PIs, and reviewers
working with one locked study.

What can I ask it?
------------------

Examples:

* How many subjects match a cohort definition?
* What variables are available for a study question?
* Summarize missingness for a field.
* Create a descriptive table or plot.
* Run a basic analysis and explain the result in study language.

What files do I need?
---------------------

At minimum, place study datasets and a data dictionary under
``data/raw/{STUDY_NAME}/``:

.. code-block:: text

   data/raw/Indo-VAP/
   ├── datasets/
   ├── data_dictionary/
   └── annotated_pdfs/        # optional

Do I need an API key?
---------------------

Not if you use local Ollama. Hosted providers such as Anthropic, OpenAI,
and Google require API keys.

Start with Ollama when possible:

.. code-block:: bash

   export LLM_PROVIDER=ollama
   export LLM_MODEL=qwen3:8b

How do I install it?
--------------------

Use:

.. code-block:: bash

   curl -LsSf https://astral.sh/uv/install.sh | sh
   git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
   cd RePORT-AI-Portal
   make chat

Then follow :doc:`quickstart`.

How do I run it?
----------------

For the web UI:

.. code-block:: bash

   make chat

For developer/operator pipeline runs only:

.. code-block:: bash

   make pipeline

Can I use an existing processed study?
--------------------------------------

Yes. If ``output/{STUDY}/trio_bundle/`` already exists, the web UI can
use that existing published bundle instead of loading the study again.

Does the assistant read raw files?
----------------------------------

The intended user workflow is that the assistant answers from the
published, scrubbed study bundle under ``output/{STUDY}/trio_bundle/``.
Raw files belong under ``data/raw/{STUDY}/`` and are handled by the
loading pipeline.

For the full technical boundary, see
:doc:`../developer_guide/phi_architecture`.

Does retrieval make answers more accurate?
------------------------------------------

Yes, but it is not a 100% guarantee. The assistant does not answer from
model memory alone. It searches the published bundle with structured tools
for variables, forms, datasets, CRF/PDF text, and deterministic analysis
outputs, then shows source/tool evidence with the response. That grounding
reduces hallucination risk and makes answers easier to verify.

Accuracy still depends on the reviewed bundle, data dictionary quality, PDF
extraction quality, and whether the user question maps cleanly to available
study artifacts. The realistic target is high, measured accuracy on a
maintained evaluation set, not a universal 100%. To move closer, add reviewed
snapshots, improve variable descriptions, include missing protocol/CRF text,
and regression-test representative questions after each retrieval change.

What if my PDFs may contain PHI?
--------------------------------

Treat them as PHI-bearing by default. The portal runs PDFs through the
PHI-scrub before any text reaches a hosted LLM provider.

Can I skip PDFs?
----------------

Yes. PDFs are optional. The portal can still work from datasets and the
data dictionary.

Where do I check what happened during a run?
--------------------------------------------

Start with:

* ``output/{STUDY}/README.md``
* ``output/{STUDY}/audit/``
* the terminal output from ``make pipeline`` or ``make chat``

What if I find raw PHI in output?
---------------------------------

Stop using and sharing the affected output. Preserve the file path and
row context for the study team, but do not paste raw PHI into tickets or
chat. Notify the study PI, data manager, and local review contact, then
re-run only after the cause is fixed.

Can it handle large datasets?
-----------------------------

It is designed for study-scale files, but runtime depends on local
hardware, file size, and model choice. If a local model is slow, use a
smaller Ollama model or a machine with more memory.

Is there a GUI?
---------------

Yes. Run:

.. code-block:: bash

   make chat

The web UI guides the user through provider selection, study loading,
and chat.

Where are developer details?
----------------------------

Use the :doc:`../developer_guide/index` for architecture, source files,
tests, code-level behavior, and contributor workflow. Use
:doc:`../irb_auditor/index` for reviewer-facing PHI handling and
control evidence.
