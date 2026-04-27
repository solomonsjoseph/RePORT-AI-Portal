Tech Stack
==========

Rewritten 2026-04-27 against the v0.20.0 code state. Every runtime and
development dependency, grouped by role, with one paragraph each on
**what** it is, **why** it was chosen, and **how** the project uses
it. Pinned versions and rationale live in ``pyproject.toml``.

Runtime — language and tooling
------------------------------

Python 3.11+
~~~~~~~~~~~~

**What.** The host language. **Why.** Required for
:mod:`concurrent.futures` clean shutdown semantics, ``asyncio.timeout``,
and the ``X | Y`` union syntax used throughout the codebase. **How.**
``pyproject.toml`` pins ``requires-python = ">=3.11"``; CI matrix
runs against 3.11 / 3.12 / 3.13.

uv
~~

**What.** A Rust-based pip / poetry / pipx replacement.
**Why.** 10-100× faster lockfile resolution; reproducible
environments. **How.** Project-wide convention: ``uv sync
--all-groups`` to install, ``uv run`` to invoke. The Makefile assumes
``uv``; CI installs it via the official ``astral-sh/setup-uv@v3``
action.

Ruff
~~~~

**What.** A fast Rust-based Python linter + formatter.
**Why.** Single tool that replaces flake8, isort, pyupgrade, and
includes ``S`` (flake8-bandit) security rules. **How.** Configuration
at ``pyproject.toml:215-241`` (the ``[tool.ruff.lint]`` section).
``S101`` (assert) is per-file-ignored for ``tests/`` since pytest
idiom; ``S603`` (subprocess) is whitelisted at our hardened
``subprocess.run`` callsites with ``# noqa: S603``.

mypy
~~~~

**What.** Static type checker. **Why.** Catches a class of LLM-flow
bugs (e.g., the v0.18.0 PR #15 type errors I caught at PR-prep time:
``Anthropic`` vs ``Client`` cross-binding). **How.**
``pyproject.toml`` configures ``ignore_missing_imports = true`` so
optional deps don't block; custom stubs live in ``typings/`` for
``google.genai`` and ``anthropic``.

Pytest
~~~~~~

**What.** Test runner. **Why.** Mature ecosystem, ``conftest.py``
fixtures, deterministic markers. **How.**
:doc:`testing` covers the test-file conventions. ``make test`` runs
the deterministic subset that excludes the AI Assistant construction
smokes; ``make test-all`` runs the full suite.

Runtime — pipeline
------------------

pandas
~~~~~~

**What.** Tabular dataframe library. **Why.** Excel reading, JSONL
output, dataset cleanup, k-anonymity equivalence-class lookups all
ride on pandas. **How.**
:mod:`scripts.extraction.dataset_pipeline` reads the raw Excel into
a DataFrame; per-row records are serialised to JSONL with the
provenance dict.

openpyxl
~~~~~~~~

**What.** Excel ``.xlsx`` reader/writer. **Why.** pandas's default
``.xlsx`` engine. **How.** Used implicitly by ``pd.read_excel`` for
the dictionary + dataset legs.

pypdf
~~~~~

**What.** Lightweight PDF text extractor. **Why.** Powers the legacy
raw-PDF API path. **How.** Used in
:mod:`scripts.extraction.extract_pdf_data` when the operator opts
into the gated raw-PDF API path with the two-part attestation.

pdfplumber
~~~~~~~~~~

**What.** Layout-aware PDF extractor. **Why.** Per-character bounding
boxes give better structure recovery than ``pypdf`` for complex
multi-section CRFs. **How.** As of PR #15 (v0.19.0), pdfplumber is
the **always-on code path** inside the two-way PDF orchestrator
(:mod:`scripts.extraction.pdf_pipeline`). Extracted text is
PHI-redacted before any LLM call; the LLM response is merged with
the code candidate via ``_merge``.

PyYAML
~~~~~~

**What.** YAML parser. **Why.** The PHI scrub catalog
(``scripts/security/phi_scrub.yaml``) and the study-knowledge
overlay (``config/study_knowledge.yaml``) ship as YAML so domain
experts can edit without touching code. **How.** Loaded once at
import time; cached.

Runtime — agent
---------------

LangChain + LangGraph
~~~~~~~~~~~~~~~~~~~~~

**What.** LLM-agent framework. **Why.** ``init_chat_model`` gives
provider-agnostic construction (Anthropic / OpenAI / Google / Ollama
/ NVIDIA all behind one API); LangGraph's ReAct prebuilt is the
agent topology. **How.** :mod:`scripts.ai_assistant.agent_graph` is
the only module that constructs an LLM client; every client takes
``api_key=`` as an explicit kwarg sourced from the in-memory
KeyStore (PR #3) — no ``os.environ`` lookup at construction time.

LangChain provider packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Per-provider LangChain integrations: ``langchain-anthropic``,
``langchain-openai``, ``langchain-google-genai``,
``langchain-ollama``, ``langchain-nvidia-ai-endpoints``. **Why.**
Each provider has its own client + auth + retry semantics; the
LangChain wrappers normalise them. **How.** All five are declared
runtime dependencies; ``init_chat_model("anthropic:claude-...")``
dispatches to the right wrapper based on the provider prefix.

anthropic, google-genai (raw SDKs)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Provider raw SDKs. **Why.** The PDF orchestrator's
``_extract_via_llm`` calls the raw SDK directly because the
orchestrator's contract is a single non-streaming JSON response
with PHI-redacted text — heavier LangChain machinery is overkill
here. **How.**
:func:`scripts.extraction.pdf_pipeline._extract_via_llm` dispatches
on ``provider`` ∈ ``{anthropic, google, gemini, google-genai}``.

Streamlit ≥ 1.38, < 2.0
~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Web UI framework. **Why.** Fast prototyping; built-in
``session_state``, file uploaders, chat widgets. **How.**
``scripts/ai_assistant/web_ui.py`` is the entry; UI primitives
factored into ``scripts/ai_assistant/ui/{wizard,chat,conversations,
streaming,...}.py``. Theme + bridge JS in
``scripts/ai_assistant/ui/assets/``.

Plotly + Kaleido
~~~~~~~~~~~~~~~~

**What.** Interactive charts (Plotly) + headless export (Kaleido).
**Why.** ``run_python_analysis`` renders model output as Plotly
figures; Kaleido exports them as PNG so the persisted analysis
``.py`` file produces reproducible images on a fresh run. **How.**
Used inside the sandbox subprocess child only — the agent's parent
process does not ``import plotly``.

Runtime — security
------------------

scripts.security.* (in-tree)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** The PHI handling surface lives entirely in-tree:

* :mod:`scripts.security.phi_scrub` — 8-action honest-broker catalog
* :mod:`scripts.security.phi_patterns` — shared regex catalog
* :mod:`scripts.security.phi_allowlist` — clinical-phrase exemption
* :mod:`scripts.security.phi_gate` — agent-output gate
* :mod:`scripts.security.kanon_gate` — k-anon (k=5) + l-diversity (l=2)
* :mod:`scripts.security.secure_env` — zone guards
* :mod:`scripts.security.phi_ner` — local-Ollama narrative NER design
  stub (feature-flagged, raises until implemented)

**Why.** No external dependency for PHI handling — auditors can read
every line of the security surface without trusting an upstream maintainer.
**How.** See :doc:`phi_architecture` for the full architecture.

cryptography (HMAC + secure_zero_fill)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Standard library wrapper for HMAC-SHA256 and secure
random. **Why.** Used for per-subject SANT date jitter and ID
pseudonymization. **How.** :func:`scripts.security.phi_scrub.pseudo_id`,
:func:`scripts.security.phi_scrub.date_offset_days`.

Runtime — observability
-----------------------

Python ``logging`` (with custom redactor)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Standard logging. **Why.** Familiar API; the redactor is a
single ``logging.Filter`` so we don't need a logging-framework
dependency. **How.**
:func:`scripts.utils.log_hygiene.install_phi_redactor` attaches a
filter to the root logger that scrubs API keys + PHI patterns from
every log line at format time.

structlog (deferred)
~~~~~~~~~~~~~~~~~~~~

**What.** Not currently used. **Why mentioned.** Open question
whether to migrate to ``structlog`` for structured logging in a
future phase; for now standard logging is sufficient.

Development
-----------

pip-audit
~~~~~~~~~

**What.** Dependency vulnerability scanner. **Why.** Catches known
CVEs in pinned dependencies before they reach production. **How.**
Runs on demand (``make security``); not yet a dedicated CI job
(documented gap in ``docs/irb_dossier/conformance_matrix.md``,
outside the Sphinx tree).

Sphinx + sphinx-rtd-theme + myst-parser
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** Documentation generator. **Why.** RST + autodoc gives
free API reference from docstrings; mature toctree semantics. **How.**
``make docs`` builds; ``make docs-quality`` runs the doc-freshness
lint and a ``-W`` (warnings as errors) Sphinx rebuild. CI gate at
``.github/workflows/docs-quality-check.yml``.

Custom type stubs
-----------------

``typings/`` ships in-tree stubs for two providers whose upstream
typing is incomplete:

* ``typings/anthropic/`` — covers the raw SDK's
  ``messages.create`` / ``messages.stream`` surface used by the PDF
  orchestrator + legacy raw-PDF path.
* ``typings/google/`` — covers the
  ``google.genai.Client.models.generate_content`` surface.

The ``mypy`` config picks up ``typings/`` automatically via
``mypy_path``.

Pinning policy
--------------

* **Major versions pinned with caret semantics** for runtime deps that
  the agent talks to (LangChain, Anthropic, Google) — e.g.
  ``langchain>=1.0.0,<2.0.0``. Reason: provider APIs evolve; we
  catch the v2 break in CI before it reaches production.
* **Streamlit pinned to ``>=1.38, <2.0``** because
  ``st.session_state`` semantics changed materially across major
  versions.
* **All other deps** pinned with ``>=`` only; ``uv.lock`` records
  the resolved versions reproducibly.

Where this is enforced: ``pyproject.toml`` (top-level + dev /
test / docs optional groups). The lockfile (``uv.lock``) is the
source of truth for the installed tree.
