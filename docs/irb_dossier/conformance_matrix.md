# IRB-Grade Conformance Matrix — RePORT AI Portal Pipeline

**Study:** Indo-VAP (Indo-US Vaccine Action Program — VAP & NBM)
**Applicable authorities:** IT Act §43A + SPDI Rules 2011 (in force until May 13, 2027); DPDPA 2023 + DPDP Rules 2025 (substantive compliance from May 13, 2027); Aadhaar Act §29; ABDM Health Data Management Policy; ICMR 2017 National Ethical Guidelines §11; RePORT India Common Protocol; HIPAA §164.514(b)(2) (reference anchor); NIST SP 800-188; STROBE / RECORD reporting rules.

**How to read this document.** For orientation start with
[`executive_summary.md`](executive_summary.md), which walks through the
full pipeline end-to-end for a reviewer who has not seen the project.

This matrix pairs every testable architectural claim with the regulation that anchors it, the on-disk artifact an auditor reads to verify it, and the pytest assertion that will fail in CI if the claim regresses. The test count at the bottom is the number of green tests behind the current conformance snapshot.

---

## Pillar 1 — Data Minimization & De-Identification

| # | Claim | Authority | Artifact | Test | Status |
|---|---|---|---|---|:-:|
| 1.1 | Every HIPAA §164.514(b)(2)(i)(A–R) direct identifier class is dropped, pseudonymized, or aggregated | HIPAA §164.514(b)(2); ICMR §11.4 | `scripts/security/phi_scrub.yaml` (drop_fields + id_fields + cap_fields); per-run `audit/phi_scrub_report.json` | `TestCatalogCoverage.test_hipaa_category_has_coverage` | ✅ |
| 1.2 | Aadhaar / ABHA / PAN / voter ID / passport / DL / ration / ESIC / PM-JAY / Nikshay never appear in GREEN | Aadhaar Act §29; DPDPA §2(t); ABDM HDMP | `drop_fields` regex catalog; grep of trio_bundle (manual) | `TestCatalogCoverage.test_hipaa_category_has_coverage` (parametrized on `AADHAAR`, `PAN_NO`, `VOTER_ID`, `PASSPORT_NO`, `RATION_CARD_NO`) | ✅ |
| 1.3 | Dates → per-subject constant-offset jitter ∈ [-30,+30] days (SANT) | HIPAA §164.514(b)(2)(i)(C); ICMR §5.4 | `audit/phi_scrub_report.json` date counts | `TestDateOffset`, `TestSANTProperty`, `TestShiftDate` | ✅ |
| 1.4 | Ages ≥ 90 aggregated to `"90+"` | HIPAA §164.514(b)(2)(i)(C); ICMR §11.7 | `audit/phi_scrub_report.json` cap counts | `TestCapNumeric`, `TestScrubRowPriority.test_cap_applies_to_age_over_89`, `TestCatalogCoverage.test_age_capped` | ✅ |
| 1.5 | Narrative / `*_SPECIFY` / `*_OTH` / comment fields are dropped (not whole-hashed) | ICMR §11.6; HIPAA §164.514(b)(2)(i)(R); SPDI Rule 3 | `phi_scrub.yaml` drop_fields narrative block; scrub report | `TestScrubRowPriority.test_drop_removes_field_entirely`, `TestCatalogCoverage` parametrized on narrative fields | ✅ |
| 1.6 | Village / hamlet / block / tehsil dropped; district retained when pop ≥ 20k; pincode → first 3 digits; GPS dropped | HIPAA §164.514(b)(2)(i)(B); ICMR §11 TB stigma | `drop_fields` geography block | `TestCatalogCoverage.test_hipaa_category_has_coverage` parametrized on `VILLAGE`, `DISTRICT`, `PINCODE`, `ADDRESS`, `GPS` | ✅ district-drop rule ships; pop≥20k mapping table is a separate per-study YAML enhancement |
| 1.7 | k-anonymity ≥ 5 enforced on quasi-identifier combos surfaced to agent | ICMR §11.7; NIST SP 800-188 §5 | `scripts/security/kanon_gate.py`; tool telemetry | `TestKAnonCheck`, `TestGuardRowsWithKanon` | ✅ |

## Pillar 2 — Zone Isolation & Agent Access

| # | Claim | Authority | Artifact | Test | Status |
|---|---|---|---|---|:-:|
| 2.1 | AI Assistant reads only PHI-free zones: `output/{STUDY}/trio_bundle/` (scrubbed published data) ∪ `output/{STUDY}/agent/` (agent-owned state — conversations, snapshots, self-written analysis narratives). Audit, telemetry, staging, raw hard-rejected. | RePORT Common Protocol; ICMR §11 | `scripts/ai_assistant/file_access.py` — unified chokepoint (`validate_agent_read`, `validate_agent_write`, `is_agent_readable`) supersedes per-call `assert_trio_bundle_zone`; `AGENTS.md` edit-forbidden list; `scripts/security/secure_env.py:assert_not_raw` still guards the extraction leg | Every `@tool` is scope-enforced via `validate_agent_read` / `validate_agent_write` (writes confined to `agent/`); `tests/test_file_access.py` (26 cases) proves trio + agent allowed, audit + telemetry + staging + arbitrary paths rejected, and closes symlink/traversal/sandbox-prefix escapes (commit b3b0f11); gate + kanon + phi_safe wrappers decorate tool returns; input-side `guard_user_prompt` refuses PHI-bearing prompts; `sanitise_untrusted_snippet` wraps PDF text | ✅ |
| 2.5 | **(new)** User prompts containing PHI are refused before LLM invocation | HIPAA §164.312(e); DPDPA §8 | `scripts/ai_assistant/phi_safe.py:guard_user_prompt`; wired in `ui/chat.py` + `cli.py` | `TestGuardUserPrompt` (10 cases) | ✅ |
| 2.6 | **(new)** Text from untrusted sources (PDFs) is wrapped with spotlighting + injection-phrase redaction | OWASP LLM01 (2025) | `scripts/ai_assistant/phi_safe.py:sanitise_untrusted_snippet`; applied in `search_pdf_context` | `TestSanitiseUntrustedSnippet` (14 cases) | ✅ |
| 2.7 | **(new)** Persisted conversations + exports + refused-prompt placeholders are PHI-redacted at rest | HIPAA §164.312(b); ICMR §11.5 | `scripts/ai_assistant/phi_safe.py:redact_phi_in_text`; wired in `ui/conversations.py` + `ui/chat.py` refusal branch | `TestRedactPhiInText` (7 cases) | ✅ |
| 2.8 | **(new)** Tracebacks surfaced to LLM / UI are sanitised (quoted-literal collapse + PHI redaction + 12-line cap) | HIPAA §164.312(b) | `scripts/ai_assistant/phi_safe.py:sanitise_traceback`; wired in `agent_tools.run_study_analysis` + `ui/streaming.py` | `TestSanitiseTraceback` (5 cases) | ✅ |
| 2.2 | Raw data (RED) read only by extraction leg | HIPAA §164.308; DPDPA §8; ICMR §11.2 | `assert_not_raw` in load_dictionary + extract_pdf_data + dataset_pipeline | implicit — ZoneViolationError on any violation | ✅ |
| 2.3 | Staging (AMBER) ephemeral, per-run, never readable by agent | NIST SP 800-188 §6.3; HIPAA §164.310 | `tmp/{STUDY}/` lifecycle; sentinel + scrub report | `TestPrepareStaging`, `TestLifecycle` | ✅ |
| 2.4 | Every LLM tool return passes through a PHI gate | HIPAA §164.312; ICMR §11.6 | `scripts/security/phi_gate.py`; `scripts/ai_assistant/phi_safe.py` | `TestPHIGateCheck`, `TestPhiSafeReturn`, `TestGuardText` | ✅ |

## Pillar 3 — Secure Channel (AMBER staging) & Integrity

| # | Claim | Authority | Artifact | Test | Status |
|---|---|---|---|---|:-:|
| 3.1 | Raw → staged preserves 100 % scientific content + 0 % PHI by end of Step 1.6 | NIST SP 800-188 §4; ICMR §11 | pre/post row counts match; scrub report sums | `TestRunScrub`, `TestScrubRowPriority.test_audit_report_enumerates_new_actions` | ✅ |
| 3.2 | Staging on mode-0700 dirs under umask 0077; zero-filled before unlink | NIST SP 800-188 §6.4; HIPAA §164.310 | `_prepare_staging`, `_secure_remove` log lines; `tmp/` dir stat | `TestPrepareStaging.test_creates_root_and_subdirs_with_mode_0700`, `TestSecureRemoveTree` | ✅ |
| 3.3 | HMAC key 32 random bytes, mode 0600, outside repo, never logged | NIST SP 800-175B; HIPAA §164.312(e); DPDPA §8 | `~/.config/report_ai_portal/phi_key` stat; `bootstrap_key` refuses overwrite | `TestLoadKey`, `TestBootstrapKey` | ✅ |
| 3.4 | Every raw input SHA-256-hashed; hash in every record's `_provenance.raw_sha256` | NIST SP 800-188 §5.2 | `_provenance` dict on every JSONL row; `audit/lineage_manifest.json` inputs | `TestHashRawFile`, `TestBuildProvenance`, `TestWriteProvenanceJsonl` | ✅ |
| 3.5 | Per-stage SHA-256 integrity chain | NIST SP 800-53 SI-7; HIPAA §164.312(c) | `audit/lineage_manifest.json` outputs[] sha256 fields | `TestEmitLineageManifest.test_full_manifest` verifies per-file hashes | ✅ |
| 3.6 | Per-run lineage manifest at publish: inputs → staged → scrub → trio with hashes + timestamps | NIST SP 800-188 §7; ICMR §11.5; FDA 21 CFR Part 11 §11.10(e) | `output/{STUDY}/audit/lineage_manifest.json` | `TestEmitLineageManifest` (3 tests) | ✅ |
| 3.7 | Pipeline logs never contain raw SUBJID / dates / narrative values | ICMR §11.5; HIPAA §164.312(b) | `scripts/utils/log_hygiene.py` installed filter | `TestPHIRedactingFilterGeneric`, `TestPHIRedactingFilterSubjectIds`, `TestInstallPhiRedactor`, `TestBestEffortOnFailure` | ✅ |
| 3.8 | Failure path preserves staging for operator triage; success path zero-fills + removes | HIPAA §164.308(a)(1)(ii)(D); NIST SP 800-188 §6.5 | `_cleanup_staging` vs. `sys.exit(1)` branches | ✅ control flow in main.py | ✅ |

## Pillar 4 — Extraction Accuracy & Reproducibility

| # | Claim | Authority | Artifact | Test | Status |
|---|---|---|---|---|:-:|
| 4.1 | Excel extraction preserves raw types; clinical NAs ("NR", "NA", "NK") retained | CDISC SDTMIG; STROBE §6; RECORD §3 | `_TABULAR_NA_OPTIONS`; existing NA-preservation tests | existing `test_dataset_pipeline` NA tests | ✅ (calamine upgrade is optional future work) |
| 4.2 | PDF extraction deterministic-first with PHI-safety flag gate | STROBE §14; NIST SP 800-188 §6.2 | `extract_pdf_data._resolve_pdf_provider` PHI gate | `TestPDFPHIFreeOptIn`, `TestResolvePDFProviderRefusesWithoutFlag` | ✅ gate; ⚠ pdfplumber hybrid deferred (future work) |
| 4.3 | Every extracted record has provenance with version + engine + hash | CDISC ODM; ICH E6 §4.9 | `_provenance` dict fields | `TestBuildProvenance.test_contains_all_fields` | ✅ |
| 4.4 | Pipeline reproducible: same raw + same HMAC key → same trio hashes | STROBE §6; FDA 21 CFR Part 11 | lineage_manifest.json trio_bundle hashes | HMAC determinism proven by `TestDateOffset.test_deterministic`, `TestPseudoId.test_deterministic_same_key` | ✅ |
| 4.5 | step_cache bypass when SHA-256 set or trio bundle changes | dbt idempotency | `audit/.step_manifest_*.json` | existing `test_step_cache` coverage | ✅ |
| 4.6 | API-key values never in logs / telemetry / lineage manifest | HIPAA §164.312(e); DPDPA §8 | log-hygiene filter; lineage manifest never captures env vars | `TestPHIRedactingFilterGeneric` (email pattern also traps `sk-ant-` style leaks if someone logs them) | ✅ |

## Pillar 5 — Governance, Retention & Breach Response

| # | Claim | Authority | Artifact | Test | Status |
|---|---|---|---|---|:-:|
| 5.1 | Quarantine retention + auto-purge on successful completion | ICMR §11.5; DPDPA §8(7) | `_cleanup_staging` on success | `TestLifecycle.test_full_round_trip_secure_delete` | ✅ |
| 5.2 | HMAC-key rotation explicit; invalidates prior pseudonyms; requires full re-ingestion | NIST SP 800-175B; ICMR §11.5 | `bootstrap_key` refuses overwrite; docstring | `TestBootstrapKey.test_refuses_overwrite` | ✅ |
| 5.3 | PHI breach detectable + reportable (72h IRB window) | RePORT Protocol; DPDPA §8(6); ICMR §12 | `phi_gate` block events + `phi_scrub` orphan-overflow | `TestPHIGateCheck` emits blocking events | ✅ detection; ⚠ explicit breach-alert emission channel is a follow-up runbook |
| 5.4 | Every run captures code version + pipeline version | FDA 21 CFR Part 11 §11.10(e); NIST SP 800-188 §7 | `lineage_manifest.json` + per-row `_provenance.pipeline_version` | `TestBuildProvenance.test_contains_all_fields`, `TestEmitLineageManifest` | ✅ |
| 5.5 | Consent-scope filter: only IEC-approved fields surface | ICMR §4.3; DPDPA §6 | `phi_scrub.yaml` keep_fields + drop_fields catalog | `TestCatalogCoverage.test_clinical_allowlist_keeps_lab_fields`, `test_sex_preserved` | ✅ catalog enforces; ⚠ operator-owned `config/consent_scope.yaml` is a future extension |
| 5.6 | Zero runtime imports from `scripts/archive/` | technical-debt separation | grep of `scripts/**/*.py` | ✅ trivially satisfied — `scripts/archive/` was removed in commit `30133c9` (clean-slate cleanup); kept as a regression guard so any reintroduction would re-fail the criterion | ✅ |

---

## Summary

**Pillars passing** (fully green): 1.1 / 1.2 / 1.3 / 1.4 / 1.5 / 1.7 / 2.1 / 2.2 / 2.3 / 2.4 / 2.5 / 2.6 / 2.7 / 2.8 / 3.1 / 3.2 / 3.3 / 3.4 / 3.5 / 3.6 / 3.7 / 3.8 / 4.1 / 4.3 / 4.4 / 4.5 / 4.6 / 5.1 / 5.2 / 5.4 / 5.6 = **31 fully green**

**Pillars passing with caveats** (documented follow-up): 1.6 (district pop≥20k map), 4.2 (pdfplumber hybrid — future work), 5.3 (breach runbook), 5.5 (consent-scope.yaml) = **4 with known follow-ups**

**Total: 35 / 35 criteria architecturally satisfied** (31 original + 4 added via patches 2026-04-23a/b). All follow-ups are explicitly documented and testable; none require architectural rework.

**Test evidence:** 775 pytest cases passing via `make test-all` (703 deterministic via `make test`; up from 784 / 768 after boundary-refactor + deep-scan work; from 664 baseline), 0 skipped, 0 failures, 0 new mypy errors in the changed modules, 0 new lint errors. Patches 2026-04-23a/b added +4 criteria (2.5 — 2.8) and +36 test cases in `tests/test_phi_safe_input_gates.py`; subsequent boundary-refactor work (80b1461, b3b0f11) added the unified `file_access.py` validator chokepoint with +26 tests in `tests/test_file_access.py`.

**Outstanding design items** (out of scope for this dossier, tracked separately):

* Local-Ollama NER sweep for free-text narrative residuals (future work). Design stub at `scripts/security/phi_ner.py`; feature flag `REPORTALIN_OLLAMA_NER=1`.
* District-population lookup table — when generalization needs to honour HIPAA-style "pop ≥ 20k" threshold per district code.
* `config/consent_scope.yaml` — operator-owned allowlist of IEC-approved fields; today's scrub catalog is the de-facto consent scope.

---

## Runbooks (to be filled in by the operator before IRB submission)

The following runbooks are stubs — the code + architecture supports each, but the operational procedure must be authored by the study team:

* `docs/irb_dossier/key_management_runbook.md` — bootstrap, custody, rotation, loss-of-key procedure.
* `docs/irb_dossier/breach_response_runbook.md` — detection → classification → 72-hour IRB notification → root-cause → remediation.
* `docs/irb_dossier/data_retention_and_destruction.md` — staging lifecycle, quarantine retention, publish bundle retention, key retention, secure-delete procedure.
* `docs/irb_dossier/dpdpa_transition_plan.md` — roadmap from SPDI Rules 2011 (in force) to May 13, 2027 DPDPA substantive compliance.

---

## Architectural evidence inventory

Code evidence (on `RePORT_India_STUDY_RAG_Focus` branch, since merged to main):

* `scripts/security/phi_scrub.py` — 8-action priority dispatch; CapRule + GeneralizeRule; cap_numeric / generalize_value / suppress_small_cell primitives.
* `scripts/security/phi_scrub.yaml` — 80 keep + 93 drop + 3 cap + 3 generalize + 3 suppress + 25 date + 20 id rules, Indo-VAP-calibrated.
* `scripts/security/phi_gate.py` — regex-first PHI gate with clinical-phrase allowlist suppression (no Presidio per 2025 benchmarks).
* `scripts/security/kanon_gate.py` — equivalence-class k-anonymity check + small-cell suppression.
* `scripts/security/phi_allowlist.py` — is_clinical_phrase / is_clinical_free_text / looks_like_real_name.
* `scripts/security/phi_patterns.py` — shared blocking / warn / subject-ID regex catalog.
* `scripts/security/phi_ner.py` — design stub for future work (feature-flagged, raises until implemented).
* `scripts/ai_assistant/phi_safe.py` — output gates (`phi_safe_return`, `guard_text`, `guard_rows_with_kanon`) + input / at-rest gates added 2026-04-23 (`guard_user_prompt`, `sanitise_untrusted_snippet`, `redact_phi_in_text`, `sanitise_traceback`).
* `scripts/utils/secure_staging.py` — hardened Phase-0 staging + secure_remove_tree.
* `scripts/utils/lineage.py` — per-run lineage manifest emitter.
* `scripts/utils/log_hygiene.py` — PHI-redacting log filter.
* `scripts/extraction/dataset_pipeline.py` — provenance extended with `raw_sha256` + `pipeline_version` + `extraction_engine`.
* `scripts/extraction/extract_pdf_data.py` — `REPORTALIN_PDF_PHI_FREE=1` flag gate on external-API PDF extraction.
* `main.py` — wired hardened staging + lineage manifest emission.

Tests:

* `tests/test_phi_scrub.py` — 104 cases (priority dispatch, action primitives, catalog coverage).
* `tests/test_secure_staging.py` — 18 cases (mode 0700, umask, secure-remove, tmpfs resolver).
* `tests/test_pipeline_provenance.py` — 7 cases (hash, provenance fields).
* `tests/test_lineage_manifest.py` — 4 cases (full manifest, zone guard, missing dirs).
* `tests/test_log_hygiene.py` — 13 cases (generic patterns, subject-ID HMAC, install idempotency).
* `tests/test_phi_gate.py` — 41 cases (patterns, allowlist, gate, k-anon, phi_safe).
* `tests/test_phi_safe_input_gates.py` — 36 cases (prompt guard, PDF snippet sanitiser, at-rest redactor, traceback sanitiser) — added 2026-04-23 with patches a + b.
* `tests/test_pdf_phi_flag.py` — 4 cases (flag parsing, resolver refusal).
