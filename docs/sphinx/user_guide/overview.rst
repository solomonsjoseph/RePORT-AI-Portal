Overview
========

The Pain
--------

Clinical-research teams at Indian sites wait **weeks to months** every time
they need a variable pulled from their own study data. The path looks like
this:

1. Researcher drafts a request ("give me the smoking-status column joined
   to the TB-recurrence outcome for Cohort A").
2. Email goes to the data manager.
3. Data manager queues the request behind dozens of others.
4. Extraction happens, tables are mailed back as Excel.
5. Researcher discovers they need one more column. Goto 1.

Each round-trip is an IRB-sensitive access — the raw data carries PHI and
cannot simply be copied to researcher laptops. So the queue is long by
design, not by accident.

The cost compounds: a cohort-level question that *should* take an hour
(fit a model, generate a plot, write a sentence) takes a calendar month.
Grants slip. Papers stall. Junior researchers give up.

What RePORT AI Portal Is
------------------------

**What.** A single-study, privacy-first, local-first AI assistant that
answers the researcher's questions directly from the published study
artifacts — without the data-manager round-trip and without ever exposing
raw PHI to the LLM.

**Why.** Researchers own epidemiological-question formulation and
interpretation; data managers own data-custody. The assistant covers the
mechanical bit in the middle (fetch the column, run the model, render the
plot) so the human-expert hours on both sides go to the work only humans
can do.

**How.** A four-tier honest-broker pipeline ingests raw study data once,
strips PHI into a PHI-free "trio bundle", and stands up a ReAct agent that
can query the bundle directly. The researcher talks to the agent; the
agent touches only the de-identified bundle; the raw data stays in the
locked room.

Who Benefits
------------

**Clinical researchers** who want immediate answers to epidemiological
questions (incidence, risk factors, interaction effects) from their own
study data without drafting a data-manager ticket.

**Principal investigators** who want a live, auditable picture of cohort
characteristics and outcome counts for grant reports and steering-committee
meetings.

**Data managers** who are tired of being a human JOIN engine, and who want
a defensible PHI story for every access.

**IRB / Institutional Ethics Committee reviewers** who want a single
evidence artifact (``audit/lineage_manifest.json``) pairing every raw input
hash with every published trio artifact hash, plus a 31-criterion
conformance matrix (plus four follow-ups added in patches
2026-04-23a/b) tied to HIPAA / DPDPA / SPDI / Aadhaar Act / ICMR /
NIST SP 800-188 / RePORT India Common Protocol.

**Epidemiologists** who want reproducible models — the pipeline preserves
per-subject date intervals exactly under SANT jitter, so survival and
person-time analyses run on the de-identified bundle return the same
numbers they would on the raw data.

When NOT to Use It
------------------

RePORT AI Portal is deliberately narrow. Do **not** reach for it when:

* **You need multi-study federated analysis.** The runtime is single-study
  by design. Multi-study workflows, HPC deployment, and federated
  aggregation are explicitly out of scope.
* **You need structured data-cleaning.** The pipeline scrubs PHI and
  propagates duplicate-column drops; it does not impute, harmonize units,
  or resolve coding discrepancies. Those remain data-manager work.
* **You need to upload new data through a UI.** Data lands in
  ``data/raw/{STUDY}/`` out-of-band; the agent reads the published bundle
  only. There is no upload-then-chat flow.
* **You want a general-purpose chatbot.** System prompts are ground-truthed
  against the study's data dictionary and grounded answers only. Off-study
  questions return "I don't have that data" rather than LLM hallucinations.

What's in the Bundle
--------------------

After ``make pipeline`` succeeds, ``output/{STUDY}/trio_bundle/`` contains
three companion artifacts the agent consults:

* ``datasets/`` — scrubbed JSONL, one file per study form. All direct and
  indirect identifiers dropped, pseudonymized, generalized, capped, or
  suppressed per the 8-action catalogue. Dates jittered per-subject with
  constant offset so intervals survive unchanged.
* ``dictionary/`` — the study data dictionary in JSONL (variable name →
  type → valid values → description).
* ``pdfs/`` — structured variable definitions extracted from annotated CRF
  PDFs (form-level metadata that doesn't fit in the dictionary).

Plus the sibling ``audit/`` directory containing ``phi_scrub_report.json``
(counts per action per field, no raw values), ``dataset_cleanup_report.json``,
``dictionary_cleanup_report.json``, ``pdfs_cleanup_report.json``, and
``lineage_manifest.json`` (the one-page IRB evidence artifact).

How You Use It
--------------

Two ways.

**Interactive chat.** ``make chat`` launches a Streamlit web UI with an
input box and a plot/table pane. Ask epidemiological questions in plain
English; the ReAct agent routes to structured tools (variable lookup,
dataset query, stats, plot rendering) and answers with citations back to
the bundle. See :doc:`quickstart` for a 10-minute walkthrough.

**Scripted pipeline run.** ``make pipeline`` runs the full extract →
scrub → publish flow and produces the bundle + audit reports. Run this
once per data refresh, then query via chat for as many iterations as the
research question needs.

Project Status
--------------

Current version: |version|. The runtime implements the four-tier
honest-broker architecture with a 31-criterion IRB benchmark (plus four
follow-ups added in patches 2026-04-23a/b) satisfied architecturally
(784 passing tests via ``make test-all``; 712 deterministic via
``make test``; zero new lint or mypy errors). See
``docs/irb_dossier/conformance_matrix.md`` for the full evidence matrix
and ``docs/irb_dossier/executive_summary.md`` for the IEC reviewer
orientation.
