Tech Stack
==========

**What.** Every runtime and development dependency in the project,
grouped by role, with a sentence on why it was chosen and how the
project uses it.

**Why.** A new contributor reading ``pyproject.toml`` sees a flat list of
package names. That doesn't tell them which deps they can swap, which
are load-bearing for PHI guarantees, and which are there for
developer ergonomics. This page is the rationale layer.

**How.** Grouped by role (runtime / extraction / LLM / UI / testing /
linting / documentation). Each entry: What it is, Why we picked it, How
we use it. Version policy at the end.

.. contents:: On this page
   :local:
   :depth: 2

Runtime Core
------------

Python 3.11+
~~~~~~~~~~~~

**What.** The language runtime.
**Why.** 3.11 brings ``zoneinfo`` / faster interpreter / better error
messages; all of :mod:`scripts.security` relies on ``from __future__
import annotations`` which is stable from 3.7 but the rest of the stack
needs 3.11 at minimum (pandas 2.x, langchain 0.3, etc.).
**How.** Pinned at the ``pyproject.toml`` ``requires-python = ">=3.11"``
line. CI runs against 3.11 and 3.12.

uv (package manager)
~~~~~~~~~~~~~~~~~~~~

**What.** Fast Rust-based Python package manager by Astral.
**Why.** Replaces pip/pip-tools/virtualenv/venv with one tool; lockfile
(``uv.lock``) is reproducible across macOS / Linux / Windows; ``uv
sync --all-groups`` is 10× faster than ``pip install -r requirements``.
**How.** Every dev-facing command runs through ``uv`` (``uv run pytest``,
``uv run ruff check``, ``uv run mypy``). ``make`` targets call
``uv run`` under the hood.

Extraction + Transformation
---------------------------

pandas
~~~~~~

**What.** Tabular data library.
**Why.** Standard Python answer to "read a spreadsheet, normalize rows,
write JSONL". The ``itertuples()`` + ``to_json(orient='records', lines=True)``
combination drives the extraction path.
**How.** :mod:`scripts.extraction.dataset_pipeline` uses
``pd.read_excel`` / ``pd.read_csv`` with carefully chosen NA-handling
options (clinical strings like "NR" / "NA" must NOT be coerced to
``NaN``). ``_TABULAR_NA_OPTIONS`` centralises the NA policy.

openpyxl
~~~~~~~~

**What.** Python reader for Excel 2007+ ``.xlsx`` files.
**Why.** Pandas backend of choice for ``.xlsx`` on macOS / Linux (no
Excel required). Alternatives considered: ``xlsx2csv`` (lossy), ``xlrd``
(abandoned for ``.xlsx``).
**How.** Used implicitly via ``pd.read_excel(engine='openpyxl')``.

xlrd
~~~~

**What.** Legacy Excel 97-2004 ``.xls`` reader.
**Why.** Some Indian clinical study datasets still ship as ``.xls``;
pandas dropped ``xlrd`` support in 1.2 for ``.xlsx`` but kept it for
legacy ``.xls``. We need both.
**How.** Pinned via ``xlrd==1.2.0`` in ``pyproject.toml`` (the last
version that supports ``.xls``).

PyPDF / pdfplumber
~~~~~~~~~~~~~~~~~~

**What.** PDF parsing libraries. Both are wired into the runtime as
of v0.20.0.
**Why.** Annotated CRFs contain both flat text and form-field layout.
``pypdf`` powers the legacy raw-PDF API path (gated by the two-part
``REPORTALIN_PDF_PHI_FREE`` attestation). ``pdfplumber`` powers the
always-on code path inside the two-way PDF orchestrator
(``scripts.extraction.pdf_pipeline``, PR #15) — extracted text is
PHI-redacted before any LLM call, paired with the LLM response via
the ``_merge`` step.
**How.** Legacy path: :mod:`scripts.extraction.extract_pdf_data`.
Orchestrator path: :mod:`scripts.extraction.pdf_pipeline` (the wizard's
"Load Study" flow always selects this path; CLI users can opt in via
``REPORTALIN_PDF_EXTRACTION_MODE=llm``).

PyYAML
~~~~~~

**What.** YAML parser.
**Why.** ``scripts/security/phi_scrub.yaml`` is YAML for two reasons:
(a) humans read + review rule catalogs more comfortably than Python
literals, (b) catalog changes don't require Python edits (just YAML).
``config/config.yaml`` and ``config/study_knowledge.yaml`` are also YAML.
**How.** :func:`scripts.security.phi_scrub.load_scrub_config` +
:func:`config._load_yaml_config`.

LLM Integration
---------------

LangChain + LangGraph
~~~~~~~~~~~~~~~~~~~~~

**What.** LangChain orchestrates tool-using LLMs;
LangGraph is the state-machine layer for ReAct agents.
**Why.** ``create_react_agent`` gives us a provider-agnostic ReAct
implementation across Anthropic / OpenAI / Google / Ollama without
per-provider glue. The tool decorator (``@tool``) is the idiomatic
wrapper for the 12 structured-data tools the agent uses
(canonical list: :data:`scripts.ai_assistant.agent_tools.ALL_TOOLS`).
**How.** :mod:`scripts.ai_assistant.agent_graph` builds the graph;
:mod:`scripts.ai_assistant.agent_tools` holds the ``@tool``-decorated
functions (every one wrapped by ``@phi_safe_return`` via
:mod:`scripts.ai_assistant.phi_safe`).

``init_chat_model`` (from langchain-core)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Unified chat-model constructor across providers.
**Why.** One line per provider — ``init_chat_model("claude-sonnet-4",
model_provider="anthropic")`` — instead of per-provider import and
config.
**How.** The agent startup reads ``config.LLM_PROVIDER`` + ``config.LLM_MODEL``
and hands them to ``init_chat_model`` once.

User Interface
--------------

Streamlit
~~~~~~~~~

**What.** Python web-app framework.
**Why.** Researchers are not web developers; Streamlit gives us a
chat + plot + table UI with ``streamlit run`` and zero build step. The
dev feedback loop (edit Python → reload page) matches the agent-tool
dev loop.
**How.** :mod:`scripts.ai_assistant.web_ui` is the entry point; UI
helpers are split across ``scripts/ai_assistant/ui/``.

Plotly + Kaleido
~~~~~~~~~~~~~~~~

**What.** Interactive plotting library + static-image exporter.
**Why.** Researcher audience wants both in-browser interactive plots
(Plotly HTML) and publication-ready static PNG/SVG (via Kaleido).
**How.** :mod:`scripts.ai_assistant.analytical_engine` returns Plotly
figures; the UI renders them interactively and offers a Kaleido export
for publication handoff.

Testing + Quality
-----------------

pytest
~~~~~~

**What.** Test runner.
**Why.** Industry-standard Python test framework; fixtures + parametrize
are idiomatic; rich plugin ecosystem.
**How.** 775 tests in ``tests/``, organised by module. ``make test``
runs the 703-test deterministic subset (excludes agent-tools, agent-graph,
CLI, and telemetry tests); ``make test-all`` runs the full 775.

ruff
~~~~

**What.** Rust-based linter + formatter.
**Why.** Replaces flake8 / isort / black with one tool, 50-100× faster.
Catches SIM / RUF / BLE / S-class issues in addition to formatting.
**How.** ``pyproject.toml [tool.ruff]`` config pins line-length=100
and the enabled rule set. ``make lint`` is ``ruff check + ruff format``.

MyPy
~~~~

**What.** Static type checker.
**Why.** Catches ``None``-deref / type-mismatch / missing-key bugs
before runtime. The PHI-scrub catalogue in particular benefits because
a type error in the priority dispatch would silently fail on a new row
shape.
**How.** ``pyproject.toml [tool.mypy]`` with
``ignore_missing_imports=True`` (we cannot stub every LangChain version).

Custom type stubs (``typings/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Hand-written ``.pyi`` files for ``google`` / ``anthropic``.
**Why.** The vendor libraries ship incomplete type hints or change API
shape between releases; local stubs give us stable types for the
narrow surface we actually use.
**How.** Referenced via ``mypy_path`` in ``pyproject.toml``.

Documentation
-------------

Sphinx
~~~~~~

**What.** Python documentation generator.
**Why.** RST + autodoc + intersphinx are the stable idiom for Python
library docs. The ``sphinx-rtd-theme`` renders cleanly on GitHub Pages.
**How.** ``docs/sphinx/conf.py`` + ``make docs``. Build output under
``docs/sphinx/_build/html``.

Scientific Stack
----------------

The analytical engine uses a focused scientific-Python subset:

* **numpy** — the array primitive. Everything downstream builds on it.
* **scipy** — statistics (t-tests, chi-square, distributions).
* **statsmodels** — logistic regression, survival analysis, Cox
  models. Chosen over ``scikit-learn`` because ``statsmodels`` exposes
  standard epi output (coefficients, confidence intervals, p-values)
  directly rather than requiring post-hoc extraction.
* **matplotlib** — still the de-facto Python plotting library; used for
  static publication plots alongside Plotly for interactive UI plots.

Version Policy
--------------

* **Runtime-critical** deps (pandas, langchain, statsmodels, pyyaml) are
  pinned to a tested version range in ``pyproject.toml``. Upgrades
  require a PR, a full ``make ci`` green, and a note in the commit
  message if any public behaviour shifts.
* **Dev tooling** (ruff, mypy, pytest) tracks latest-stable. A ruff or
  mypy version bump can be a chore-commit if no code changes are needed.
* **LLM providers** (anthropic, openai, google-generativeai) are pinned
  conservatively because provider SDKs have a history of breaking
  changes at minor-version bumps. Upgrades wait for a user-facing need.
* **Security patches** across all of the above are fast-tracked —
  ``uv lock --upgrade-package <name>`` + re-run CI + merge.

Dependencies Intentionally NOT in the Stack
-------------------------------------------

* **presidio-analyzer** — see :doc:`decisions` ADR-004.
* **cryptography / pycryptodome** — not needed; HMAC-SHA256 is in the
  stdlib ``hmac`` + ``hashlib`` modules, and we deliberately do NOT
  use AES at rest (see ADR-002).
* **dvc / git-lfs** — the pipeline does not track raw data in git; the
  raw tree lives under ``.gitignore``.
* **airflow / prefect** — the pipeline runs locally via
  ``make pipeline``; no orchestration framework needed for a single-
  study local-first runtime.
* **docker** — the stack runs in a ``uv`` virtualenv. Dockerisation is
  a deployment concern left to the operator's site.
