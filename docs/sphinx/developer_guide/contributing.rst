Contributing
============

We welcome contributions to RePORT AI Portal! This guide will help you get started.

Getting Started
---------------

Setting Up Your Development Environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Fork the repository** on GitHub

2. **Clone your fork:**

   .. code-block:: bash

      git clone https://github.com/your-username/RePORT-AI-Portal.git
      cd RePORT-AI-Portal

3. **Add the upstream remote:**

   .. code-block:: bash

      git remote add upstream https://github.com/solomonsjoseph/RePORT-AI-Portal.git

4. **Install uv (if not already installed):**

   .. code-block:: bash

      curl -LsSf https://astral.sh/uv/install.sh | sh

5. **Install development dependencies:**

   .. code-block:: bash

      uv sync --all-groups    # Installs all dependencies including dev and docs

6. **Verify installation:**

   .. code-block:: bash

      uv run python -c "import scripts; print('Setup successful!')"
      uv run pytest --version

Development Workflow
--------------------

Creating a Feature Branch
~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Update your main branch:**

   .. code-block:: bash

      git checkout main
      git pull upstream main

2. **Create a feature branch:**

   .. code-block:: bash

      git checkout -b feature/your-feature-name

   Use prefixes:

   * ``feature/`` - New features
   * ``bugfix/`` - Bug fixes
   * ``docs/`` - Documentation changes
   * ``refactor/`` - Code refactoring
   * ``test/`` - Test additions/changes

Making Changes
~~~~~~~~~~~~~~

1. **Write your code** following the :ref:`coding-standards`

2. **Add tests** for new functionality

3. **Update documentation** as needed

4. **Run tests:**

   .. code-block:: bash

      make test          # or: uv run pytest tests/

5. **Check code quality:**

   .. code-block:: bash

      make lint          # or: uv run ruff check .
      make typecheck     # or: uv run mypy scripts/

6. **Check doc freshness** (if you touched docs, ``ALL_TOOLS``,
   ``__version__.py``, or ``phi_scrub.yaml``):

   .. code-block:: bash

      make doc-freshness  # or: uv run --frozen python scripts/lint_doc_freshness.py

   The linter compares live source-of-truth values (tool count, version,
   scrub-action count) against prose in ``README.md`` /
   ``AGENTS.md`` / ``docs/sphinx/`` / ``docs/irb_dossier/`` and
   rejects forbidden phrases that indicate retired architecture (see
   ``FORBIDDEN`` in ``scripts/lint_doc_freshness.py`` for the full
   pattern catalog and the canonical replacement guidance). It is
   wired into ``.github/workflows/docs-quality-check.yml`` and runs on
   every PR that touches docs or canonical source files.

7. **Run all quality checks at once:**

   .. code-block:: bash

      make ci            # lint + typecheck + test

Committing Changes
~~~~~~~~~~~~~~~~~~

1. **Stage your changes:**

   .. code-block:: bash

      git add .

2. **Commit with a descriptive message:**

   .. code-block:: bash

      git commit -m "Add feature: brief description

      - Detailed change 1
      - Detailed change 2

      Closes #123"

   Follow commit message conventions:

   * First line: Brief summary (<50 chars)
   * Blank line
   * Detailed description
   * Reference issues/PRs

3. **Push to your fork:**

   .. code-block:: bash

      git push origin feature/your-feature-name

Submitting a Pull Request
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Go to GitHub** and create a pull request from your fork to the main repository

2. **Fill out the PR template:**

   * Description of changes
   * Related issues
   * Testing performed
   * Screenshots (if applicable)

3. **Wait for review** and address any feedback

4. **Ensure CI passes** (tests, linting, docs)

.. _coding-standards:

Coding Standards
----------------

Python Style Guide
~~~~~~~~~~~~~~~~~~

Follow **PEP 8** with these specifics:

* Line length: 100 characters (configured in ``pyproject.toml``)
* Indentation: 4 spaces
* Linting and formatting: ``ruff`` (not black/flake8/isort)
* Quotes: Double quotes for strings

Example:

.. code-block:: python

   """Module docstring.

   This module does XYZ.
   """

   from __future__ import annotations

   import pandas as pd

   from scripts.utils import helper_function


   def process_data(
       data: pd.DataFrame,
       config: dict[str, str],
       validate: bool = True,
   ) -> pd.DataFrame:
       """Process the input data.

       Args:
           data: Input DataFrame to process.
           config: Configuration dictionary.
           validate: Whether to validate results.

       Returns:
           Processed DataFrame.

       Raises:
           ValueError: If data is empty.

       Example:
           >>> df = pd.DataFrame({"col1": [1, 2, 3]})
           >>> result = process_data(df, {"key": "value"})
           >>> len(result)
           3
       """
       if data.empty:
           raise ValueError("Data cannot be empty")

       # Processing logic
       result = data.copy()

       if validate:
           _validate_result(result)

       return result

Documentation Standards
~~~~~~~~~~~~~~~~~~~~~~~

**All public functions, classes, and modules must have Google-style docstrings:**

.. code-block:: python

   def function_name(param1: str, param2: int = 10) -> bool:
       """One-line summary.

       Detailed description of what the function does.
       Can span multiple lines.

       Args:
           param1: Description of param1.
           param2: Description of param2. Defaults to 10.

       Returns:
           Description of return value.

       Raises:
           ValueError: When param1 is empty.
           TypeError: When param2 is not an integer.

       Example:
           >>> result = function_name("test", 20)
           >>> print(result)
           True

       Note:
           Additional notes about usage, edge cases, etc.
       """

Type Hints
~~~~~~~~~~

Use type hints for all function signatures:

.. code-block:: python

   from __future__ import annotations

   def process_records(
       records: list[dict[str, str]],
       max_count: int | None = None,
   ) -> pd.DataFrame | None:
       """Process a list of records."""
       ...

Testing Standards
-----------------

Writing Tests
~~~~~~~~~~~~~

* **Location**: Tests in ``tests/`` directory mirror ``scripts/`` structure
* **Framework**: Use ``pytest``
* **Coverage**: Aim for >80% code coverage
* **Types**: Write unit tests, integration tests, and end-to-end tests

Example Test:

.. code-block:: python

   # tests/test_dataset_pipeline.py

   import pytest
   from scripts.extraction.dataset_pipeline import extract_single_dataset


   def test_extract_single_dataset_success(tmp_path):
       """Test successful dataset extraction."""
       xlsx = Path("tests/fixtures/trio_min/datasets/test_enrollment.xlsx")
       success, count, err = extract_single_dataset(
           xlsx, tmp_path, "test_study", "2024-01-01T00:00:00+00:00"
       )

       assert success
       assert count > 0
       assert err is None


   def test_extract_from_pdf_invalid_file():
       """Test extraction with invalid file."""
       with pytest.raises(FileNotFoundError):
           extract_from_pdf("nonexistent.pdf")


   @pytest.mark.parametrize("pdf_path,expected_fields", [
       ("tests/fixtures/form1.pdf", ["name", "age"]),
       ("tests/fixtures/form2.pdf", ["id", "date"]),
   ])
   def test_extract_from_pdf_parametrized(pdf_path, expected_fields):
       """Test extraction with multiple inputs."""
       result = extract_from_pdf(pdf_path)

       for field in expected_fields:
           assert field in result

Running Tests
~~~~~~~~~~~~~

.. code-block:: bash

   # Run all tests
   make test
   # Or directly:
   uv run pytest tests/

   # Run with coverage
   uv run pytest --cov=scripts --cov-report=html

   # Run specific test file
   uv run pytest tests/test_dataset_extraction.py

   # Run specific test
   uv run pytest tests/test_dataset_extraction.py::test_extract_excel_success

   # Run with verbose output
   uv run pytest -v

Documentation Standards
-----------------------

Updating Documentation
~~~~~~~~~~~~~~~~~~~~~~

When adding features:

1. **Update docstrings** in the code
2. **Update user guide** if user-facing
3. **Update API reference** if adding public APIs
4. **Update architecture docs** if changing structure

Building Documentation Locally
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Build HTML docs (from repo root)
   make docs

   # Or from the Sphinx directory
   cd docs/sphinx && make html

   # View docs
   open docs/sphinx/_build/html/index.html  # macOS

Documentation Guidelines
~~~~~~~~~~~~~~~~~~~~~~~~

* Use reStructuredText (.rst) for docs
* Include code examples
* Add cross-references with ``:doc:``, ``:ref:``, etc.
* Keep examples up-to-date with code

Code Review Process
-------------------

Reviewer Responsibilities
~~~~~~~~~~~~~~~~~~~~~~~~~

Reviewers should check:

* **Correctness**: Does the code work as intended?
* **Tests**: Are there adequate tests?
* **Documentation**: Is the code documented?
* **Style**: Does it follow coding standards?
* **Performance**: Are there efficiency concerns?
* **Security**: Are there security implications?

Author Responsibilities
~~~~~~~~~~~~~~~~~~~~~~~

Authors should:

* Respond promptly to feedback
* Make requested changes
* Explain design decisions
* Keep PRs focused and reasonably sized

Best Practices
--------------

Code Quality
~~~~~~~~~~~~

* **DRY** (Don't Repeat Yourself): Avoid code duplication
* **KISS** (Keep It Simple, Stupid): Prefer simple solutions
* **YAGNI** (You Aren't Gonna Need It): Don't add unused features
* **Single Responsibility**: Each function/class has one job

Git Practices
~~~~~~~~~~~~~

* **Atomic commits**: Each commit does one thing
* **Meaningful messages**: Explain what and why
* **Small PRs**: Easier to review (<400 lines preferred)
* **Rebase before merge**: Keep history clean

Performance Considerations
~~~~~~~~~~~~~~~~~~~~~~~~~~

* Profile before optimizing
* Use appropriate data structures
* Cache expensive operations
* Consider memory usage for large datasets

Security Practices
~~~~~~~~~~~~~~~~~~

* Never commit API keys or secrets
* Use environment variables for sensitive data
* Validate all user inputs
* Follow least privilege principle

Common Tasks
------------

Adding a New Pipeline Stage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Create module in ``scripts/``
2. Add configuration to ``config.py``
3. Update ``main.py`` to call the stage
4. Add tests in ``tests/``
5. Document in user guide
6. Update architecture docs

Adding LLM Provider Support
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

LLM providers are configured via ``init_chat_model()`` from LangChain.
To add a new provider:

1. Install the provider's LangChain integration package
2. Add the provider name to ``config.py`` (``LLM_PROVIDER``)
3. Update ``config/config.yaml`` with any provider-specific settings
4. Add integration tests
5. Document in configuration guide

Issue Tracking
--------------

Finding Issues to Work On
~~~~~~~~~~~~~~~~~~~~~~~~~

Look for issues labeled:

* ``good first issue``: Beginner-friendly
* ``help wanted``: Contributions welcome
* ``bug``: Bug fixes needed
* ``enhancement``: New features

Creating Issues
~~~~~~~~~~~~~~~

When creating an issue:

1. **Search first**: Check if it already exists
2. **Use templates**: Follow the issue template
3. **Be specific**: Provide details and examples
4. **Add labels**: Help with organization

Communication
-------------

Channels
~~~~~~~~

* **GitHub Issues**: Bug reports, feature requests
* **Pull Requests**: Code discussions
* **Discussions**: General questions
* **Email**: For sensitive topics

Code of Conduct
~~~~~~~~~~~~~~~

* Be respectful and inclusive
* Provide constructive feedback
* Assume good intentions
* Focus on what is best for the project

Questions?
----------

If you have questions:

* Check :doc:`../user_guide/faq`
* Search existing issues
* Ask in GitHub Discussions
* Email the maintainers

Thank you for contributing to RePORT AI Portal!
