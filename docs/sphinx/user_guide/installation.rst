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

Install dependencies:

.. code-block:: bash

   uv sync --all-groups

Verify the install:

.. code-block:: bash

   uv run python -c "import scripts; print('Installation successful')"

Start the Web UI
----------------

.. code-block:: bash

   make chat

The app opens a local Streamlit page. Use it to select the model
provider, load a study, and start chat.

Prepare for First Run
---------------------

Before loading a study:

1. Put study files under ``data/raw/{STUDY_NAME}/``.
2. Choose a model provider in :doc:`configuration`.
3. Create the PHI key:

   .. code-block:: bash

      python -m scripts.security.phi_scrub bootstrap-key

Then continue with :doc:`quickstart`.
