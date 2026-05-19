Conformance Evidence
====================

This table gives reviewers a concise control map. It identifies the
claim, the evidence to request, and the regression check that should
remain green in CI.

.. list-table::
   :header-rows: 1
   :widths: 26 28 26 20

   * - Control
     - Evidence
     - Automated check
     - Authority alignment
   * - Raw files are not assistant-readable.
     - Agent path validator rejects raw, staging, and audit locations.
     - ``tests/test_file_access.py``
     - ICMR confidentiality; HIPAA minimum necessary posture.
   * - Staging is temporary and restricted.
     - ``tmp/{STUDY}/`` mode, cleanup report, and secure staging tests.
     - ``tests/test_secure_staging.py``
     - ICMR confidentiality; NIST de-identification operations.
   * - Direct identifiers are removed or pseudonymized before publish.
     - PHI scrub catalog and per-run PHI scrub report.
     - ``tests/test_phi_scrub.py``
     - HIPAA 45 CFR 164.514; DPDPA/SPDI; Aadhaar/ABDM.
   * - Header approval does not expose row values.
     - ``phi_handling_approval.json`` contains headers, actions, and
       rule metadata only.
     - ``tests/security/test_phi_review.py`` and
       ``tests/skills/test_extract_to_llm_source_cli.py``
     - Minimum necessary and data-minimisation posture.
   * - Dates are protected.
     - Default date drop/shift behavior; Limited Dataset attestation
       when precise-date utility is approved.
     - ``tests/test_phi_scrub.py``
     - HIPAA Safe Harbor/Limited Dataset; ICMR privacy.
   * - Government IDs are blocked.
     - Scrub catalog, PHI gate catalog, and PHI gate test results.
     - ``tests/test_phi_gate.py``
     - Aadhaar Act; ABDM; DPDPA/SPDI.
   * - Row-level assistant answers are privacy-gated.
     - k-anonymity and l-diversity gate behavior.
     - ``tests/test_phi_gate.py``
     - ICMR confidentiality; re-identification risk reduction.
   * - PDF content is PHI-safe before LLM use.
     - Redact-then-call orchestrator, PHI-free PDF attestation gate for
       legacy raw-PDF path, and PDF redaction tests.
     - ``tests/security/test_pdf_redaction_pipeline.py``
     - HIPAA disclosure controls; ICMR confidentiality.
   * - Audit artifacts do not expose row data.
     - Counts-only audit reports and lineage manifest.
     - ``tests/test_lineage_manifest.py``
     - IRB/IEC auditability without raw-PHI disclosure.
   * - Published assistant source is scanned before verification passes.
     - ``verifier_report.json`` assertion for PHI-pattern absence in
       ``llm_source/``.
     - ``tests/skills/test_extract_to_llm_source_verify.py``
     - Pre-disclosure validation and auditability.
   * - Logs and persisted assistant text are redacted.
     - Log hygiene filter and at-rest redaction helpers.
     - ``tests/test_log_hygiene.py`` and
       ``tests/test_phi_safe_input_gates.py``
     - HIPAA audit/security safeguards; ICMR confidentiality.

Reviewer Evidence Package
-------------------------

For a submission or audit, attach:

* the commit SHA under review,
* CI results for tests, lint, typecheck, dependency audit, and docs,
* a representative ``output/{STUDY}/audit/`` package with raw PHI
  withheld,
* the PHI scrub configuration used for the run,
* the PHI-key custody statement without the key value,
* any Limited Dataset or PHI-free PDF attestation that enabled a
  higher-risk mode.

Open Operator Items
-------------------

These items are study-team responsibilities before production research
use:

* breach-response runbook,
* retention and destruction runbook,
* consent-scope or approved-field allowlist when required by the IEC/IRB,
* district population-threshold mapping if geography is retained,
* narrative/free-text retention approval if narrative fields are ever
  needed.
