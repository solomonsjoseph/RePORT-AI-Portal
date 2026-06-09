Configuration
=============

Most users only need a few settings: study name, model provider, optional
API key, and a small number of PHI-related choices.

Minimum Settings
----------------

Set the study name if the folder cannot be auto-detected:

.. code-block:: bash

   export STUDY_NAME=Indo-VAP

Choose one model provider.

Local Ollama, no API key:

.. code-block:: bash

   export LLM_PROVIDER=ollama
   export LLM_MODEL=qwen3:8b

Hosted providers:

.. code-block:: bash

   export LLM_PROVIDER=anthropic
   export ANTHROPIC_API_KEY=sk-ant-...
   export LLM_MODEL=claude-opus-4-7

   # or
   export LLM_PROVIDER=openai
   export OPENAI_API_KEY=sk-...
   export LLM_MODEL=gpt-5.5

   # or
   export LLM_PROVIDER=google-genai
   export GOOGLE_API_KEY=...
   export LLM_MODEL=gemini-3.1-pro-preview

The web UI also lets you choose the provider and paste the key during
setup.

Recommended Default
-------------------

For the strongest local privacy posture, start with Ollama:

.. code-block:: bash

   export LLM_PROVIDER=ollama
   export LLM_MODEL=qwen3:8b

Ollama runs on the user's machine and does not require an external API
key.

Study Folder
------------

The expected input layout is:

.. code-block:: text

   data/raw/{STUDY_NAME}/
   ├── datasets/
   └── annotated_pdfs/        # optional

The main output appears under:

.. code-block:: text

   output/{STUDY_NAME}/

PHI-Related Settings
--------------------

Most users should leave these alone unless the study team has made a
specific decision.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Setting
     - When to use it
   * - ``REPORTALIN_TMPFS_STAGING=1``
     - Linux-only option to place temporary staging files in memory when
       available.
   * - ``REPORTAL_PROCESS_ROLE=llm-agent``
     - **Security-critical.** Set in agent subprocess environments to deny
       audit-ledger writes and maintainer-only CLI paths (such as
       ``--resume-held``) from agent processes. Any agent that attempts those
       operations while this variable is set receives an explicit refusal.
   * - ``REPORTAL_AGENT_MODEL``
     - Overrides the default agent model id (``config.AGENT_MODEL_ID``).
       Takes effect at import time via ``config.py``.
   * - ``REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT`` / ``REPORTAL_PIPELINE_LOCK_PARENT_PID``
     - **Internal pipeline use only — not for operators to set manually.**
       The ``extract_to_llm_source`` wrapper sets these in the child
       ``main.py --pipeline`` subprocess to pass the already-acquired
       pipeline lock baton. ``main.py`` validates the claimed parent PID
       is live and equals ``os.getppid()`` before honouring the baton; a
       stale or mismatched value causes the child to acquire the lock normally.

PHI Key
-------

The scrubber needs one local PHI key for stable pseudonyms and date
shifting. Normal web-UI users do not create this key manually; the
**Load Study** flow creates it when needed.

Developers and deployment operators can provision the key from the
command line when running the pipeline outside the web UI. Keep this key
outside the repository and back it up according to the study team's
policy. Rotating it changes pseudonyms and requires a full re-run.

Where to Put More Detail
------------------------

User-facing configuration should stay short. Detailed implementation
behavior belongs in:

* :doc:`../developer_guide/operations`
* :doc:`../developer_guide/architecture`
* :doc:`../developer_guide/phi_architecture`

Next Step
---------

Run :doc:`quickstart` after choosing the settings above.
