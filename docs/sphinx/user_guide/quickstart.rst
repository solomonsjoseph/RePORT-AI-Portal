Quick Start
===========

Use this walkthrough to load one study and ask the first question.

Before You Start
----------------

You need:

* the project installed; see :doc:`installation`;
* one study folder under ``data/raw/{STUDY_NAME}/``;
* a local Ollama model or a hosted LLM API key;
* access to the web UI;
* a prepared ``llm_source`` bundle, or a plugin-enabled LLM session that can
  run the ``report-ai-study-pipeline`` workflow.

1. Place Study Files
--------------------

Expected layout:

.. code-block:: text

   data/raw/Indo-VAP/
   ├── datasets/
   ├── data_dictionary/
   └── annotated_pdfs/        # optional

Set the study name if needed:

.. code-block:: bash

   export STUDY_NAME=Indo-VAP

On Windows PowerShell, use ``$env:STUDY_NAME = "Indo-VAP"`` instead.

2. Choose a Model Provider
--------------------------

Recommended local setup:

.. code-block:: bash

   export LLM_PROVIDER=ollama
   export LLM_MODEL=qwen3:8b

Hosted provider example:

.. code-block:: bash

   export LLM_PROVIDER=anthropic
   export ANTHROPIC_API_KEY=sk-ant-...
   export LLM_MODEL=claude-opus-4-7

On Windows PowerShell, set environment variables with ``$env:NAME =
"value"``. You can also choose the provider from the web UI instead of
setting these values in the shell.

See :doc:`configuration` for OpenAI, Google, and PDF-related settings.

3. Prepare the Study
--------------------

Use the web UI:

.. code-block:: bash

   make chat

Then prepare the study bundle through the ``report-ai-study-pipeline``
plugin, or proceed with an existing valid ``output/{STUDY}/llm_source/``
bundle when one is already present. The plugin workflow runs duplicate
handling, Source Truth creation from printed PDFs plus row-1 headers, and
PHI-safe dataset publishing. The data dictionary remains handled by the host
repo.

The command-line ``make study STUDY=<name>`` path runs the same orchestrator
from the shell for developers and deployment operators. It drives the host
publish path in-lock via the dataset child skill.

Expected result:

.. code-block:: text

   output/Indo-VAP/
   ├── llm_source/
   ├── audit/
   └── agent/

4. Start Chat
-------------

If the web UI is not already open:

.. code-block:: bash

   make chat

Ask a simple first question, for example:

.. code-block:: text

   How many subjects are in this study?

Then try a study-specific question:

.. code-block:: text

   What variables are available for baseline demographics?

Common Problems
---------------

**PHI key not found**
   Use the study preparation flow in the web UI or plugin workflow. If the
   command-line dataset publish reports this error, ask a
   developer or operator to provision the key.

**Study not found**
   Confirm ``STUDY_NAME`` matches the folder under ``data/raw/``.

**API key not found**
   Export the key for the selected hosted provider, or use Ollama.

**PDF warning**
   PDFs are optional. If PDFs may contain PHI, review
   :doc:`configuration` before enabling hosted PDF processing.

Next Steps
----------

* :doc:`data_pipeline` - what happens when a study is loaded.
* :doc:`configuration` - common runtime settings.
* :doc:`faq` - common user and privacy questions.
