Frequently Asked Questions
===========================

General Questions
-----------------

What is RePORT AI Portal?
~~~~~~~~~~~~~~~~~~~~~~~~~

RePORT AI Portal is a **single-study, privacy-first, local-first AI Assistant system** for one
already-existing clinical research study. It processes one fixed study under
``data/raw/{STUDY_NAME}/`` and provides extraction, structured tool-based
querying, and grounded Q&A. PHI is scrubbed **inside the pipeline** at Step 1.6
on AMBER-staged JSONL (eight-action catalog, rule + allowlist) — operators
do **not** need to pre-scrub raw data; raw inputs flow through the
honest-broker boundary unchanged and are never read by the LLM agent. The
user provides the LLM; the system provides study-specific AI Assistant context.

Who should use RePORT AI Portal?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Clinical research teams analysing one study's data via grounded AI Assistant
* Data managers requiring auditable clinical datasets
* Researchers needing privacy-compliant, citation-backed answers
* Organizations needing regulatory-compliant data processing

What data formats does it support?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Input**: PDF (annotated case report forms), Excel ``.xlsx``, CSV
* **Output**: JSONL (JSON Lines)

Installation & Setup
--------------------

What are the system requirements?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Python 3.11 or higher (3.13 recommended)
* 8GB RAM minimum (16GB recommended)
* 2GB disk space for application and dependencies
* macOS, Linux, or Windows operating system

How do I install RePORT AI Portal?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Install uv (if not already installed)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Clone and setup
   git clone https://github.com/solomonsjoseph/RePORT-AI-Portal.git
   cd RePORT-AI-Portal
   make quickstart    # Syncs dependencies and launches the app

Or manually:

.. code-block:: bash

   uv sync            # Install dependencies
   make pipeline      # Run the data processing pipeline

See :doc:`installation` for detailed instructions.

Do I need an API key?
~~~~~~~~~~~~~~~~~~~~~

It depends on the LLM provider you choose.

* **Local Ollama** (the recommended PHI-safe option) — no API key required. Ollama
  runs entirely on your machine; nothing is sent to an external service. Set
  ``LLM_PROVIDER=ollama`` and start Ollama before running the pipeline.
* **Hosted providers** (Anthropic, OpenAI, Google) — yes, an API key is required.
  Add the key to your environment or ``.env`` file:

  .. code-block:: bash

     ANTHROPIC_API_KEY=sk-ant-...   # for Anthropic
     OPENAI_API_KEY=sk-...          # for OpenAI
     GOOGLE_API_KEY=...             # for Google

  Note: using a hosted provider for PDF extraction also requires setting
  ``REPORTALIN_PDF_PHI_FREE=1`` — see :doc:`configuration`.

  .. note::

     The Streamlit wizard's step 1 routes the pasted key into an
     in-memory ``KeyStore`` (``scripts/ai_assistant/keystore.py``,
     the KeyStore) and scrubs the corresponding ``*_API_KEY`` from
     ``os.environ`` for the lifetime of the app. Keys are re-injected
     only into the short-lived pipeline subprocess via
     ``KeyStore.env_for_subprocess``. CLI users invoking ``main.py``
     directly use the env var path normally.

Using the Pipeline
------------------

How do I run the pipeline?
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   make pipeline

Or run individual legs independently:

.. code-block:: bash

   make dictionary         # data-dictionary extraction leg
   make extract-datasets   # dataset extraction leg
   make pdf-extract        # PDF variable extraction leg
   make bundle             # PHI scrub → cleanup → publish (trio bundle)

Can I run only part of the pipeline?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. Each extraction leg can be run independently. Use the Makefile:

.. code-block:: bash

   make dictionary        # data-dictionary extraction leg only
   make extract-datasets  # dataset extraction leg only
   make pipeline          # full pipeline (all legs → scrub → publish)

How long does processing take?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Processing time depends on:

* Number of documents (PDF pages or Excel rows)
* LLM provider and model speed
* System resources

Typical processing rates:

* Dictionary loading: <1 minute for most studies
* Data extraction: 1-5 seconds per PDF page

Data Extraction
---------------

What types of data can be extracted?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Structured form fields (text, numbers, dates)
* Checkboxes and radio buttons
* Tables and grids
* Free-text responses

How accurate is the extraction?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The pipeline uses ``pdfplumber`` for text-layer extraction from annotated CRF PDFs
(no LLM is involved in the dataset or dictionary extraction legs). Accuracy is
document-dependent:

* **Searchable PDFs** with consistent annotation markup — high fidelity for
  structured fields; variable quality for free-text narrative cells (which the
  pipeline drops as PHI rather than attempting NER).
* **Image-only / scanned PDFs** — not supported; the pipeline requires a
  searchable text layer.

The dataset extraction leg reads ``.xlsx`` / ``.csv`` directly — no OCR involved.

Can I validate extraction results?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes! The pipeline includes:

* Automatic validation against data dictionary
* Manual review reports
* Error logs for problematic records

.. code-block:: python

   from scripts.extraction.dataset_pipeline import extract_datasets

   result = extract_datasets()
   print(f"Extracted {result['total_records']} records")

Privacy Expectations
--------------------

Does the runtime scrub PHI?
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. The PHI scrubber runs as Step 1.6 of the pipeline over the AMBER staging
workspace before any audit output is written. It applies eight action classes
in strict priority order — keep / birthdate / drop / cap / generalize /
suppress_small_cell / date jitter / id pseudonymize — against ~200
Indo-VAP-calibrated rules in ``scripts/security/phi_scrub.yaml``. A further
defence-in-depth PHI gate + k-anonymity check runs on every LLM tool return.
See :doc:`data_pipeline` for the full flow and ``docs/irb_dossier/
conformance_matrix.md`` for the active IRB conformance matrix.

How do I know this actually works? How do I trust it?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Four places you can inspect the evidence without trusting any claim:

1. **``output/{STUDY}/audit/phi_scrub_report.json``** — counts of every
   scrub action per field per file. If this is empty or missing,
   the scrub did not run.
2. **``output/{STUDY}/audit/lineage_manifest.json``** — SHA-256 of every
   raw input paired with SHA-256 of every published trio artifact.
   Regenerating the pipeline should reproduce the same trio hashes for
   the same raw hashes + same HMAC key.
3. **``docs/irb_dossier/conformance_matrix.md``** — 31 testable claims
   ("direct identifiers dropped", "ages ≥ 90 capped", etc.) each with
   the pytest case that fails in CI if the claim regresses.
4. **``make test`` / ``make test-all``** — deterministic and full-suite
   pytest gates, including catalog-coverage tests that run against the
   shipped ``phi_scrub.yaml``. A rule deletion or pattern regression
   breaks CI immediately.

What exactly counts as PHI in this pipeline? What is NOT in scope?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**In scope** — structural-field transformations only. The scrubber reads
JSONL rows and rewrites the values of fields whose *column names* match
the catalog. Eight action classes cover the HIPAA §164.514(b)(2)(i)
identifier list plus India-specific government IDs (Aadhaar / ABHA / PAN
/ voter / passport / DL / ration / PM-JAY / Nikshay) plus
quasi-identifiers at the agent-boundary k-anonymity gate.

**Deliberately NOT in scope today:**

* Free-text narrative PHI — fields like ``*_SPECIFY``, ``*_COMMENT``,
  ``*_REMARK`` are dropped wholesale rather than having their contents
  NER-parsed. The Stage-5 local-Ollama NER sweep
  (:mod:`scripts.security.phi_ner`) is a design stub pending calibration.
* DICOM metadata scrubbing — if CXR images are attached, the pipeline
  does not strip DICOM ``PatientName`` / ``PatientID`` tags. That belongs
  to a separate imaging pipeline.
* Re-identification against external datasets — the k-anonymity gate
  defends against re-identification from quasi-identifiers *within* the
  study. Cross-linkage against voter rolls / ABDM / commercial
  aggregators is outside our threat model.

What if I find raw PHI in the output?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is a breach scenario. Treat every incident as reportable under RePORT
India Common Protocol + local IRB timelines (typically 72 hours).

1. **Stop.** Do not publish, share, or further process the affected trio
   bundle.
2. **Preserve evidence.** Save the file path, the row index, and the
   offending value (redact in your report; do not paste the raw value
   into email).
3. **Classify.** Was it a direct identifier the scrub should have caught?
   Or a narrative-field residual (Stage-5 territory)? Check
   ``phi_scrub.yaml`` for a matching rule; if none, file a rule-catalog
   gap.
4. **Report.** Notify the study PI, the data manager, and the IRB per the
   breach-response runbook (``docs/irb_dossier/breach_response_runbook.md``
   — study-team-owned stub).
5. **Remediate.** Add the missing rule to ``phi_scrub.yaml``, re-run the
   pipeline, verify the audit report no longer contains a residual.
6. **Rotate the HMAC key** if the breach suggests subject-id
   compromise. Rotation invalidates every prior pseudonym and requires
   full re-ingestion.

Does this guarantee HIPAA Safe Harbor compliance?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Architecturally yes, operationally it depends on your data.** The
scrubber implements the HIPAA §164.514(b)(2)(i) identifier list
(categories A–R) plus DPDPA / SPDI / Aadhaar Act / ICMR overlays. See
``docs/irb_dossier/conformance_matrix.md`` Pillar 1 for the per-category
mapping with test evidence.

What the architecture *cannot* guarantee on its own:

* That your study data dictionary uses column names the catalog
  recognises. Indo-VAP is calibrated in. A new study should sample
  column names against the catalog before first run.
* That your annotated PDFs are PHI-free before you set
  ``REPORTALIN_PDF_PHI_FREE=1``. That flag is your signed assertion; the
  system refuses to help you leak PHI but cannot verify your assertion.

Can the LLM see my raw study data?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

No. Two hard invariants:

1. The LLM agent reads only from ``output/{STUDY}/trio_bundle/`` (the
   GREEN zone of scrubbed artifacts) and ``output/{STUDY}/agent/`` (its
   own analysis outputs, conversations, and snapshots). Every
   ``@tool``-decorated function in
   :mod:`scripts.ai_assistant.agent_tools` resolves every path through
   :func:`scripts.ai_assistant.file_access.validate_agent_read` (the
   unified agent-zone chokepoint) before any file I/O. A tool that
   tries to read from ``data/raw/``, ``audit/``, ``tmp/{STUDY}/`` or any
   other zone raises
   :class:`scripts.security.secure_env.ZoneViolationError`
   (a ``PermissionError`` subclass).
2. Every tool return string passes through
   :func:`scripts.security.phi_gate.phi_gate_check` before the response
   reaches the model. Blocking patterns (Aadhaar / PAN / email / etc.)
   replace the response with a redaction message rather than leaking.

If you use an external LLM API (Anthropic / Google), the tool *inputs* to
the model are cleaned via the same gate, so prompts + tool calls +
outputs all stay PHI-free.

What about the PDFs? They came from a data manager as annotated CRFs.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The pipeline treats ``data/raw/{STUDY}/annotated_pdfs/`` as **PHI-bearing
by default**. External-API PDF extraction is refused unless the operator
explicitly sets ``REPORTALIN_PDF_PHI_FREE=1``. Two safe alternatives:

* Use ``--pdf-source <path>`` with pre-extracted JSON files (the pipeline
  copies them into the staging bundle without any LLM call).
* Skip the PDF leg entirely — the pipeline succeeds without it; the
  trio bundle simply omits ``pdfs/``.

How the Agent Reads the Bundle (No Vector DB)
---------------------------------------------

How does the agent access study data?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ReAct agent uses 12 structured-data tools to query the trio bundle directly
(see :data:`scripts.ai_assistant.agent_tools.ALL_TOOLS`). There is no chunking,
embedding, or vector index. Tools include variable search, form listing, dataset
querying, descriptive statistics, cross-referencing, study-level analysis,
PDF-context search, and a sandboxed Python runner.

.. code-block:: bash

   # Start the interactive chat (web UI with setup wizard)
   make chat

   # Or use the CLI REPL directly
   make chat-cli

How do I add a custom tool?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a new ``@tool``-decorated function in ``scripts/ai_assistant/agent_tools.py`` and
register it in the tool list in ``scripts/ai_assistant/agent_graph.py``. Ensure the tool
resolves every file path through
:func:`scripts.ai_assistant.file_access.validate_agent_read` (for reads)
or :func:`scripts.ai_assistant.file_access.validate_agent_write` (for
writes) and wraps the return string with ``@phi_safe_return``.

Troubleshooting
---------------

The pipeline fails with "API key not found"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add your API key to the `.env` file:

.. code-block:: bash

   OPENAI_API_KEY=your_key_here

Extraction is very slow
~~~~~~~~~~~~~~~~~~~~~~~~

The dataset and dictionary extraction legs use ``pandas`` / ``openpyxl`` — they are
I/O-bound, not LLM-bound, and should finish in seconds to minutes. If they are slow:

* Check that your Excel files are not password-protected or corrupted.
* The step cache means unchanged inputs are skipped on re-runs; ``make clean``
  forces a full re-run if the cache appears stale.

For PDF extraction slowness, the Ollama-backed leg calls a local model; ensure the
Ollama server has adequate GPU/CPU headroom. The agent will automatically step down
the ``qwen3:8b → qwen3:4b → qwen3:1.7b`` ladder if memory is tight.

I'm getting validation errors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check the pipeline audit reports for the source of the error:

.. code-block:: bash

   jq '.' output/Indo-VAP/audit/dataset_cleanup_report.json
   jq '.' output/Indo-VAP/audit/phi_scrub_report.json

Common causes:

* Missing or misnamed ``subject_id`` column — rows without it land in the
  quarantine directory (``tmp/{STUDY}/quarantine/``).
* Data dictionary field definitions missing for columns the pipeline expects.
* Source file encoding issues (use UTF-8 or Latin-1; other encodings may corrupt field names).

Memory errors during processing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Try:

* Use a smaller Ollama model: the agent's qwen3 downgrade ladder steps down
  automatically on Ollama OOM errors (``qwen3:8b → qwen3:4b → qwen3:1.7b``).
* Ensure you have at least 8 GB RAM (16 GB recommended) — see :doc:`installation`.
* Close other memory-intensive applications before running the pipeline.

Development & Contributing
--------------------------

How can I contribute?
~~~~~~~~~~~~~~~~~~~~~

See :doc:`../developer_guide/contributing` for contribution guidelines.

How do I report bugs?
~~~~~~~~~~~~~~~~~~~~~~

Open an issue on GitHub with:

* Detailed description
* Steps to reproduce
* Error messages and logs
* System information

Where can I get help?
~~~~~~~~~~~~~~~~~~~~~

* **GitHub Issues**: https://github.com/solomonsjoseph/RePORT-AI-Portal/issues

Performance & Scaling
---------------------

Can it handle large datasets?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes! The pipeline is designed for:

* Hundreds of PDF forms
* Thousands of Excel records
* Multi-gigabyte databases

Use batch processing and parallel execution for large datasets.

Can I run it on a server?
~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes! RePORT AI Portal can run:

* On local machines
* On remote servers (via SSH)
* In Docker containers
* On cloud platforms (AWS, GCP, Azure)

Is there a GUI?
~~~~~~~~~~~~~~~

Yes. RePORT AI Portal ships both interfaces:

* **Streamlit web UI** — ``make chat`` (or ``python main.py --web``). Provides a guided setup wizard, conversation history, and interactive charts.
* **CLI** — ``make chat-cli`` (or ``python main.py --chat``). Useful for scripted or headless use.

See :doc:`quickstart` for step-by-step instructions.

Licensing & Usage
-----------------

What license is RePORT AI Portal under?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check the LICENSE file in the repository for licensing information.

Can I use it for commercial purposes?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check the specific license terms. Contact the maintainers for commercial licensing inquiries.

Can I modify the code?
~~~~~~~~~~~~~~~~~~~~~~~

Yes, if permitted by the license. Contributions back to the project are welcome!

Next Steps
----------

* Read the :doc:`quickstart` guide
* Explore :doc:`data_pipeline` for detailed pipeline documentation
* Check :doc:`configuration` for all configuration options
