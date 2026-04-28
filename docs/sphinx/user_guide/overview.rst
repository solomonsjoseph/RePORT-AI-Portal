Overview
========

This page describes the current user-facing behavior of the portal:
the research bottleneck it removes, the privacy boundary it enforces,
and the workflow a researcher or data manager sees.

The Pain
--------

Clinical-research teams at Indian sites wait **weeks to months** every
time they need a variable pulled from their own study data. The path
looks like this:

1. Researcher drafts a request ("give me the smoking-status column
   joined to the TB-recurrence outcome for Cohort A").
2. Email goes to the data manager.
3. Data manager queues the request behind dozens of others.
4. Extraction happens, tables are mailed back as Excel.
5. Researcher discovers they need one more column. Goto 1.

Each round-trip is an IRB-sensitive access — the raw data carries PHI
and cannot simply be copied to researcher laptops. So the queue is
long *by design*, not by accident.

The cost compounds: a cohort-level question that *should* take an hour
(fit a model, generate a plot, write a sentence) takes a calendar
month. Grants slip. Papers stall. Junior researchers give up.

What RePORT AI Portal Is
------------------------

A single-study, privacy-first, local-first AI assistant that answers
the researcher's questions directly from the published study artifacts
— without the data-manager round-trip and without ever exposing raw
PHI to the LLM.

**Why.** Researchers own epidemiological-question formulation and
interpretation; data managers own data-custody. The assistant covers
the mechanical bit in the middle (fetch the column, run the model,
render the plot) so the human-expert hours on both sides go to the
work only humans can do.

**How.** A four-zone honest-broker pipeline ingests raw study data
once, strips PHI into a PHI-free "trio bundle", and stands up a ReAct
agent that can query the bundle directly. The researcher talks to the
agent; the agent touches only the de-identified bundle; the raw data
stays in the locked room.

The Four Zones
--------------

Every artifact this project produces lives in exactly one of four
zones. Confusing them is the failure mode every guard in the codebase
exists to prevent.

.. list-table::
   :header-rows: 1
   :widths: 14 30 56

   * - Zone
     - Path
     - Posture
   * - **RED**
     - ``data/raw/{STUDY}/``
     - Raw clinical inputs (Excel datasets, dictionary, annotated
       PDFs). Presumed to carry PHI. The agent and the LLM never read
       from here. The extraction subprocess is the only legitimate
       reader.
   * - **AMBER**
     - ``tmp/{STUDY}/``
     - Per-run scratch workspace. Mode ``0700`` under umask ``0077``.
       Optionally redirected to ``/dev/shm`` (tmpfs) on Linux via
       ``REPORTALIN_TMPFS_STAGING=1`` so raw extracted rows never hit
       physical disk on the extraction host. Securely overwritten +
       ``fsync``-ed + unlinked on a successful run; preserved on
       failure for forensic inspection.
   * - **GREEN**
     - ``output/{STUDY}/trio_bundle/``
     - The published, sanitised study bundle: ``datasets/``,
       ``dictionary/``, ``pdfs/``, ``variables.json``. Every byte has
       been through PHI scrub + cleanup + propagation + atomic
       publish. Together with ``output/{STUDY}/agent/`` this forms
       the LLM agent's read surface (``trio_bundle/`` ∪ ``agent/``).
   * - **GREEN-PROTECT**
     - Agent tool boundary
     - Defence-in-depth checks before the assistant speaks: PHI regex
       gate, k-anonymity (k=5), and l-diversity (l=2) for row-level
       results.

The counts-only IRB audit envelope lives at ``output/{STUDY}/audit/``:
lineage manifest, scrub report, cleanup report, and telemetry. It is
not part of the LLM read surface, and ``validate_agent_read`` rejects
it.

There is also a fifth, **out-of-zone** location at the repo root:
``snapshots/{STUDY}/`` holds a version-controlled, maintainer-curated
*snapshot baseline* of a cleaned trio bundle. The pipeline's PDF
orchestrator reads this baseline as a per-PDF fallback when the LLM
tier is unavailable. **The LLM cannot read it** — its read zone is
strictly ``trio_bundle/`` + ``agent/``, so a stale baseline can never
be served as live data. See :doc:`../developer_guide/operations` for
the maintenance protocol.

The Two-Way PDF Orchestrator
----------------------------

PDF extraction is a special case because annotated CRF PDFs may carry
filled-in patient data, handwritten signatures, or example subject IDs
in annotations. The pipeline ships two co-existing paths:

* **Orchestrator path** (``scripts/extraction/pdf_pipeline.py``, the
  wizard's "Load Study" default). The ``pdfplumber`` code path always
  runs first and extracts text
  locally. The text is PHI-redacted via ``phi_patterns.BLOCKING_PATTERNS``
  *before* any byte leaves the host. A defensive
  ``_assert_no_raw_phi_in_payload`` re-check raises if any blocking
  pattern survives. Only the redacted text reaches the LLM. The
  response is re-scrubbed and merged with the code candidate. Per-PDF
  fallback to the snapshot baseline at ``snapshots/{STUDY}/pdfs/``
  when the LLM tier is unavailable. **No raw PDF bytes leave the
  host on this path.**

* **Legacy raw-PDF API path** (``scripts/extraction/extract_pdf_data.py``,
  the CLI default for back-compat). Refused unless the operator
  attests, twice, that the source PDFs are PHI-free: env flag
  ``REPORTALIN_PDF_PHI_FREE=1`` *and* a non-empty attestation note
  at ``authorities/phi_free_pdfs.md``.

The Agent Side
--------------

Once the trio bundle is published, the user is in chat. The LLM agent
(LangGraph ReAct pattern, provider-agnostic via ``init_chat_model``)
has 12 tools and three independent gates on every tool return:

1. **PHI gate** — regex catalog (Aadhaar, PAN, MRN, phone, email,
   precise dates) with a clinical-phrase allowlist.
2. **k-anonymity gate (k=5)** — equivalence-class size check on
   row-returning tools.
3. **l-diversity gate (l=2)** — sensitive-attribute homogeneity check
   for row-level results.

Plus three structural protections:

* **Read-zone enforcement** —
  :func:`scripts.ai_assistant.file_access.validate_agent_read` resolves
  every path with ``os.path.realpath`` and verifies containment in
  ``trio_bundle/`` ∪ ``agent/`` only. Audit, telemetry, staging, raw,
  and the snapshot baseline are hard-rejected with
  ``ZoneViolationError``.
* **KeyStore** — API keys never live in the parent process's
  ``os.environ``. The wizard's step 1 routes the pasted key into an
  in-memory ``KeyStore`` registry; the corresponding ``*_API_KEY``
  env variable is scrubbed. Keys are re-injected only into the
  short-lived pipeline subprocess via ``KeyStore.env_for_subprocess``.
* **Subprocess sandbox** — ``run_python_analysis`` runs in an
  isolated subprocess with ``RLIMIT_AS`` / ``RLIMIT_NPROC`` /
  ``RLIMIT_CPU`` rlimits, a sanitised env, and read-only access to
  ``trio_bundle/`` only. The generated ``.py`` file is persisted to
  ``output/{STUDY}/agent/analysis/{ts}.py`` for operator
  reproduction.

The assistant is instructed to answer in natural clinical-research
language while staying grounded. For substantive study questions it
must resolve variable names before analysis, cite the dataset/form
evidence in plain language, keep computed facts separate from
interpretation, and surface missing data, low-confidence PDF matches,
small-cell suppression, or underpowered models as caveats.

The Wizard
----------

The Streamlit web UI (``make chat``) walks the operator through three
steps:

1. **LLM** — pick provider + model, paste API key. Key goes straight
   into the in-memory KeyStore; never persisted to disk, never
   leaked into ``os.environ``.
2. **Data** — two top-level buttons:

   * *Use Existing Study* — skip the pipeline, trust whatever's
     published in ``output/{STUDY}/trio_bundle/``. Disabled when no
     bundle exists.
   * *Load Study* — run the pipeline subprocess. The PDF
     orchestrator runs in this path; per-PDF fallback to the
     snapshot baseline kicks in when the LLM tier is unavailable.

3. **Chat** — confirm and start asking questions.

Who Benefits
------------

* **Clinical researchers** who want immediate answers to
  epidemiological questions (incidence, risk factors, interaction
  effects) from their own study data without drafting a data-manager
  ticket.
* **Principal investigators** who want a live, auditable picture of
  cohort characteristics and outcome counts for grant reports and
  steering-committee meetings.
* **Data managers** who are tired of being a human JOIN engine, and
  who want a defensible PHI story for every access.
* **IRB / Institutional Ethics Committee reviewers** who want a single
  evidence artifact (``output/{STUDY}/audit/lineage_manifest.json``)
  pairing every raw input hash with every published trio artifact
  hash, plus an active conformance matrix tied to HIPAA, DPDPA, SPDI,
  Aadhaar Act, ICMR, NIST SP 800-188, and the RePORT India Common
  Protocol.
* **Epidemiologists** who want reproducible models — the pipeline
  preserves per-subject date intervals exactly under SANT jitter, so
  survival and person-time analyses run on the de-identified bundle
  return the same numbers they would on the raw data.

When NOT to Use It
------------------

RePORT AI Portal is deliberately narrow. Do **not** reach for it
when:

* **You need multi-study federated analysis.** The runtime is
  single-study by design. Multi-study workflows, HPC deployment, and
  federated aggregation are explicitly out of scope.
* **You need structured data-cleaning.** The pipeline scrubs PHI and
  applies an honest-broker catalog, but it does not impute missing
  values, harmonise units across studies, or perform feature
  engineering. Bring your own cleaning step downstream.
* **Your raw data is already de-identified to a public standard.**
  This pipeline is overkill for a published WHO indicator dump or a
  publicly-released SDTM submission. Use plain pandas.
* **You need an answer right now and the inputs aren't checked in.**
  ``data/raw/{STUDY}/`` must exist with ``datasets/``,
  ``data_dictionary/``, and (optionally) ``annotated_pdfs/``. The
  pipeline cannot conjure them.

Where To Go Next
----------------

* :doc:`installation` — system requirements + one-shot install
  (``uv sync --all-groups``).
* :doc:`quickstart` — ten-minute walkthrough from clone to first
  answer.
* :doc:`data_pipeline` — the full pipeline in operator terms.
* :doc:`configuration` — every runtime knob, including the PHI-safety
  flags + the KeyStore credential-handling note.
* :doc:`../developer_guide/index` — for contributors and code reviewers.
* ``docs/irb_dossier/`` (outside the Sphinx tree) — the IRB-grade
  evidence package: conformance matrix, PHI walkthrough, and executive
  summary.
