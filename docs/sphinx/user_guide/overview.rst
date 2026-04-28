Overview
========

RePORT AI Portal helps a study team ask questions about one clinical
research study without handing raw PHI to the AI assistant.

It is built for a common bottleneck: researchers need simple cohort
answers, plots, and model summaries, but every request has to go through
a data manager because the raw files contain identifiers. The portal
keeps custody with the data team, creates a PHI-scrubbed study bundle,
and lets users ask grounded questions against that published bundle.

What It Does
------------

RePORT AI Portal:

* loads one study from local files;
* creates a PHI-scrubbed bundle for analysis;
* keeps raw data outside the AI assistant's working area;
* opens a local chat interface for questions about the study;
* returns counts, tables, charts, and short explanations grounded in
  the study files;
* produces audit files so the study team can verify what was processed.

Who It Helps
------------

* **Researchers** get faster answers to cohort and outcome questions.
* **Data managers** spend less time manually joining columns and
  exporting one-off spreadsheets.
* **PIs** get a repeatable way to review study status, cohort counts,
  and analysis outputs.
* **Reviewers** get a clear audit trail without needing access to raw
  subject data.

Typical Workflow
----------------

1. Put the study files under ``data/raw/{STUDY_NAME}/``.
2. Run the pipeline or click **Load Study** in the web UI.
3. The portal publishes a PHI-scrubbed bundle under ``output/{STUDY}/``.
4. Open the chat UI and ask questions about the published study.
5. Use the audit files when the team needs evidence of what changed.

Privacy in Plain Language
-------------------------

The raw files are treated as sensitive. The assistant is meant to work
from the published, scrubbed study bundle rather than the raw input
files. If a user chooses a hosted LLM provider, they are responsible for
confirming that their local study policy allows that provider.

For the full privacy architecture and control evidence, see
:doc:`../developer_guide/phi_architecture` and ``docs/irb_dossier/``.

When to Use It
--------------

Use RePORT AI Portal when:

* your team has one study to review or analyse;
* raw files are local and access-controlled;
* researchers need faster answers from the same study bundle;
* the team wants audit files for review.

Do not use it as a replacement for:

* multi-study federated analysis;
* formal statistical review by an epidemiologist;
* source data cleaning before the portal sees the files;
* an imaging or DICOM de-identification pipeline.

Next Step
---------

Start with :doc:`quickstart` if the repo is already installed, or
:doc:`installation` if this is a new machine.
