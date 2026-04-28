Installation
============

This page covers setup for users and local operators. Developer tooling
and contribution workflow live in :doc:`../developer_guide/index`.

Requirements
------------

* macOS, Linux, or Windows with WSL
* Python 3.11 or newer
* Git
* ``uv`` package manager
* 8 GB RAM minimum, 16 GB recommended
* About 2 GB free disk space for dependencies

Install
-------

Install ``uv``:

.. code-block:: bash

   curl -LsSf https://astral.sh/uv/install.sh | sh

Clone the project:

.. code-block:: bash

   git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
   cd RePORT-AI-Portal

Start the Web UI
----------------

.. code-block:: bash

   make chat

``make chat`` installs the web and AI Assistant dependency groups it
needs, then opens a local Streamlit page. Use it to select the model
provider, load a study, and start chat.

Optional Developer Setup
------------------------

Developers who need the full test, docs, profiling, and LLM toolchain can
install every dependency group explicitly:

.. code-block:: bash

   make sync

Verify the full developer install:

.. code-block:: bash

   uv run python -c "import scripts; print('Installation successful')"

Prepare for First Run
---------------------

Before loading a study:

1. Put study files under ``data/raw/{STUDY_NAME}/``.
2. Choose a model provider in :doc:`configuration`.
3. Use **Load Study** in the web UI. It creates the local PHI key if one
   does not already exist.

Then continue with :doc:`quickstart`.
