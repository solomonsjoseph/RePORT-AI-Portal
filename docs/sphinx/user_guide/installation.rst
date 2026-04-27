Installation
============

Prerequisites
-------------

Before installing RePORT AI Portal, ensure you have:

* Python 3.11 or higher (3.13 recommended)
* `uv <https://docs.astral.sh/uv/>`_ package manager
* Git (for cloning the repository)

System Requirements
~~~~~~~~~~~~~~~~~~~

* **Operating System**: macOS, Linux, or Windows
* **Memory**: Minimum 8GB RAM (16GB recommended for large datasets)
* **Disk Space**: 2GB for the application and dependencies

Installation Steps
------------------

1. Install uv (if not already installed)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   curl -LsSf https://astral.sh/uv/install.sh | sh

2. Clone the Repository
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
   cd RePORT-AI-Portal

3. Quick Start (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make quickstart    # Syncs dependencies and launches the app

Or step-by-step:

.. code-block:: bash

   uv sync            # Install all dependencies (creates .venv automatically)
   make pipeline      # Run the full data processing pipeline

Required Dependencies
~~~~~~~~~~~~~~~~~~~~~

The main dependencies include:

* **pandas**: Data manipulation and analysis
* **openpyxl**: Excel file reading/writing
* **pypdf/pdfplumber**: PDF processing
* **cryptography**: AES-256-GCM encryption primitives

Optional Dependencies
~~~~~~~~~~~~~~~~~~~~~

For the full stack (recommended — installs all groups):

.. code-block:: bash

   uv sync --all-groups

Or install individual groups as needed:

For AI Assistant (LangGraph + LangChain providers):

.. code-block:: bash

   uv sync --group ai_assistant

This includes:

* langchain-core / langgraph: ReAct agent framework
* langchain-openai / langchain-anthropic / langchain-google-genai / langchain-ollama: LLM providers
* kaleido (0.2.x, ``<1.0``): static image export for Plotly figures (required by the analytical agent to save charts to disk and embed them in chat exports)

For the Streamlit web UI:

.. code-block:: bash

   uv sync --group web

This includes:

* streamlit: Browser-based chat interface (``make chat`` / ``main.py --web``)

For additional LLM SDK access:

.. code-block:: bash

   uv sync --group llm

This includes:

* anthropic: Direct Anthropic SDK
* google-genai: Direct Google GenAI SDK

For development:

.. code-block:: bash

   uv sync --group dev

This includes:

* ruff: Fast Python linter and formatter
* mypy: Static type checking
* pytest: Testing framework (included in dev group)
* pip-audit: Security vulnerability auditing

For documentation:

.. code-block:: bash

   uv sync --group docs

This includes:

* sphinx: Documentation generation
* sphinx-rtd-theme: Read the Docs theme
* sphinx-autodoc-typehints: Type hint rendering

4. Verify Installation
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   uv run python -c "import scripts; print('Installation successful!')"

Configuration
-------------

After installation, you'll need to configure:

1. **Environment Variables**: Copy `.env.example` to `.env` and set your API keys
2. **Config File**: Adjust `config.py` for your specific needs
3. **Data Paths**: Update paths in config to point to your data directories

Next Steps
----------

* See :doc:`configuration` for detailed configuration options
* See :doc:`quickstart` for your first data extraction
