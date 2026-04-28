# PHI Follow-Up Register

This file is the current residual-risk register for PHI handling. It is
not a historical implementation plan. Closed work is described in the
architecture and conformance docs; this page keeps only items that still
need an operator decision, a future implementation, or an explicit
deployment constraint.

## Current PHI posture

The production path is single-study and local-first:

- Raw study files stay in `data/raw/{STUDY}/`.
- Extracted rows land in AMBER staging at `tmp/{STUDY}/`.
- Step 1.6 runs the eight-action PHI scrub before publish.
- The published LLM read surface is limited to
  `output/{STUDY}/trio_bundle/` and `output/{STUDY}/agent/`.
- `output/{STUDY}/audit/` is counts-only and agent-rejected.
- Every agent tool return passes through PHI and row-privacy gates.
- The PDF orchestrator extracts text locally, redacts it before any LLM
  call, re-scrubs the response, and falls back per PDF to the tracked
  snapshot baseline when the LLM tier is unavailable.

See `conformance_matrix.md` for the claim-by-claim test inventory and
`phi_walkthrough.md` for the full PHI handling narrative.

## Open follow-ups

| ID | Area | Current control | Remaining work | Owner |
|---|---|---|---|---|
| F2 | Breach response | PHI gate blocks and scrub audits are recorded; logs are PHI-redacted. | Write the study-team breach-response runbook: detection, severity, IRB/IEC notification window, containment, root cause, and remediation. | Study team |
| F3 | Retention/destruction | AMBER staging is securely removed on success; output and audit trees are durable. | Write the retention/destruction runbook for raw inputs, trio bundle, audit envelope, restore points, logs, and HMAC key custody. | Study team |
| F4 | Consent scoping | `phi_scrub.yaml` is the de-facto field allow/drop catalog. | Optionally add `config/consent_scope.yaml` as an IEC-approved allowlist layered above the scrub catalog. | Study team + engineering |
| F5 | District population threshold | Geography identifiers are dropped or generalized by catalog rules. | Add a per-study district-population mapping if the site needs population-threshold retention logic. | Study team |
| F6 | Narrative rescue | High-risk narrative/free-text fields are dropped by default. | Calibrate and implement local narrative NER only if preserving narrative signal becomes scientifically necessary. | Engineering |

## Deployment constraints

The current architecture is appropriate for local, single-study,
single-operator or institution-controlled use where the operator controls
the machine and the raw data directory.

Before multi-tenant, hosted, or cross-institution deployment, add:

- authenticated user identity and authorization,
- encrypted log/audit storage or institution-managed encrypted volume,
- per-study run locking,
- explicit breach-response and retention runbooks,
- upload policy enforcement,
- and deployment-specific network egress controls.

## Closed controls that must stay closed

These are no longer follow-ups; they are baseline controls and should
regress only with a failing test:

- mandatory k-anonymity and l-diversity for row-level agent returns,
- input-side PHI refusal before LLM invocation,
- prompt-injection wrapping for untrusted PDF snippets,
- traceback sanitization before surfacing tool or UI errors,
- KeyStore API-key handling in the Streamlit flow,
- sandboxed `run_python_analysis`,
- PDF redact-then-call orchestration,
- counts-only audit files,
- lineage manifest hash chain,
- secure AMBER staging teardown.

Any change to those controls must update `conformance_matrix.md`,
`phi_walkthrough.md`, and the relevant pytest coverage in the same PR.
