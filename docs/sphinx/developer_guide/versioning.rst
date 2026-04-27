Versioning
==========

RePORT AI Portal uses `Semantic Versioning <https://semver.org/>`_ with automatic
version bumping based on `Conventional Commits <https://www.conventionalcommits.org/>`_.

.. contents:: On this page
   :local:
   :depth: 2

Version Source of Truth
-----------------------

The canonical version lives in ``__version__.py`` at the repository root:

.. code-block:: python

   __version__: str = "0.16.0"

All other modules import from this single source:

- ``config.py`` — runtime configuration
- ``main.py`` — CLI entry point (``--version``)
- ``scripts/__init__.py`` — package version marker
- ``scripts/utils/__init__.py`` — utilities package
- ``docs/sphinx/conf.py`` — Sphinx documentation build

Automatic Version Bumping
-------------------------

CI Workflow (GitHub Actions)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

On every push to ``main``, the ``auto-version.yml`` workflow inspects the
commit message and bumps the version automatically:

.. list-table::
   :header-rows: 1
   :widths: 40 20 30

   * - Commit Message
     - Bump Type
     - Example
   * - ``fix: ...``
     - Patch
     - 0.14.0 → 0.14.1
   * - ``feat: ...``
     - Minor
     - 0.14.0 → 0.15.0
   * - ``feat!: ...`` or ``BREAKING CHANGE:``
     - Major
     - 0.14.0 → 1.0.0
   * - ``docs:``, ``chore:``, ``ci:``, etc.
     - None
     - 0.14.0 (unchanged)

The workflow updates both ``__version__.py`` and ``config/config.yaml`` and
commits the change back as ``chore: bump version X.Y.Z → A.B.C``.  A guard
prevents infinite loops by skipping runs triggered by version-bump commits.

Local Smart Commit
~~~~~~~~~~~~~~~~~~

For command-line use, the ``smart-commit`` helper performs the same bump
locally before creating the Git commit:

.. code-block:: bash

   ./scripts/utils/smart-commit.sh "feat: add new feature"

The helper reads the commit message, invokes the local ``bump-version``
hook, stages ``__version__.py``, and commits in one step.

Manual Bumping
--------------

Edit ``__version__.py`` directly and commit with ``--no-verify`` to skip
hooks:

.. code-block:: bash

   # Edit __version__.py, then:
   git add __version__.py
   git commit --no-verify -m "chore: bump version to X.Y.Z"

Version Validation
------------------

``__version__.py`` validates the version string at import time.  Only the
strict ``MAJOR.MINOR.PATCH`` format is accepted — no pre-release or build
metadata suffixes.

The smoke test suite (``tests/test_smoke.py::test_version_is_valid_semver``)
additionally asserts that the version parses correctly and that the derived
``__version_info__`` tuple is consistent.

Checking the Current Version
----------------------------

.. code-block:: bash

   make version
   # or
   uv run python main.py --version
