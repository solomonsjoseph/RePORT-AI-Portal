# RePORT AI Portal — Executive Summary for the Institutional Ethics Committee

This document is written for an IEC / IRB reviewer who has not seen the
pipeline before. It explains, in plain terms, what the project is, what
threat model it addresses, how it handles PHI from raw input to agent
response, and where the auditor can verify each claim without reading
any code.

A companion document, [conformance_matrix.md](conformance_matrix.md),
lists the 35 testable criteria with the regulation each criterion answers,
the artifact that proves it, and the automated test that would fail in
CI if the claim ever regresses.

For a deeper walk-through of every PHI-handling process — both a
technical version (named methods, regulatory anchors, alternatives
considered, self-scrutiny Q&A) and a non-technical version (analogies,
everyday scenarios, IRB approval checklist) — see
[phi_walkthrough.md](phi_walkthrough.md).

## 1. What This Project Is

The RePORT AI Portal is a single-study AI assistant that answers a
researcher's epidemiological questions (incidence, risk factors,
associations, cohort summaries) directly from the study's own data —
without the weeks-to-months data-manager request queue that is the
current bottleneck.

The assistant runs locally against one fixed study under
`data/raw/{STUDY_NAME}/`. It never uploads raw study data to any
external service. The researcher asks a natural-language question; an
AI agent answers by querying structured data inside a de-identified
"trio bundle", and the raw data stays in the locked room.

The study this documentation is calibrated against is **Indo-VAP**
(Indo-US Vaccine Action Program — Biomarkers for Risk of TB), a
prospective TB cohort with two arms: index cases and household
contacts.

## 2. What This Project Is Not

- **Not** a cross-study federated analysis system. One study per install.
- **Not** a data-cleaning tool. It pseudonymises, generalises, and
  drops identifiers; it does not harmonise coding or impute missing
  values.
- **Not** a data-upload portal. Raw study data is placed in the
  filesystem out-of-band by the data manager; the assistant does not
  accept file uploads.
- **Not** a generic chatbot. The agent is grounded in one study's data
  dictionary and answers only questions the structured tools can
  resolve.

## 3. Threat Model

The protections below defend against:

1. **Raw PHI leakage into the agent-visible surface.** Subject IDs,
   names, Indian government IDs (Aadhaar / ABHA / PAN / voter / PM-JAY
   / Nikshay), exact dates, exact addresses, and narrative free-text
   must not reach the LLM, the researcher's chat screen, or any
   downstream audit artifact.
2. **Re-identification through quasi-identifier combinations.** Even
   with direct identifiers removed, a rare combination of
   `age-band × sex × district × outcome` can point to one subject.
3. **Data-recovery from deleted working files.** Staging workspace
   contents must not be recoverable from the underlying filesystem
   after the pipeline completes.
4. **Egress of PHI to external LLM APIs.** Any path that sends study
   bytes to a third-party service (Anthropic, Google) must be refused
   unless the operator explicitly attests the content is PHI-free.
5. **Silent drift between code and policy.** Every PHI-handling
   promise must be backed by an automated test that fails in CI if the
   code stops satisfying it.

The architecture documented below answers each of these.

## 4. End-to-End Data Flow

```
   Raw study data (PHI-bearing)
   data/raw/{STUDY}/
       ├─ datasets/            (Excel / CSV)
       ├─ data_dictionary/     (Excel)
       └─ annotated_pdfs/      (CRF templates / protocol)
                 │
                 │   (1) Extraction leg reads raw, writes to AMBER staging
                 ▼
   Transient staging (PHI-bearing, short-lived)
   tmp/{STUDY}/             (mode 0700, umask 0077;
       ├─ datasets/          optionally /dev/shm tmpfs on Linux)
       ├─ dictionary/
       ├─ pdfs/
       └─ quarantine/
                 │
                 │   (2) PHI scrub runs over staging datasets
                 │       BEFORE any audit artifact is written
                 ▼
   Transient staging (PHI-free)
                 │
                 │   (3) Dataset cleanup + cleanup propagation
                 │       run against staged artifacts; emit audits
                 ▼
   Published trio bundle (PHI-free, durable)
   output/{STUDY}/trio_bundle/
       ├─ datasets/              <-- LLM read zone (1 of 2; the other is `output/{STUDY}/agent/`)
       ├─ dictionary/
       ├─ pdfs/
       └─ variables.json
   output/{STUDY}/audit/
       ├─ phi_scrub_report.json
       ├─ dataset_cleanup_report.json
       ├─ dictionary_cleanup_report.json
       ├─ pdfs_cleanup_report.json
       └─ lineage_manifest.json
                 │
                 │   (4) Researcher asks the AI agent a question
                 ▼
   AI agent (LLM) queries trio_bundle via structured tools
                 │
                 │   (5) Every tool response passes through
                 │       a PHI gate + a k-anonymity check before
                 │       reaching the LLM
                 ▼
   Answer to the researcher
```

The four-zone labels referenced throughout the documentation map onto
this flow:

- **RED** — the raw study tree, accessed only by the extraction leg.
- **AMBER** — the transient staging workspace, where all PHI-handling
  transformations happen.
- **GREEN** — the published trio bundle, PHI-free by construction.
- **GREEN-PROTECT** — the agent-tool boundary, a defence-in-depth gate
  that catches anything the offline scrub might have missed.

## 5. What the PHI Scrub Does, in Plain Terms

The scrub step (number 2 in the flow above) applies eight named
actions against the rows in the staging datasets, in a fixed priority
order. The first action that matches a field's column name wins.

1. **Keep.** An allowlist of clinical lab / medication / time-of-day /
   categorical-indicator columns that the scrub never touches, so
   broader patterns cannot accidentally delete scientific content.
2. **Birthdate.** Under the default "Safe Harbor" posture the
   birthdate field is dropped entirely; under "Limited Dataset"
   (requires an IRB-approved DUA on file) the birthdate is shifted by
   the same per-subject offset as other dates.
3. **Drop.** The field is removed from every row. Covers names, Indian
   government IDs, contact information, exact addresses, narrative
   free-text, and staff identifiers.
4. **Cap.** Numeric values strictly greater than a threshold are
   replaced with a fixed label. Default: age > 89 → "90+".
5. **Generalise.** Value-level mapping to a broader category. Marital
   status → Married / Single / Other. Facility type → Government /
   Private / Other.
6. **Suppress small cell.** Numeric values greater than a threshold
   are clamped to the threshold. Default: household contact counts
   greater than 5 become 5.
7. **Date jitter.** Every remaining date is shifted by a per-subject
   constant offset in the range ±30 days. All dates for one subject
   shift by the same number of days, so the intervals between events
   survive unchanged (survival / incidence / person-time analyses are
   unaffected), while the exact calendar date of any event is
   obscured. This is the SANT method (Shift-And-Not-Truncate).
8. **Pseudonymise.** Subject IDs and other linkage identifiers are
   replaced with `SUBJ_<hash>`, where the hash is an HMAC-SHA256 of
   the original identifier keyed by a 32-byte secret that lives
   outside the repository tree at `~/.config/report_ai_portal/phi_key`
   (mode 0600). The same identifier always maps to the same pseudonym
   while the key is valid, so longitudinal linkage is preserved.
   Without the key, the pseudonym is non-reversible.

The rule catalog for Indo-VAP ships with roughly 200 rules: 80 keep +
93 drop + 3 cap + 3 generalise + 3 suppress + 25 date patterns + 20
id patterns. Rules are declared in
[`scripts/security/phi_scrub.yaml`](../../scripts/security/phi_scrub.yaml),
not in Python, so an auditor can review the entire catalog in one
text file.

## 6. What the Agent Can and Cannot See

The AI agent (LangChain / LangGraph ReAct pattern, provider-agnostic
via `init_chat_model`) has read access to the trio bundle and the
agent state directory (`output/{STUDY}/agent/**`), and nothing else.
Every tool function resolves the path through
`scripts.ai_assistant.file_access.validate_agent_read(path)` (the
unified agent-zone chokepoint; the legacy `assert_trio_bundle_zone`
remains as a directory-level early-reject at pipeline boundaries),
which raises a `ZoneViolationError` (a `PermissionError` subclass)
if any file path falls outside the agent's allowed zone.

Every tool return string additionally passes through two gates:

- A regex-based PHI gate with a clinical-phrase allowlist. Blocking
  patterns (Aadhaar, PAN, email, phone, precise dates, etc.) replace
  the response with a redaction message. Low-confidence warn patterns
  (short numeric IDs, generic name-like pairs) are audit-logged but
  allowed through unless the clinical allowlist also matches.
- A k-anonymity check. If the response would surface row-level data
  whose quasi-identifier equivalence class has fewer than 5 members,
  the gate suppresses the response and returns an aggregate or an
  explicit "too-few-records" message.

## 7. What the Operator Sees in the Audit Directory

After a successful pipeline run, `output/{STUDY}/audit/` contains five
JSON files. Every one of them is **counts-only** — no raw values, no
before/after pairs, no subject identifiers.

- `phi_scrub_report.json` — counts of each action per field per
  source file. The compliance posture (Safe Harbor or Limited
  Dataset) is recorded at the top. If this file is missing the scrub
  did not run; if its `scrubbed` array is empty the scrub was a
  no-op.
- `dataset_cleanup_report.json`, `dictionary_cleanup_report.json`,
  `pdfs_cleanup_report.json` — per-leg removed-column / merged-column
  counts from the downstream cleanup steps.
- `lineage_manifest.json` — pairs every raw input file (path +
  SHA-256 + size + mtime) with every published trio artifact (path +
  SHA-256 + size + mtime), plus references to the per-leg audit
  reports and the compliance posture used. Re-running the pipeline
  against the same raw inputs and the same HMAC key produces the
  same trio SHA-256 values. This is the single page an IEC reviewer
  can consult to verify the transformation happened as claimed.

The [audit report sample directory](./) will hold sanitised sample
outputs from a live run, added alongside the study's first production
ingest.

## 8. How an Auditor Verifies a Claim Without Reading Code

Every claim in the 35-criterion conformance matrix (31 original + 4
added via patches 2026-04-23a/b) pairs with an automated test. To
verify a claim:

1. Clone the repository and install the development environment:
   `uv sync --all-groups`.
2. Run the full test suite: `make test-all`. The expected result is
   "775 passed, 0 skipped, 0 failed". (`make test` runs the
   deterministic, network-free subset and reports "703 passed".)
3. Pick a claim from the conformance matrix and grep for the named
   test: `grep -r "TestCatalogCoverage" tests/`. Run just that test:
   `uv run pytest tests/test_phi_scrub.py::TestCatalogCoverage`.
4. Inspect the test body. Each test is written to fail if the claim
   is violated.

For live verification against a real pipeline run:

1. Execute `make pipeline` to produce the bundle and audit reports.
2. Use `jq` or a text editor to read the audit JSON files.
3. Confirm the file's counts match expectations (for example, the
   `phi_scrub_report.json` should show non-zero counts for
   `phi-scrub-drop`, `phi-scrub-id`, `phi-scrub-date`, and if present
   `phi-scrub-cap`).
4. Use `sha256sum` on each file listed in the lineage manifest to
   confirm the recorded hashes match disk contents.

## 9. Regulatory Anchors

Every architectural choice maps to at least one external authority.
The complete reference list with URLs is in the developer guide at
[`docs/sphinx/developer_guide/references.rst`](../sphinx/developer_guide/references.rst).
For IEC convenience, the primary anchors are:

- **HIPAA Privacy Rule §164.514(b)(2)** — the 18-identifier Safe
  Harbor list. Our drop catalog, cap rule, and date-precision rules
  all trace here.
- **India Digital Personal Data Protection Act 2023** — notified;
  substantive compliance effective May 13, 2027. Our India-government-
  ID drop rules and narrative-drop posture are calibrated to DPDPA's
  definition of personal data.
- **SPDI Rules 2011** — still in force until May 2027; defines
  "sensitive personal data or information" and backs the current
  narrative-drop and biometric-drop rules.
- **Aadhaar Act 2016 §29** — restricts sharing of Aadhaar identity
  information. Our catalog drops every Aadhaar-shaped field.
- **ICMR National Ethical Guidelines 2017 §11** — community-level
  confidentiality and k-anonymity guidance for cohort studies.
- **ABDM Health Data Management Policy** — governs ABHA (health ID)
  records; we drop every ABHA-shaped field.
- **RePORT India Common Protocol** — the study's governing protocol;
  defines the 72-hour IEC notification window for PHI breaches.
- **NIST SP 800-188** — de-identification techniques and audit-chain
  requirements. Our integrity chain (per-row `_provenance.raw_sha256`
  + lineage manifest) traces here.
- **STROBE / RECORD** — observational-study reporting rules.
  Reproducibility requirements.

## 10. What the IEC Should Review Before Approval

Recommended review sequence:

1. Read this document end-to-end.
2. Read [`conformance_matrix.md`](conformance_matrix.md). Each row is
   a testable promise. Spot-check any five rows by running the named
   tests locally.
3. Read
   [`docs/sphinx/developer_guide/phi_architecture.rst`](../sphinx/developer_guide/phi_architecture.rst)
   for the module-level walk-through.
4. Read
   [`docs/sphinx/developer_guide/decisions.rst`](../sphinx/developer_guide/decisions.rst)
   for the rationale behind each architectural choice, including the
   alternatives that were considered and rejected.
5. Read the **operator-owned runbook stubs** listed at the bottom of
   the conformance matrix (key management, breach response, data
   retention, DPDPA transition). These are the procedures the study
   team fills in before first production ingest — the code can
   enforce the technical controls, but an IEC cannot approve until
   the human-owned procedures are written.

Questions, requests for additional evidence, or suggestions for
additional claims the matrix should include are welcome. Contact the
study PI.
