PHI Architecture
================

**What.** The canonical developer-facing description of the full PHI
handling story — the four-tier honest-broker zones, the eight-action
scrub catalog, the agent-boundary gates, the integrity chain, the log
hygiene layer, and the PDF PHI-safety gate. One page so a new
contributor can understand every defence before touching any module.

**Why.** Before this page the PHI story was split across
``architecture.rst``, ``operations.rst``, ``data_extraction_datasets.rst``,
the IRB dossier, and commit messages. A new contributor could read any
subset and have a consistent-but-incomplete picture. This page is the
single place to look.

**How.** Top-down: zones → transformations → gates → integrity →
observability. Every section names the module that implements the
behaviour so you can jump from doc to code in one click.

.. contents:: On this page
   :local:
   :depth: 2

The Four Tiers
--------------

.. code-block:: text

   ┌─ Tier 0 RED ────────────────────────────────────────────────────┐
   │ data/raw/{STUDY}/{datasets, data_dictionary, annotated_pdfs}/   │
   │ • Zone guard: assert_not_raw                                    │
   │ • Read-only by the extraction leg                               │
   │ • Hash every input file → SHA-256 in provenance                 │
   └────────────┬────────────────────────────────────────────────────┘
                │ deterministic extraction (Phase 0.a)
                ▼
   ┌─ Tier 1 AMBER — secure channel ─────────────────────────────────┐
   │ tmp/{STUDY}/ (or /dev/shm/{STUDY}/ on Linux tmpfs)              │
   │ • mode 0700, umask 0077 (via scripts/utils/secure_staging.py)   │
   │ • Step 1.6 phi_scrub: 8-action priority dispatch                │
   │ • Step 1.7 dataset_cleanup + Step 1.8 cleanup_propagation       │
   │ • Zero-fill + fsync + unlink on success                         │
   │ • Never read by the LLM agent                                   │
   └────────────┬────────────────────────────────────────────────────┘
                │ atomic per-leg publish
                ▼
   ┌─ Tier 2 GREEN — trio_bundle ────────────────────────────────────┐
   │ output/{STUDY}/trio_bundle/{datasets, pdfs, dictionary,         │
   │                              variables.json}                    │
   │ • PHI-free by construction                                      │
   │ • Post-publish SHA-256 manifest → audit/lineage_manifest.json   │
   │ • LLM agent read zone (1/2); the other is output/{STUDY}/agent/ │
   └────────────┬────────────────────────────────────────────────────┘
                │ every @tool guarded + gated
                ▼
   ┌─ Tier 3 GREEN-PROTECT — agent boundary ─────────────────────────┐
   │ scripts/ai_assistant/agent_tools.py                             │
   │ • file_access.validate_agent_read/write on every file I/O       │
   │   (unified chokepoint: trio_bundle + agent/ only; audit +       │
   │    telemetry + staging + raw are hard-rejected)                 │
   │ • phi_gate_check on every text return (phi_safe.phi_safe_return)│
   │ • kanon_check on row-level returns (kanon_gate)                 │
   │ • Telemetry _mask_phi on LLM tool previews                      │
   └─────────────────────────────────────────────────────────────────┘

RED zone — ``data/raw/{STUDY_NAME}/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** The unscrubbed study tree.
**Why.** Contains PHI by definition. Every access outside the extraction
leg is a potential breach.
**How.** :func:`scripts.security.secure_env.assert_not_raw` raises
``ZoneViolationError`` when any path under ``data/raw/`` reaches a
non-extraction module. The extraction leg itself uses the zone guard
indirectly via ``config.RAW_DATA_DIR`` and reads files read-only.

AMBER zone — ``tmp/{STUDY_NAME}/`` (or ``/dev/shm/{STUDY}/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** The transient staging workspace where extraction writes,
Step 1.6 scrubs, Step 1.7 cleans up, and Step 1.8 propagates cleanup
decisions to the dictionary + pdfs legs.
**Why.** Nothing in output/ can change until AMBER is PHI-free. Keeping
PHI-bearing data in a short-lived, permission-restricted, optionally
in-memory workspace minimises the window and blast radius of exposure.
**How.** :func:`scripts.utils.secure_staging.prepare_staging` sets mode
0700 and runs every write under ``umask 0077`` so files land mode 0600.
When ``REPORTALIN_TMPFS_STAGING=1`` AND ``/dev/shm`` is writable
(Linux), the staging root redirects to tmpfs and never hits physical
disk on the extraction host. On success, ``secure_remove_tree``
overwrites each staging file with ``secrets.token_bytes`` of matching
size, fsyncs, and unlinks — resistant to filesystem forensics.

GREEN zone — ``output/{STUDY_NAME}/trio_bundle/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** The PHI-free published bundle. One of two zones the agent
reads; the other is ``output/{STUDY}/agent/`` (:data:`config.AGENT_STATE_DIR`).
**Why.** Every claim about "the agent cannot see PHI" resolves to this
zone being PHI-free by construction. If a field leaks into GREEN, it
leaks everywhere downstream.
**How.** The ``_publish_staging`` function in ``main.py`` atomically
renames each AMBER leg (``datasets``, ``dictionary``, ``pdfs``) into
``trio_bundle/`` only after scrub + cleanup + propagation succeed. Per-leg
``assert_output_zone`` wraps every write. Publish is the AMBER → GREEN
transition — there is no intermediate state where the agent could see
partial output.

GREEN-PROTECT — the agent-tool boundary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**What.** A defence-in-depth layer between GREEN and the LLM.
**Why.** If the offline scrub (Step 1.6) misses any PHI token — because
a new field shape slipped past the catalog or because a narrative field
carries a regex-matchable identifier — the query-time gate catches it
before the string reaches the model.
**How.** Every ``@tool`` in ``scripts/ai_assistant/agent_tools.py`` starts
with :func:`scripts.security.secure_env.assert_trio_bundle_zone` for any
file I/O. Return values run through :func:`scripts.security.phi_gate.phi_gate_check`
via the :func:`scripts.ai_assistant.phi_safe.phi_safe_return` decorator.
Row-level responses also run through :func:`scripts.security.kanon_gate.kanon_check`
to enforce k-anonymity ≥ 5.

The Eight-Action Scrub Catalog
------------------------------

Every PHI transformation at Step 1.6 resolves to one of eight named
action classes, evaluated in a strict priority order for each field of
each row:

1. ``keep`` — allowlist; short-circuits every other rule. 80 rules in
   ``phi_scrub.yaml`` cover clinical lab / medication / time-of-day /
   categorical indicator columns that must survive the scrub.
2. ``birthdate`` — posture-dependent. ``safe_harbor`` drops the field
   entirely; ``limited_dataset`` falls through to rule 7 and requires
   ``authorities/phi_limited_dataset.md``.
3. ``drop`` — field removed from every row. 93 rules cover names, Indian
   government IDs, contact info, exact geography, system timestamps,
   narrative / specify / comment fields, staff identifiers, batch/scan
   metadata.
4. ``cap`` — numeric > threshold replaced with a label (default age > 89
   → ``"90+"``, HIPAA §164.514(b)(2)(i)(C)).
5. ``generalize`` — value-level categorical mapping (marital status →
   Married / Single / Other; facility type → Government / Private /
   Other).
6. ``suppress_small_cell`` — numeric > threshold clamped to the threshold
   (ICMR §11.7 k-anonymity proxy for household-contact counts).
7. ``date`` (jitter) — per-subject deterministic offset ∈
   ``[-max_jitter_days, +max_jitter_days]``. Offset = HMAC-SHA256(key,
   subject_id)[:4] mod (2*N+1) - N. SANT method preserves every
   per-subject interval exactly.
8. ``id`` (pseudonymize) — ``"SUBJ_" + hmac_sha256(key, raw_id)[:12]``.
   Deterministic cross-file linkage; non-reversible without key.

Rule catalog ships in ``scripts/security/phi_scrub.yaml`` (Indo-VAP-
calibrated) and is consumed by :class:`scripts.security.phi_scrub.PHIScrubConfig`
via :func:`scripts.security.phi_scrub.load_scrub_config`. The priority
dispatch lives in :func:`scripts.security.phi_scrub._scrub_row`.

The Integrity Chain
-------------------

**What.** Cryptographic linkage from every raw input file to every
published trio artifact.
**Why.** An auditor must be able to verify "these GREEN artifacts came
from those RED inputs, processed by this code version, under this
compliance posture." Hashes make the claim falsifiable without access
to the raw data.
**How.** Three layers:

* **Per-row**. Every JSONL row carries ``_provenance.raw_sha256`` (set
  by :func:`scripts.extraction.dataset_pipeline._build_provenance`),
  plus ``pipeline_version`` and ``extraction_engine`` strings.
* **Per-stage**. Every raw input is hashed at extract-time (via
  :func:`scripts.utils.integrity.hash_file`). Scrub preserves the
  provenance dict as-is (scrub acts on values, not metadata).
* **Per-run**. Step 4 emits ``output/{STUDY}/audit/lineage_manifest.json``
  via :func:`scripts.utils.lineage.emit_lineage_manifest` — a single
  JSON file pairing inputs and outputs by SHA-256 + size + mtime, plus
  per-leg audit references and the compliance posture used.

Log Hygiene
-----------

**What.** A ``logging.Filter`` that redacts PHI-like substrings from
every log record before the handler formats it.
**Why.** Raw subject IDs, dates, emails, and Aadhaar numbers must not
land in ``.logs/*.log``. A log file leak is the same breach as a dataset
leak; both must be closed.
**How.** :class:`scripts.utils.log_hygiene.PHIRedactingFilter` runs two
passes — first a per-subject HMAC-tagged replacement of configured
subject-ID regex matches, then the shared BLOCKING + WARN pattern
catalog from :mod:`scripts.security.phi_patterns`. Install once per
process via :func:`scripts.utils.log_hygiene.install_phi_redactor`; the
function is idempotent.

PDF PHI-Safety Gate
-------------------

**What.** A refusal at ``_resolve_pdf_provider`` when external-API PDF
extraction is attempted without the operator's explicit PHI-free
attestation.
**Why.** Sending raw PDF bytes to Anthropic / Google Gemini is a
network egress of PHI unless the source PDFs are verified PHI-free
(blank CRFs / protocol / MOP). Without this gate the pipeline would
silently leak PHI to a third-party LLM API.
**How.** The operator must set ``REPORTALIN_PDF_PHI_FREE=1`` to
authorize the external-API path. The flag is an explicit assertion
recorded against the IRB dossier. Alternatives that do not trigger the
gate: ``--pdf-source <path>`` with pre-extracted JSON, or skipping the
PDF leg entirely.

Module Map
----------

Cross-reference of every module involved in PHI handling:

.. list-table::
   :header-rows: 1
   :widths: 35 15 50

   * - Module
     - Tier
     - Responsibility
   * - :mod:`scripts.security.secure_env`
     - all
     - Zone-guard assertions (``assert_not_raw``,
       ``assert_write_zone``, ``assert_output_zone``,
       ``assert_trio_bundle_zone``).
   * - :mod:`scripts.security.phi_scrub`
     - AMBER
     - 8-action priority dispatch at Step 1.6; HMAC key management;
       posture enforcement; orphan quarantine; audit emission.
   * - ``scripts/security/phi_scrub.yaml``
     - AMBER
     - Indo-VAP-calibrated rule catalog (keep / drop / cap / generalize
       / suppress / date / id / birthdate).
   * - :mod:`scripts.security.phi_patterns`
     - AMBER + GREEN-PROTECT
     - Shared regex catalog (BLOCKING / WARN / SUBJECT_ID) consumed by
       the agent gate AND the log redactor.
   * - :mod:`scripts.security.phi_allowlist`
     - GREEN-PROTECT
     - Clinical-phrase allowlist — suppresses false-positive warnings
       on verbatim like "Treatment Completed" or "patient expired".
   * - :mod:`scripts.security.phi_gate`
     - GREEN-PROTECT
     - ``phi_gate_check`` — regex + allowlist; blocking hits replace the
       tool response with a redaction message.
   * - :mod:`scripts.security.kanon_gate`
     - GREEN-PROTECT
     - ``kanon_check`` for equivalence-class k-anonymity;
       ``mask_small_cell`` / ``suppress_small_cells`` for aggregate
       cross-tabs.
   * - :mod:`scripts.security.phi_ner`
     - GREEN-PROTECT
     - Stage-5 design stub for a local-Ollama narrative NER sweep;
       feature-flagged via ``REPORTALIN_OLLAMA_NER``.
   * - :mod:`scripts.ai_assistant.file_access`
     - GREEN-PROTECT
     - Unified agent-world file I/O chokepoint. ``validate_agent_read``
       / ``validate_agent_write`` / ``validate_sandbox_write`` /
       ``is_agent_readable`` resolve each path with ``os.path.realpath``
       and verify containment with ``os.path.commonpath``. Reads accept
       ``trio_bundle/`` ∪ ``agent/`` (plus the repo-tracked
       ``config/study_knowledge.yaml`` read-allowlist); agent-tool
       writes accept ``agent/`` only; ``exec_python`` sandbox writes
       narrow further to ``agent/analysis/``. Audit, telemetry,
       staging, raw, and arbitrary filesystem paths raise
       ``ZoneViolationError``. Symlinks and ``..`` traversal are
       neutralised by the realpath + commonpath pair.
   * - :mod:`scripts.ai_assistant.phi_safe`
     - GREEN-PROTECT
     - Output-side: ``@phi_safe_return`` decorator + ``guard_text`` /
       ``guard_rows_with_kanon``. Input-side (added 2026-04-23):
       ``guard_user_prompt`` refuses PHI-bearing researcher prompts at
       chat / CLI entry; ``sanitise_untrusted_snippet`` wraps
       PDF-extracted text in a spotlighting envelope and redacts
       imperative-voice injection phrases. At-rest:
       ``redact_phi_in_text`` for conversation persistence + exports;
       ``sanitise_traceback`` for error surfaces fed back to the LLM
       or the UI.
   * - :mod:`scripts.utils.secure_staging`
     - AMBER
     - ``prepare_staging`` hardens mode + umask; ``secure_remove_tree``
       zero-fills on teardown; ``resolve_staging_root`` switches to
       tmpfs when opted in.
   * - :mod:`scripts.utils.lineage`
     - GREEN
     - ``emit_lineage_manifest`` produces the one-page IRB evidence
       artifact pairing raw-hash with trio-hash.
   * - :mod:`scripts.utils.log_hygiene`
     - all
     - ``PHIRedactingFilter`` + ``install_phi_redactor`` — runtime log
       redaction using the shared regex catalog.
   * - :mod:`scripts.utils.integrity`
     - all
     - ``hash_file`` / ``hash_bytes`` streaming SHA-256 helpers; single
       source of truth for the integrity chain.
   * - :mod:`scripts.extraction.dataset_pipeline`
     - RED → AMBER
     - Reads raw, writes staged JSONL with full provenance (incl.
       ``raw_sha256``).
   * - :mod:`scripts.extraction.extract_pdf_data`
     - RED → AMBER
     - PDF extraction with the ``REPORTALIN_PDF_PHI_FREE`` gate on the
       external-API path.

IRB Benchmark Cross-Reference
-----------------------------

Every claim in the 31-criterion benchmark (plus four follow-ups added in
patches 2026-04-23a/b, totalling 35 architecturally satisfied) at
``docs/irb_dossier/conformance_matrix.md`` maps to at least one module
above. Pillar 1 (Minimization & De-ID) is satisfied by phi_scrub +
phi_scrub.yaml + phi_allowlist. Pillar 2 (Zone Isolation) is satisfied
by secure_env + the per-tool assertions. Pillar 3 (Secure Channel +
Integrity) is satisfied by secure_staging + integrity + lineage +
log_hygiene. Pillar 4 (Extraction Accuracy + Reproducibility) is
satisfied by dataset_pipeline provenance + the PDF PHI-safety gate.
Pillar 5 (Governance + Retention + Breach) is satisfied by the
counts-only audit contract + HMAC key rotation semantics.

When You Touch This Code
------------------------

* **Adding a PHI rule** — declare it in ``phi_scrub.yaml`` under the
  matching action section (``drop_fields`` / ``cap_fields`` / etc.).
  Add a case to the catalog-coverage tests in ``tests/test_phi_scrub.py``.
  Do NOT add inline regex in Python.
* **Adding a new agent tool** — start the function body with
  ``validate_agent_read(path)`` (for reads) or ``validate_agent_write(path)``
  (for writes) from :mod:`scripts.ai_assistant.file_access` for any file
  I/O. The validator is the unified chokepoint: it accepts only
  ``trio_bundle/`` + ``agent/`` paths and rejects audit, telemetry, staging,
  raw, and arbitrary filesystem paths with ``ZoneViolationError``. Wrap
  with ``@phi_safe_return`` so the gate runs on the return value. If the
  tool surfaces row-level data, call ``guard_rows_with_kanon`` first.
  If the tool surfaces text from outside the trio bundle (PDF extract,
  remote metadata, vocabulary file), wrap that text with
  ``sanitise_untrusted_snippet`` before returning it.
* **Adding a new user-input entry point** — call
  ``guard_user_prompt(text)`` before invoking the agent. If it returns
  ``ok=False``, display the refusal message and persist a category-
  tagged placeholder (never the raw prompt). See chat.py + cli.py for
  the pattern.
* **Adding a new persistence / export surface** — route user-facing
  text through ``redact_phi_in_text`` before write. Route exception
  traces through ``sanitise_traceback`` before any return that the LLM
  or UI can read.
* **Adding a new PHI class regex** — declare it in
  :mod:`scripts.security.phi_patterns` under ``BLOCKING_PATTERNS`` (high
  confidence) or ``WARN_PATTERNS`` (low-confidence heuristic). Both the
  agent gate and the log redactor pick up new patterns automatically.
* **Rotating the HMAC key** — delete ``~/.config/report_ai_portal/phi_key``,
  re-bootstrap, and re-run the pipeline from scratch. Every prior
  pseudonym + date offset is invalidated; there is no gradual
  migration path by design.

See Also
--------

* :doc:`decisions` — the "why" for every major PHI-architecture choice.
* :doc:`references` — every cited regulation and standard with URLs.
* :doc:`architecture` — the non-PHI-specific runtime architecture.
* :doc:`operations` — operational runbook.
