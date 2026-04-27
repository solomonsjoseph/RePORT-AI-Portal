Testing
=======

This guide covers testing practices and procedures for RePORT AI Portal.

Testing Strategy
----------------

Test Pyramid
~~~~~~~~~~~~

RePORT AI Portal follows the test pyramid approach:

.. code-block:: text

                    ▲
                   ╱ ╲
                  ╱ E2E╲           End-to-End Tests
                 ╱Tests╲           (Few, Slow, High Confidence)
                ╱═══════╲
               ╱         ╲
              ╱Integration╲        Integration Tests
             ╱    Tests    ╲       (Moderate, Medium Speed)
            ╱═══════════════╲
           ╱                 ╲
          ╱   Unit Tests      ╲    Unit Tests
         ╱                     ╲   (Many, Fast, Focused)
        ╱═══════════════════════╲
       ▼                         ▼

Test Types
~~~~~~~~~~

**Unit Tests**

* Test individual functions/classes in isolation
* Fast execution (<1s per test)
* Mock external dependencies
* 70-80% of total tests

**Integration Tests**

* Test interaction between components
* Use real dependencies (databases, files)
* Moderate execution time (1-10s per test)
* 15-25% of total tests

**End-to-End Tests**

* Test complete workflows
* Use real data and services
* Slow execution (10s-minutes per test)
* 5-10% of total tests

Test Organization
-----------------

Directory Structure
~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   tests/
   ├── __init__.py
   ├── conftest.py                    # Shared fixtures
   ├── fixtures/                      # Test data
   │   ├── golden/
   │   └── trio_min/
   ├── security/                      # Zone guard and prompt injection tests
   ├── ai_assistant/                           # Agent & CLI tests (planned)
   └── extraction/                    # Extraction pipeline tests (planned)

Naming Conventions
~~~~~~~~~~~~~~~~~~

* Test files: ``test_<module_name>.py``
* Test functions: ``test_<function_name>_<scenario>``
* Test classes: ``Test<ClassName>``

Examples:

.. code-block:: python

   # tests/test_dataset_extraction.py

   def test_extract_excel_success():
       """Test successful PDF extraction."""
       pass

   def test_extract_from_pdf_missing_file():
       """Test extraction with missing file."""
       pass

   class TestPDFExtractor:
       """Tests for PDFExtractor class."""

       def test_init(self):
           """Test PDFExtractor initialization."""
           pass

Writing Tests
-------------

Unit Test Example
~~~~~~~~~~~~~~~~~

.. note::

   The examples below are schematic illustrations of the testing pattern.
   Function names match the real module; see ``tests/`` for runnable tests.

.. code-block:: python

   # tests/test_dataset_pipeline.py

   import pytest
   from unittest.mock import Mock, patch
   from scripts.extraction.dataset_pipeline import clean_record_for_json


   def test_clean_record_drops_none_values():
       """Test that clean_record_for_json drops None fields."""
       record = {"id": "SUBJ_abc123", "age": 45, "notes": None}
       result = clean_record_for_json(record)
       assert "notes" not in result


   def test_clean_record_preserves_valid_fields():
       """Test that clean_record_for_json preserves non-None fields."""
       record = {"id": "SUBJ_abc123", "age": 45}
       result = clean_record_for_json(record)
       assert result["age"] == 45


   @pytest.mark.parametrize("field,value,expected_present", [
       ("age", 45, True),
       ("missing", None, False),
   ])
   def test_clean_record_parametrized(field, value, expected_present):
       """Test clean_record_for_json with multiple field shapes."""
       record = {field: value}
       result = clean_record_for_json(record)
       assert (field in result) == expected_present

Integration Test Example
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # tests/integration/test_pipeline_integration.py

   import pytest
   import pandas as pd
   from scripts.extraction.load_dictionary import load_study_dictionary
   from scripts.extraction.dataset_pipeline import extract_datasets


   @pytest.fixture
   def sample_dictionary(tmp_path):
       """Create a sample data dictionary."""
       dict_path = tmp_path / "dictionary.xlsx"
       # Create dictionary file
       df = pd.DataFrame({
           "field_name": ["patient_id", "age"],
           "field_type": ["string", "integer"],
       })
       df.to_excel(dict_path, index=False)
       return dict_path


   @pytest.fixture
   def sample_data(tmp_path):
       """Create sample data file."""
       data_path = tmp_path / "data.xlsx"
       df = pd.DataFrame({
           "patient_id": ["001", "002"],
           "age": [25, 30],
       })
       df.to_excel(data_path, index=False)
       return data_path


   def test_dictionary_and_extraction_integration(
       sample_dictionary, sample_data
   ):
       """Test integration between dictionary loading and extraction."""
       # Load dictionary
       dictionary = load_study_dictionary(sample_dictionary)

       # Extract data (schematic — real call uses extract_single_dataset)
       # extracted = extract_single_dataset(data_path, dictionary, ...)

       # Verify integration
       assert dictionary is not None

End-to-End Test Example
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # tests/e2e/test_full_pipeline.py

   import pytest
   import os
   from pathlib import Path


   @pytest.mark.e2e
   def test_full_pipeline_execution(tmp_path):
       """Test complete pipeline execution."""
       # Setup test data
       setup_test_environment(tmp_path)

       # Run pipeline
       result = run_full_pipeline(
           dict_path=tmp_path / "dictionary",
           input_path=tmp_path / "input",
           output_path=tmp_path / "output",
       )

       # Verify results
       assert result.success is True
       assert (tmp_path / "output" / "extracted_data.xlsx").exists()

Fixtures and Mocking
--------------------

Using Fixtures
~~~~~~~~~~~~~~

Fixtures provide reusable test data and setup:

.. code-block:: python

   # tests/conftest.py

   import pytest
   import pandas as pd


   @pytest.fixture
   def sample_dataframe():
       """Provide a sample DataFrame for testing."""
       return pd.DataFrame({
           "id": [1, 2, 3],
           "name": ["Alice", "Bob", "Charlie"],
           "age": [25, 30, 35],
       })


   @pytest.fixture
   def temp_directory(tmp_path):
       """Provide a temporary directory."""
       test_dir = tmp_path / "test_data"
       test_dir.mkdir()
       return test_dir

Mocking External Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use mocks to isolate tests from external services:

.. code-block:: python

   from unittest.mock import Mock, patch, MagicMock


   @patch('scripts.extraction.dataset_pipeline.extract_single_dataset')
   def test_extract_with_llm_mock(mock_llm):
       """Test extraction with mocked LLM."""
       # Configure mock
       mock_llm.return_value = {"patient_id": "12345"}

       # Run test
       result = extract_from_pdf("sample.pdf")

       # Verify
       assert result["patient_id"] == "12345"
       mock_llm.assert_called_once()

Test Coverage
-------------

Measuring Coverage
~~~~~~~~~~~~~~~~~~

Run tests with coverage:

.. code-block:: bash

   # Run with coverage
   pytest --cov=scripts --cov-report=html

   # View report
   open htmlcov/index.html

Coverage Goals
~~~~~~~~~~~~~~

* **Overall**: >80% coverage
* **Critical paths**: 100% coverage (extraction, validation)
* **Utility functions**: >90% coverage
* **UI/CLI code**: >60% coverage

Continuous Integration
----------------------

GitHub Actions Workflow
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # .github/workflows/tests.yml

   name: Tests

   on: [push, pull_request]

   jobs:
     test:
       runs-on: ubuntu-latest

       steps:
       - uses: actions/checkout@v4

       - name: Install uv
         uses: astral-sh/setup-uv@v4

       - name: Set up Python
         run: uv python install 3.13

       - name: Install dependencies
         run: uv sync --all-groups

       - name: Run tests
         run: uv run pytest --cov=scripts --cov-report=xml

       - name: Upload coverage
         uses: codecov/codecov-action@v4

Pre-commit Hooks
~~~~~~~~~~~~~~~~

Set up pre-commit hooks:

.. code-block:: yaml

   # .pre-commit-config.yaml

   repos:
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.9.0
       hooks:
         - id: ruff
           args: [--fix]
         - id: ruff-format

     - repo: local
       hooks:
         - id: pytest
           name: pytest
           entry: uv run pytest tests/ -x -q
           language: system
           pass_filenames: false
           always_run: true

Best Practices
--------------

Test Independence
~~~~~~~~~~~~~~~~~

* Each test should run independently
* No shared state between tests
* Use fixtures for setup/teardown

.. code-block:: python

   # Bad: Tests depend on execution order
   def test_create_user():
       user = create_user("alice")
       assert user.name == "alice"

   def test_get_user():
       user = get_user("alice")  # Assumes previous test ran
       assert user is not None

   # Good: Tests are independent
   @pytest.fixture
   def created_user():
       user = create_user("alice")
       yield user
       delete_user("alice")

   def test_create_user(created_user):
       assert created_user.name == "alice"

   def test_get_user(created_user):
       user = get_user("alice")
       assert user is not None

Clear Test Names
~~~~~~~~~~~~~~~~

Test names should describe what they test:

.. code-block:: python

   # Bad: Unclear what's being tested
   def test_1():
       pass

   def test_extract():
       pass

   # Good: Clear description
   def test_extract_patient_id_from_valid_pdf():
       pass

   def test_extract_returns_none_when_field_missing():
       pass

Arrange-Act-Assert Pattern
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Structure tests clearly:

.. code-block:: python

   def test_process_data_with_valid_input():
       # Arrange
       input_data = pd.DataFrame({"col": [1, 2, 3]})
       expected_output = pd.DataFrame({"col": [2, 4, 6]})

       # Act
       result = process_data(input_data)

       # Assert
       pd.testing.assert_frame_equal(result, expected_output)

Test Data Management
--------------------

Using Test Fixtures
~~~~~~~~~~~~~~~~~~~

Store test data in ``tests/fixtures/``:

.. code-block:: text

   tests/fixtures/
   ├── pdfs/
   │   ├── sample_form.pdf
   │   └── invalid_form.pdf
   ├── excel/
   │   ├── valid_data.xlsx
   │   └── invalid_data.xlsx
   └── dictionaries/
       └── sample_dict.xlsx

Loading Fixtures
~~~~~~~~~~~~~~~~

.. code-block:: python

   import pytest
   from pathlib import Path

   FIXTURES_DIR = Path(__file__).parent / "fixtures"


   @pytest.fixture
   def sample_pdf():
       """Load sample PDF fixture."""
       return FIXTURES_DIR / "pdfs" / "sample_form.pdf"


   def test_extract_from_sample_pdf(sample_pdf):
       """Test extraction with sample PDF."""
       result = extract_from_pdf(sample_pdf)
       assert result is not None

Running Tests
-------------

Basic Test Execution
~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Run all tests
   pytest

   # Run with verbose output
   pytest -v

   # Run specific test file
   pytest tests/test_dataset_extraction.py

   # Run specific test
   pytest tests/test_dataset_extraction.py::test_extract_excel_success

   # Run tests matching pattern
   pytest -k "extract"

Test Markers
~~~~~~~~~~~~

Use markers to categorize tests:

.. code-block:: python

   import pytest

   @pytest.mark.slow
   def test_large_dataset_processing():
       """Slow test processing large dataset."""
       pass

   @pytest.mark.integration
   def test_database_integration():
       """Integration test with database."""
       pass

   @pytest.mark.e2e
   def test_full_pipeline():
       """End-to-end pipeline test."""
       pass

Run tests by marker:

.. code-block:: bash

   # Run only fast tests (skip slow ones)
   pytest -m "not slow"

   # Run only integration tests
   pytest -m integration

   # Run e2e tests
   pytest -m e2e

Debugging Failed Tests
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Stop on first failure
   pytest -x

   # Enter debugger on failure
   pytest --pdb

   # Show local variables on failure
   pytest -l

   # Increase verbosity
   pytest -vv

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Import Errors**

Ensure PYTHONPATH is set correctly:

.. code-block:: bash

   export PYTHONPATH="${PYTHONPATH}:$(pwd)"

**Fixture Not Found**

Check ``conftest.py`` is in the correct location and contains the fixture.

**Flaky Tests**

* Check for race conditions
* Ensure proper cleanup
* Add retries for external services

Performance Testing
-------------------

Benchmarking
~~~~~~~~~~~~

Use ``pytest-benchmark`` for performance tests:

.. code-block:: python

   def test_extraction_performance(benchmark):
       """Benchmark extraction performance."""
       result = benchmark(extract_from_pdf, "sample.pdf")
       assert result is not None

Load Testing
~~~~~~~~~~~~

Test with large datasets:

.. code-block:: python

   @pytest.mark.slow
   def test_large_batch_processing():
       """Test processing 1000 records."""
       data = generate_test_records(1000)
       result = process_batch(data)
       assert len(result) == 1000

Next Steps
----------

* Review :doc:`contributing` for development workflow
* See :doc:`api_reference` for detailed API documentation
* Check :doc:`architecture` for system design
