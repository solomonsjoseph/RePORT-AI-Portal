Data Pipeline
=============

This page explains what happens when a user loads a study. It avoids
implementation detail; developers should use
:doc:`../developer_guide/architecture` and
:doc:`../developer_guide/operations`.

What "Prepare Study" Means
--------------------------

Preparing a study turns local raw study files into a published study bundle
that the assistant can query. The active preparation workflow is the
``report-ai-study-pipeline`` plugin: duplicate handling first, Source Truth
from printed PDFs plus dataset row-1 headers second, and PHI-safe dataset
publishing last.

At a high level, the portal:

1. runs the duplicate preflight once for the study;
2. builds Source Truth sets from printed PDFs and dataset row-1 headers only;
3. stages raw dataset records inside trusted extraction code;
4. applies PHI-scrub rules to staged dataset fields;
5. cleans and aligns the study artifacts;
6. publishes the scrubbed bundle under ``output/{STUDY}/llm_source/``;
7. writes audit files under ``output/{STUDY}/audit/``;
8. opens the assistant against the published bundle.

Input Folder
------------

Place one study under ``data/raw/{STUDY_NAME}/``:

.. code-block:: text

   data/raw/Indo-VAP/
   ├── datasets/          # .xlsx or .csv study files
   ├── data_dictionary/   # dictionary workbooks, when available
   ├── annotated_pdfs/    # optional CRF templates
   ├── _forms_manifest.yaml
   └── _study_privacy.yaml

The repository does not ship raw study data. The local study team owns
which files are placed here.

``_forms_manifest.yaml`` declares which dataset files are required,
optional, or rejected. ``_study_privacy.yaml`` declares the study
jurisdictions and approval policy used by the audited extraction skill.

Output Folder
-------------

After a successful run, look under ``output/{STUDY_NAME}/``:

.. code-block:: text

   output/Indo-VAP/
   ├── llm_source/        # scrubbed bundle used by the assistant
   │   ├── SoT/           # plugin Source Truth policy/schema/joined sets
   │   ├── dataset_schema/
   │   └── dictionary_mapping/
   ├── audit/             # counts and lineage evidence
   ├── agent/             # chat state and generated analysis
   └── README.md          # local output summary

Users normally interact with ``llm_source/`` through the chat UI. The
``audit/`` folder is for review and troubleshooting.

Running Study Preparation
-------------------------

Normal users start from the web UI:

.. code-block:: bash

   make chat

Then click **Load Study**. The button activates the
``report-ai-study-pipeline`` workflow, or uses an existing valid
``output/{STUDY}/llm_source/`` bundle if one has already been prepared.
When raw dictionary files exist, the loaded bundle must include
``llm_source/dictionary_mapping/jsonl/`` outputs from the host dictionary
loader.
After a load or restore, click **Show processing log** to inspect the
captured output. The log opens in
a fixed-height scroll panel, and the same button changes to **Hide
processing log** so the wizard can be collapsed without refreshing the
page. Failed runs open the log automatically. Successful runs keep it
closed until you ask for it.

The command-line ``make pipeline`` path is a lower-level host publish path
used by the dataset child skill. It is for developers and deployment
operators who have already provisioned the local PHI key; it is not the
complete plugin workflow.

For audited dataset-publish CLI runs, use the cross-LLM dataset child skill:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
     --study Indo-VAP

   uv run --all-groups python scripts/skills/extract_to_llm_source.py verify \
     --study Indo-VAP

This wrapper adds PHI-key preflight, manifest checks, header-only
approval, terminal ``status.json``, ``verifier_report.json``, and
destruction attestation. The developer contract is in
:doc:`../developer_guide/extract_to_llm_source`.

To inspect one dataset at a time, add ``--form`` with a manifest-declared
dataset filename or stem:

.. code-block:: bash

   uv run --all-groups python scripts/skills/extract_to_llm_source.py run \
     --study Indo-VAP --form 6_HIV

Using an Existing Study
-----------------------

If ``output/{STUDY}/llm_source/`` already exists, the web UI can skip
rerunning study preparation and use the existing published bundle. This is
useful when the study was already loaded and you only want to ask questions.

PDFs
----

Annotated PDFs are optional. If they are present, the portal can use them
to enrich variable descriptions. If they are missing or unavailable, the
dataset and dictionary portions can still be used.

If your PDFs may contain PHI and you plan to use a hosted LLM provider,
review the setting in :doc:`configuration` before running the PDF path.

Audit Files
-----------

The audit folder is the user-facing evidence trail. It can help answer:

* which raw files were processed;
* whether scrub rules ran;
* what was published;
* whether a run needs review.

The audit files are not a substitute for study-team review, but they give
the team a concrete starting point.

Troubleshooting
---------------

If a run fails:

* check the terminal output first;
* if the extraction skill was used, inspect
  ``output/{STUDY}/runs/{run_id}/status.json`` and
  ``verifier_report.json``;
* confirm ``STUDY_NAME`` matches the folder under ``data/raw/``;
* confirm expected subfolders exist;
* confirm the PHI key exists if the scrubber asks for it;
* rerun after fixing the input or configuration issue.

For failure semantics and low-level pipeline behavior, see
:doc:`../developer_guide/operations`.
