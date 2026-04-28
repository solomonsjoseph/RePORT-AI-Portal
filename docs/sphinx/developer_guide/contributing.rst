Contributing
============

This repository is privacy-sensitive clinical-research software. Keep
changes small, reviewed, documented, and verifiable.

Branch And PR Workflow
----------------------

All changes go through a branch and pull request.

.. code-block:: bash

   git checkout main
   git pull
   git checkout -b docs/short-description

Use these branch prefixes:

* ``feat/`` for new behavior
* ``fix/`` for bug fixes
* ``docs/`` for documentation-only changes
* ``chore/`` for maintenance

Do not push directly to ``main``. Merge through GitHub after review.

Attribution
-----------

Project metadata names Solomon S Joseph as author and maintainer. Do not
replace that attribution with a generic team label.

Do not add AI ``Co-Authored-By`` trailers to commits, amends, squashes,
or PR descriptions. Add a ``Co-Authored-By`` trailer only when a real
external human contributor is explicitly named for that specific commit.

Local Setup
-----------

.. code-block:: bash

   uv sync --all-groups
   make verify

The project targets Python 3.11+ and uses ``uv`` for dependency
management.

Before Opening A PR
-------------------

Run the gates that match the change:

.. code-block:: bash

   make verify        # fast local readiness gate
   make test-all      # full pytest suite
   make docs-quality  # required for docs or public behavior changes
   make security      # dependency vulnerability scan

For a docs-only change, ``make docs-quality`` plus ``make verify`` is the
minimum useful check. For any PHI, security, provider, pipeline, or agent
tool change, run ``make test-all`` as well.

Coding Rules
------------

* Prefer the existing module boundaries and helper APIs.
* Keep path handling behind the zone guards:
  ``scripts.security.secure_env`` for pipeline boundaries and
  ``scripts.ai_assistant.file_access`` for agent file access.
* Keep PHI-returning or user-facing agent output behind
  ``scripts.ai_assistant.phi_safe``.
* Add tests when behavior changes.
* Update docs when operator behavior, PHI handling, configuration, or
  verification claims change.
* Use Ruff for linting and formatting; do not introduce Black, Flake8, or
  isort configuration.

Documentation Rules
-------------------

Documentation is part of the product, not a release-note graveyard.
Follow :doc:`documentation_style` for page structure, reader profiles,
and language rules.

* Current-facing pages should describe the current behavior, not the PR
  that introduced it.
* Avoid hard-coded test counts unless the count is generated or checked.
* Avoid line-number references to code; they drift quickly.
* Keep IRB-facing claims tied to artifacts and tests.
* Run ``make docs-quality`` before committing documentation changes.

Adding A PHI Rule
-----------------

1. Add the rule to ``scripts/security/phi_scrub.yaml`` under the correct
   action class.
2. Add or update coverage in ``tests/test_phi_scrub.py``.
3. Run ``make test-all``.
4. Update the PHI architecture or IRB dossier if the public handling
   story changed.

Adding An Agent Tool
--------------------

1. Register the tool in ``scripts/ai_assistant/agent_tools.ALL_TOOLS``.
2. Validate all file reads with ``validate_agent_read``.
3. Validate all writes with ``validate_agent_write`` or the narrower
   sandbox validator.
4. Wrap returns with ``@phi_safe_return`` or call the appropriate PHI
   and k-anonymity helpers directly.
5. Add tests in the existing top-level test modules or a focused new
   ``tests/test_*.py`` file.
6. Update user/developer docs and run ``make docs-quality``.

Adding Provider Support
-----------------------

Provider support has multiple surfaces:

* ``config.py`` provider inference
* ``scripts/ai_assistant/keystore.py`` provider-key mapping
* ``scripts/ai_assistant/agent_graph.py`` construction
* ``scripts/ai_assistant/ui/providers.py`` UI catalog
* ``scripts/ai_assistant/cli.py`` CLI catalog
* provider smoke tests
* configuration docs

Keep those surfaces in sync. Unknown remote providers should fail
closed.

Review Focus
------------

Reviewers should prioritize:

* PHI boundary regressions
* raw-data or audit-data exposure to the agent
* provider-key leakage
* path traversal or symlink bypasses
* stale documentation claims
* missing tests for changed behavior

General style comments are secondary to correctness, privacy, and
operator clarity.
