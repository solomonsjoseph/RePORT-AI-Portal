# Abstract

**A Fail-Closed, Audit-Complete Pipeline for Privacy-Preserving LLM Analysis of
Multi-Jurisdictional Clinical Study Data: Deterministic Tool-Call Retrieval as an
Alternative to RAG**

**Background.** Large language models (LLMs) promise a natural-language interface to
clinical study data, but two barriers block deployment on regulated cohorts. First,
protected health information (PHI) must be removed with auditable, defensible rigor
under overlapping jurisdictions (US HIPAA Safe Harbor; India DPDPA / Aadhaar Act).
Second, retrieval-augmented generation (RAG) — the dominant LLM-data pattern — embeds
records into a vector index and returns approximate text spans, which is incompatible
with both exact quantitative analysis and byte-level provenance auditing. We present
the RePORT-AI Portal, a local-first pipeline, and evaluate it on the Indo-VAP
tuberculosis cohort (37 forms; 55,488 records).

**Methods.** PHI handling is *fail-closed*: nine priority-ordered scrub rules
de-identify all 18 HIPAA Safe-Harbor identifier classes plus India-specific
identifiers (Aadhaar confirmed by Verhoeff checksum; placeholder-rejecting phone
detection). Any value a rule cannot confidently handle quarantines the row and aborts
the run rather than emitting it — there is no silent pass-through. Dates receive
interval-preserving per-subject HMAC offsets, so longitudinal structure (visit
spacing, treatment duration) survives de-identification; identifiers become
domain-separated HMAC pseudonyms. A 14-assertion publish verifier proves, *before any
byte reaches the model*, that every published column carries an audit-ledger entry
(transformation or explicit keep-decision) and that no residual PHI pattern remains.
Retrieval replaces RAG with deterministic tool calls over the published *structured*
data (JSONL + dataset schema + source-of-truth policy YAML); a sandboxed Python tool
executes statistical models on exact values. We built a tracked offline evaluation
harness scoring (a) retrieval *resolvability*, (b) tool-routing correctness, and
(c) retrieval-primitive latency over a 10-question, clinician-authored benchmark
spanning cohort-level statistical modeling and protocol-definition questions.

**Results.** The deterministic PHI / security / audit test suite passes 431/431.
All 18 identifier classes map to a specific scrub rule; k-anonymity (k ≥ 5) and
l-diversity (l ≥ 2) additionally gate query-time row output, closing the residual
re-identification vector that file-level de-identification leaves open. Retrieval
resolvability — whether each question's authoritative source columns are present and
reachable through the security-gated tool layer — was 100% (4/4 statistical; 6/6
definitional). Tool-routing correctness was 100% on the deterministic contract, and
direct structured reads completed in 0.60 ms (p50) / 1.13 ms (p95). Unlike RAG,
tool-call retrieval returns exact computable values — enabling p-values, multivariable
logistic regression with backward selection, and interaction models — alongside
verifiable file-and-line citations, with no embedding index to build or re-embed on
each republish.

**Conclusions.** Fail-closed de-identification with runtime-proven audit completeness,
paired with deterministic tool-call retrieval, delivers defensible privacy together
with exact, citable, reproducible analysis on regulated clinical data — properties a
RAG architecture cannot structurally guarantee, because embedding-based retrieval is
lossy, approximate, and bakes the corpus into an index outside the fail-closed read
zone. Honest residual limits remain: free-text columns without value-level NER (which
resolve toward *hold*, not *leak*), and definitional questions whose formal narrative
criteria reside in an external study-protocol document not yet published to the read
zone. Both are bounded and tracked; neither produces silent incorrect output.

---

*Evidence: PHI suite 431/431 (`tests/test_phi_scrub*.py`, `tests/security/`,
`tests/audit/`); retrieval metrics from `scripts/eval/retrieval_eval.py` →
`docs/eval/retrieval_eval_results.md`; feasibility and data-gap detail in
`docs/reviews/indo_vap_retrieval_eval_readiness.md`. Numbers are single-host
measurements; a cloud-model accuracy + latency pass ("Track B") is operator-run with
an API key loaded in-process.*
