Data Pipeline
=============

This page explains what happens when a user loads a study. It avoids
implementation detail; developers should use
:doc:`../developer_guide/architecture` and
:doc:`../developer_guide/operations`.

What "Load Study" Means
-----------------------

Loading a study turns local raw study files into a published study bundle
that the assistant can query.

At a high level, the portal:

1. reads the study datasets, data dictionary, and optional annotated PDFs;
2. stages extracted files in a temporary workspace;
3. applies the PHI-scrub rules to dataset fields;
4. cleans and aligns the study artifacts;
5. publishes the scrubbed bundle under ``output/{STUDY}/trio_bundle/``;
6. writes audit files under ``output/{STUDY}/audit/``;
7. opens the assistant against the published bundle.

Input Folder
------------

Place one study under ``data/raw/{STUDY_NAME}/``:

.. code-block:: text

   data/raw/Indo-VAP/
   ├── datasets/          # .xlsx or .csv study files
   ├── data_dictionary/   # data dictionary workbook or CSV
   └── annotated_pdfs/    # optional CRF templates

The repository does not ship raw study data. The local study team owns
which files are placed here.

Output Folder
-------------

After a successful run, look under ``output/{STUDY_NAME}/``:

.. code-block:: text

   output/Indo-VAP/
   ├── trio_bundle/       # scrubbed bundle used by the assistant
   ├── audit/             # counts and lineage evidence
   ├── agent/             # chat state and generated analysis
   └── README.md          # local output summary

Users normally interact with ``trio_bundle/`` through the chat UI. The
``audit/`` folder is for review and troubleshooting.

Running the Pipeline
--------------------

Normal users run the pipeline from the web UI:

.. code-block:: bash

   make chat

Then click **Load Study**. After a load or restore, click **Show
processing log** to inspect the captured pipeline output. The log opens in
a fixed-height scroll panel, and the same button changes to **Hide
processing log** so the wizard can be collapsed without refreshing the
page. Failed runs open the log automatically. Successful runs keep it
closed until you ask for it.

The command-line ``make pipeline`` path is for developers and deployment
operators who have already provisioned the local PHI key.

Using an Existing Study
-----------------------

If ``output/{STUDY}/trio_bundle/`` already exists, the web UI can skip
the pipeline and use the existing published bundle. This is useful when
the study was already loaded and you only want to ask questions.

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
* confirm ``STUDY_NAME`` matches the folder under ``data/raw/``;
* confirm expected subfolders exist;
* confirm the PHI key exists if the scrubber asks for it;
* rerun after fixing the input or configuration issue.

For failure semantics, snapshot maintenance, and low-level pipeline
behavior, see :doc:`../developer_guide/operations`.
