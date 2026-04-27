PHI Architecture
================

Rewritten 2026-04-27 against the v0.20.0 code state. The canonical
developer-facing description of the full PHI-handling story — the
four zones, the eight-action scrub catalog, the integrity chain, the
log redactor, the PDF orchestrator's redact-then-call posture, and
the agent-boundary three-gate stack. For the IRB-grade walkthrough
see ``docs/irb_dossier/phi_walkthrough.md`` (outside the Sphinx
tree); for the architectural decisions behind these mechanisms see
:doc:`decisions`.

The Four Zones (plus one out-of-zone tier)
------------------------------------------

Every artifact lives in exactly one of four zones. The fifth path
(``snapshots/{STUDY}/`` at the repo root) is *not* a zone in the
honest-broker sense — it's a version-controlled baseline, intentionally
outside every LLM-readable surface.

.. list-table::
   :header-rows: 1
   :widths: 14 30 56

   * - Zone
     - Path
     - PHI posture
   * - **RED**
     - ``data/raw/{STUDY}/``
     - Raw clinical inputs. Presumed PHI-bearing. Read-only by the
       extraction subprocess; the agent and the LLM never touch this
       zone.
   * - **AMBER**
     - ``tmp/{STUDY}/``
     - Per-run scratch. Mode ``0700`` under umask ``0077``. PHI is
       present here for the duration of one pipeline run; on success
       the entire tree is overwritten with random bytes + ``fsync``-ed
       + unlinked. On failure preserved for forensic inspection.
   * - **GREEN**
     - ``output/{STUDY}/trio_bundle/`` + ``output/{STUDY}/agent/``
     - PHI-free published artifacts + agent's own state.
       :func:`scripts.ai_assistant.file_access.validate_agent_read`
       admits paths in this zone only.
   * - **GREEN-PROTECT**
     - ``output/{STUDY}/audit/``
     - Counts-only IRB envelope: lineage manifest, scrub report,
       cleanup report, telemetry. Same ``output/`` tree as GREEN but
       hard-rejected by the agent's read-zone validator.

The fifth path:

* **``snapshots/{STUDY}/``** (repo root) — version-controlled
  cleaned trio bundle baseline used by the PDF orchestrator's per-PDF
  fallback. **The LLM cannot read it.** The path is outside the GREEN
  + GREEN-PROTECT trees so a stale baseline can never be served as
  live data. Maintainer-curated by hand; see :doc:`operations`.

Zone enforcement
~~~~~~~~~~~~~~~~

Two complementary chokepoints:

* :mod:`scripts.security.secure_env` — pipeline-side directory-level
  early-reject. Functions: ``assert_not_raw``, ``assert_output_zone``,
  ``assert_write_zone``, ``assert_trio_bundle_zone``. Used at
  pipeline boundaries (e.g. before the publish-step rename, before
  ``--pdf-source`` copy).
* :mod:`scripts.ai_assistant.file_access` — agent-runtime path
  validator. Functions: ``validate_agent_read``,
  ``validate_agent_write``, ``validate_sandbox_write``,
  ``is_agent_readable``. Resolves every path with
  ``os.path.realpath`` and verifies containment with
  ``os.path.commonpath``. Reads accept ``trio_bundle/`` ∪ ``agent/``
  (plus ``config/study_knowledge.yaml`` via an explicit allowlist for
  the StudyKnowledge helper). Agent-tool writes accept ``agent/`` only;
  ``exec_python`` sandbox writes narrow further to
  ``agent/analysis/``. Audit, telemetry, staging, raw, and the
  snapshot baseline are hard-rejected with ``ZoneViolationError``.

The Eight-Action Scrub Catalog (Step 1.6)
-----------------------------------------

:func:`scripts.security.phi_scrub.run_scrub` is invoked between the
parallel extraction phase and the dataset cleanup. It operates on
``tmp/{STUDY}/datasets/*.jsonl`` in place. Eight action classes,
evaluated in strict priority order against ~200 Indo-VAP-calibrated
rules in ``scripts/security/phi_scrub.yaml``:

1. **keep** — pass through (only for confirmed non-PHI columns)
2. **birthdate** — replace with ``birthyear`` only (HIPAA Safe
   Harbor §164.514(b)(2)(i))
3. **drop** — null out
4. **cap** — clamp at a quantile (the "age > 89" rule)
5. **generalize** — bucket into ranges (e.g. age → 5-year bands)
6. **suppress_small_cell** — null when the cohort cell has fewer
   than the configured threshold
7. **date_jitter (SANT)** — per-subject deterministic shift via
   ``HMAC-SHA256(key, subject_id)[:4] mod (2*max_days+1) - max_days``.
   Within-subject visit intervals are preserved exactly; absolute
   dates are obscured.
8. **hmac_pseudonymize** — replace IDs with
   ``SUBJ_<HMAC-SHA256(key, value)[:12]>``. Non-reversible without
   the key, deterministic with it.

The HMAC key lives at ``~/.config/report_ai_portal/phi_key`` (mode
``0600``, outside the repo, never committed). Path resolution:
``$XDG_CONFIG_HOME/report_ai_portal/phi_key`` if set, else
``~/.config/report_ai_portal/phi_key``.

Posture flags
~~~~~~~~~~~~~

The scrub config supports two "compliance posture" modes:

* **Default (Safe Harbor / NIST SP 800-188)** — ``birthdate`` ⇒
  ``birthyear``, drop precise dates, jitter within-subject, etc.
* **Limited Dataset (HIPAA §164.514(e))** — ``birthdate`` and
  precise dates retained because a Data Use Agreement is in place.
  Activated by ``compliance_posture: limited_dataset`` in
  ``phi_scrub.yaml`` AND a non-empty
  ``authorities/phi_limited_dataset.md`` attestation note.

Both pillars must hold. A YAML edit alone or an attestation note
alone is insufficient.

The Agent-Boundary Three-Gate Stack
-----------------------------------

Every tool return string passes through three gates before reaching
the LLM:

Gate 1 — PHI regex catalog (``phi_gate_check`` / ``guard_text``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Module: :mod:`scripts.security.phi_gate` and
:mod:`scripts.ai_assistant.phi_safe`. Pattern catalog:
:mod:`scripts.security.phi_patterns`. Allowlist:
:mod:`scripts.security.phi_allowlist`.

Blocking patterns: Aadhaar (12-digit + Verhoeff check), PAN
(``[A-Z]{5}[0-9]{4}[A-Z]``), email, phone (Indian mobile patterns
+ international), precise dates (``\d{1,2}[/-]\d{1,2}[/-]\d{2,4}``,
ISO ``\d{4}-\d{2}-\d{2}``), MRN-shaped tokens. When a blocking
pattern fires, the response is replaced with a redaction
message; the LLM sees the redaction notice, not the raw text.

The clinical-phrase allowlist exempts strings like "INH 5 mg/kg" or
"VL 300 copies/mL" from numeric-id false positives.

Gate 2 — k-anonymity (k=5) (``guard_rows_with_kanon``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Module: :mod:`scripts.security.kanon_gate`. Function:
:func:`scripts.security.kanon_gate.kanon_check` (used as a
primitive by ``guard_rows_with_kanon_and_ldiv`` below).

When a tool would surface row-level data, the gate computes the
equivalence class of each row over the configured quasi-identifiers
(``_DEFAULT_QUASI_IDENTIFIERS``: typically ``age_band``, ``sex``,
``district``). If any equivalence class has fewer than 5 members, the
gate suppresses the response and returns an aggregate or an explicit
"too-few-records" message.

Gate 3 — l-diversity (l=2) (``guard_rows_with_kanon_and_ldiv``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shipped in PR #13 (v0.18.0). Function:
:func:`scripts.security.kanon_gate.l_diversity_check` (used as a
primitive by ``guard_rows_with_kanon_and_ldiv``).

When a k-anon-passing equivalence class shares the same sensitive
attribute (e.g. all 5 rows have ``hiv_status = positive``), the gate
also fires. l=2 means the class must contain at least 2 distinct
values of the sensitive attribute. See ADR-015 in :doc:`decisions`
for the rationale.

The PDF Orchestrator's Redact-Then-Call Posture
-----------------------------------------------

ADR-012 (v0.19.0 / PR #15). The wizard's "Load Study" button
selects this path. Per PDF:

1. **Code path always runs.** ``pdfplumber`` extracts text + a
   heuristic candidate via
   :func:`scripts.extraction.pdf_pipeline._candidate_from_text`.
2. **Capability + provider gate.**
   :func:`scripts.utils.llm_capabilities.is_capable_model` enforces
   the model allowlist;
   :data:`scripts.extraction.pdf_pipeline.ORCHESTRATOR_SUPPORTED_PROVIDERS`
   restricts to anthropic + google.
3. **Redact-then-call.** Extracted text is scrubbed via
   :func:`scripts.extraction.pdf_pipeline._redact_text_for_llm`
   (which uses ``phi_patterns.BLOCKING_PATTERNS``). A defensive
   ``_assert_no_raw_phi_in_payload`` re-checks and raises if any
   blocking pattern survives. Only the redacted text reaches the
   LLM.
4. **Re-scrub the response.** The LLM response is parsed and every
   string field is re-scrubbed through
   :func:`scripts.ai_assistant.phi_safe.guard_text` before merge.
5. **Merge.** :func:`scripts.extraction.pdf_pipeline._merge`
   reconciles LLM + code candidates.
6. **Per-PDF snapshot fallback.** When the LLM tier is unavailable,
   the orchestrator publishes ``snapshots/{STUDY}/pdfs/{stem}_variables.json``
   instead. **Code-only output is never published** — heuristic-only
   metadata without LLM oversight is too unreliable for IRB-grade
   work.

**No raw PDF bytes leave the host on the orchestrator path.** The
legacy raw-PDF API path
(:func:`scripts.extraction.extract_pdf_data._resolve_pdf_provider`)
remains as the back-compat fallback gated by the two-part
attestation (``REPORTALIN_PDF_PHI_FREE=1`` + non-empty
``authorities/phi_free_pdfs.md``).

The Integrity Chain
-------------------

Three artifacts cryptographically link the raw inputs to the
published outputs:

1. **Per-row provenance dict** — every JSONL record in
   ``trio_bundle/datasets/`` carries a ``_provenance`` field with
   ``raw_sha256``, ``pipeline_version``, ``extraction_engine``,
   ``source_file``, ``sheet_name``, ``row_index``, ``study_name``,
   ``extraction_utc``. Traceability per row.
2. **Step-cache manifests** at
   ``output/{STUDY}/audit/manifests/{step}.json``. Each step (e.g.
   ``dataset_processing``) records the SHA-256 of every input file
   it consumed; the next run hashes inputs again and skips the step
   if all hashes match. Implementation:
   :mod:`scripts.utils.step_cache`.
3. **Lineage manifest** at
   ``output/{STUDY}/audit/lineage_manifest.json`` — Step 4 of the
   pipeline. Records:

   * Per-input hash: ``{path, sha256, size_bytes, mtime_utc}`` for
     every file under ``data/raw/{STUDY}/``.
   * Per-output hash: same shape for every file under
     ``trio_bundle/``.
   * Per-leg audit pointer: paths to ``phi_scrub_report.json``,
     ``dataset_cleanup_report.json``, etc.
   * **PHI-key fingerprint**: SHA-256 of the HMAC key bytes (so
     IRB reviewers can verify the same key was used as expected
     without ever seeing the key itself).
   * **Compliance posture**: ``default`` / ``limited_dataset`` /
     ``disabled`` / ``unknown``.
   * Pipeline version + emit timestamp.

   Implementation: :mod:`scripts.utils.lineage`.

Every audit report is **counts-only** (per ADR-009). No row contents,
no before/after pairs, no subject identifiers. The auditor reads
counts; if values are needed for debugging, the operator inspects
the live AMBER staging files (which only exist for the duration of
the run).

Log Hygiene
-----------

:func:`scripts.utils.log_hygiene.install_phi_redactor` attaches a
``logging.Filter`` to the root logger. Every log line goes through
the filter at format time. Patterns covered:

* API keys (``sk-ant-…``, ``sk-…``)
* Aadhaar, PAN, MRN-shaped tokens
* Phone, email
* Precise dates

The redactor is installed once in ``main.py`` (``_install_log_redactor_best_effort``)
and once in the AI Assistant entry points so both worlds emit
scrubbed logs.

KeyStore (PR #3)
----------------

ADR-011. API keys never persist in the parent process's
``os.environ``. The Streamlit wizard's step 1 routes the pasted key
into :mod:`scripts.ai_assistant.keystore` (an in-memory
``KeyStore`` registry); the corresponding ``*_API_KEY`` env variable
is scrubbed. Every LLM client takes ``api_key=`` as an explicit
kwarg sourced from the KeyStore. Keys are re-injected only into
the short-lived pipeline subprocess via
``KeyStore.env_for_subprocess``.

Subprocess Sandbox (PR #2)
--------------------------

ADR-010. ``run_python_analysis`` runs in a fresh ``subprocess.run``
child with ``RLIMIT_AS`` / ``RLIMIT_NPROC`` / ``RLIMIT_CPU`` clamps,
a sanitised env (no ``*_API_KEY`` from the parent KeyStore), and
read-only access to ``trio_bundle/`` only. AST + import + dunder +
builtin guards remain inside the child as defence-in-depth. See
:doc:`sandbox` for the full layered story.

Module Map
----------

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Module
     - Role
   * - :mod:`scripts.security.phi_scrub`
     - Eight-action scrub catalog driver. Reads
       ``scripts/security/phi_scrub.yaml``.
   * - :mod:`scripts.security.phi_patterns`
     - Shared regex catalog (``BLOCKING_PATTERNS``, ``WARN_PATTERNS``).
       Used by the agent-output gate, the PDF orchestrator's
       redaction step, and the log redactor.
   * - :mod:`scripts.security.phi_allowlist`
     - Clinical-phrase exemption (e.g. "INH 5 mg/kg" not flagged
       as a numeric ID).
   * - :mod:`scripts.security.phi_gate`
     - Agent-output PHI gate. ``phi_gate_check`` returns blocked /
       allowed.
   * - :mod:`scripts.security.kanon_gate`
     - k-anonymity (k=5) + l-diversity (l=2). ``kanon_check``,
       ``l_diversity_check``, ``guard_rows_with_kanon_and_ldiv``.
   * - :mod:`scripts.security.secure_env`
     - Pipeline-side directory-level zone guards.
   * - :mod:`scripts.security.phi_ner`
     - Local-Ollama narrative NER design stub
       (``REPORTALIN_OLLAMA_NER=1``).
   * - :mod:`scripts.ai_assistant.file_access`
     - Agent-runtime path validator (``validate_agent_read`` etc.).
   * - :mod:`scripts.ai_assistant.phi_safe`
     - Agent-side PHI helpers: ``phi_safe_return``, ``guard_text``,
       ``guard_user_prompt``, ``sanitise_untrusted_snippet``,
       ``redact_phi_in_text``, ``sanitise_traceback``.
   * - :mod:`scripts.ai_assistant.keystore`
     - In-memory API-key registry (PR #3).
   * - :mod:`scripts.utils.log_hygiene`
     - Logging filter for API-key + PHI redaction.
   * - :mod:`scripts.utils.lineage`
     - Lineage manifest emitter (Step 4).
   * - :mod:`scripts.utils.secure_staging`
     - AMBER staging prep + secure-zero-fill teardown.
   * - :mod:`scripts.utils.step_cache`
     - Per-step hash manifests for skip semantics.
   * - :mod:`scripts.extraction.pdf_pipeline`
     - PDF orchestrator with redact-then-call (PR #15).

IRB Benchmark Cross-Reference
-----------------------------

The 35-criterion conformance matrix (31 original + 4 added via patches 2026-04-23a/b) lives at
``docs/irb_dossier/conformance_matrix.md`` (outside the Sphinx tree).
Pillar mapping:

* **Pillar 1 — PHI scrub catalog**: ``phi_scrub.py`` + ``phi_scrub.yaml``,
  the 8 action classes documented above.
* **Pillar 2 — Zone isolation + agent access**: ``file_access.py`` +
  ``secure_env.py`` + the three agent-output gates.
* **Pillar 3 — Secure channel + integrity**: ``secure_staging.py`` +
  ``lineage.py`` + ``step_cache.py``.
* **Pillar 4 — Extraction safety**: ``dataset_pipeline.py`` +
  ``pdf_pipeline.py`` (PR #15) + ``extract_pdf_data.py``.
* **Pillar 5 — Governance + retention + breach**: ``phi_scrub.bootstrap_key``
  + ``_cleanup_staging`` + audit envelope.

When You Touch This Code
------------------------

Every diff that touches anything under ``scripts/security/``,
``scripts/ai_assistant/{file_access,phi_safe,keystore}.py``, or
``scripts/extraction/pdf_pipeline.py`` should:

1. Run ``make test-all`` locally — the 22 PHI-critical test modules
   listed in ``docs/irb_dossier/phi_walkthrough.md`` Q21 must all
   pass.
2. Run ``make doc-freshness`` — the lint compares live source-of-
   truth values (tool count, scrub-action count, version) against
   prose in this page and the IRB dossier.
3. If you change the scrub catalog (the YAML), the
   ``phi_scrub.yaml`` SHA-256 changes — which invalidates the PDF
   orchestrator's idempotent cache by design (the cache key
   includes ``phi_scrub.yaml`` hash). Confirmed by
   ``tests/security/test_pdf_redaction_pipeline.py::test_cache_key_invariants``.
4. If you add a new pattern to ``BLOCKING_PATTERNS``, add a positive
   test (the pattern fires) AND a negative test (the
   clinical-phrase allowlist still passes legitimate
   strings).

See Also
--------

* :doc:`architecture` — full system architecture.
* :doc:`decisions` — ADRs (especially 010-015 which cover the
  v0.19.0 / v0.20.0 PHI work).
* :doc:`sandbox` — subprocess sandbox.
* :doc:`operations` — snapshot-baseline maintenance protocol.
* ``docs/irb_dossier/phi_walkthrough.md`` — the IRB-grade
  walkthrough with regulatory mapping.
