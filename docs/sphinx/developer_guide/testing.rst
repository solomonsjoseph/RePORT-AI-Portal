Testing
=======

This page documents the test and verification gates that are active in
the repository today. It intentionally avoids static test counts; the
authoritative count is the pytest output for the commit under review.

Active Commands
---------------

Run these from the repository root:

.. code-block:: bash

   make test          # deterministic subset; excludes selected AI Assistant construction tests
   make test-all      # full pytest suite
   make lint          # ruff check --fix + ruff format
   make typecheck     # mypy scripts/ main.py config.py
   make security      # pip-audit dependency scan
   make verify        # fast local readiness gate
   make docs-quality  # doc freshness + Sphinx warnings-as-errors build

Direct equivalents:

.. code-block:: bash

   uv run pytest tests/
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy scripts/ main.py config.py --ignore-missing-imports
   uv run python scripts/lint_doc_freshness.py

Current Test Layout
-------------------

The suite is intentionally flat except for security-focused tests:

.. code-block:: text

   tests/
   ├── conftest.py
   ├── test_agent_graph.py
   ├── test_agent_tools.py
   ├── test_dataset_pipeline.py
   ├── test_extract_pdf_data.py
   ├── test_load_dictionary.py
   ├── test_phi_scrub.py
   ├── test_run_study_analysis.py
   ├── test_web_ui.py
   ├── test_*.py
   └── security/
       ├── test_adversarial_phi_safe.py
       ├── test_kanon_l_diversity.py
       ├── test_keystore.py
       ├── test_pdf_redaction_pipeline.py
       ├── test_sandbox_isolation.py
       └── test_*.py

There are no active ``tests/ai_assistant/`` or ``tests/extraction/``
subpackages. Agent, extraction, UI, and pipeline tests live as
top-level ``tests/test_*.py`` modules; security regression tests live
under ``tests/security/``.

What Each Gate Proves
---------------------

``make test``
   Runs the deterministic pytest subset. Use it for fast local checks
   when you did not touch LLM construction, CLI provider selection, or
   telemetry surfaces.

``make test-all``
   Runs the full suite. Use it before PRs that touch PHI handling,
   agent tools, provider construction, pipeline flow, or public docs.

``make verify``
   Runs the fast local readiness check used by the maintainer workflow:
   Ruff, mypy, and presence checks for load-bearing security modules.
   It is not a substitute for ``make test-all`` on high-risk changes.

``make docs-quality``
   Runs ``scripts/lint_doc_freshness.py`` and builds Sphinx with
   warnings treated as errors. This is required for documentation
   changes and for code changes that alter public behavior.

``make security``
   Runs ``pip-audit`` against the locked environment. It is the local
   dependency-vulnerability gate; it does not replace code review for
   application-layer security.

PHI-Critical Coverage
---------------------

PHI and boundary behavior is covered by dedicated tests across the
normal and security suites:

.. code-block:: text

   tests/test_phi_scrub.py
   tests/test_phi_gate.py
   tests/test_phi_safe_input_gates.py
   tests/test_agent_tools_phi_safe.py
   tests/test_file_access.py
   tests/test_secure_env.py
   tests/test_secure_staging.py
   tests/test_log_hygiene.py
   tests/test_lineage_manifest.py
   tests/test_pdf_phi_flag.py
   tests/test_pipeline_provenance.py
   tests/security/test_adversarial_phi_safe.py
   tests/security/test_kanon_l_diversity.py
   tests/security/test_keystore.py
   tests/security/test_llm_capabilities.py
   tests/security/test_llm_construction_smoke.py
   tests/security/test_log_hygiene_keys.py
   tests/security/test_no_keys_in_parent_environ.py
   tests/security/test_pdf_redaction_pipeline.py
   tests/security/test_phase2_pipeline_polish.py
   tests/security/test_phase2_polish_permissions.py
   tests/security/test_sandbox_isolation.py

The IRB conformance matrix maps each regulated claim to the specific
test or test family that guards it.

Writing Tests
-------------

Use pytest and keep tests close to the behavior they protect.

Naming rules:

* Test files use ``test_<module_or_behavior>.py``.
* Test classes use ``Test<Behavior>`` when grouping scenarios adds
  clarity.
* Test names describe the behavior and edge case, not the implementation
  detail.

Pattern:

.. code-block:: python

   from pathlib import Path

   import pandas as pd

   from scripts.extraction.dataset_pipeline import extract_single_dataset


   def test_extract_single_dataset_rejects_unsupported_suffix(tmp_path: Path) -> None:
       unsupported = tmp_path / "legacy.ods"
       unsupported.write_bytes(b"fake")

       success, count, error = extract_single_dataset(
           unsupported,
           tmp_path / "out",
           "Indo-VAP",
           "2026-04-28T00:00:00+00:00",
       )

       assert success is False
       assert count == 0
       assert error is not None

Prefer real filesystem fixtures for path/zone behavior. Mock only
network calls, LLM clients, time-sensitive surfaces, and hard-to-trigger
error branches.

CI Behavior
-----------

``.github/workflows/ci.yml`` runs Ruff, mypy, and the full pytest suite
on Python 3.11, 3.12, and 3.13 for code-touching pushes and PRs.

``.github/workflows/docs-quality-check.yml`` runs the doc-freshness
linter, builds Sphinx, runs linkcheck, and reports size/version drift for
documentation-touching pushes and PRs.

When a change touches security, PHI boundaries, provider construction, or
the pipeline publish path, include the local verification transcript in
the PR description.
