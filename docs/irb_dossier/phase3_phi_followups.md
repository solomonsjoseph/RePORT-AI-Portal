# PHI Handling — Comprehensive Implementation Plan (Phase 2.x Polish + Phase 3)

**Audit dates:** 2026-04-27 (three passes: initial / deeper sweep / extraction-pipeline + scrub-internals + PR re-verify)
**Author of plan:** in-house security audit
**Closure status as of v0.17.2:** PRs #10 + #11 close 17 of 23 actionable items (P1a-f + P2-P6 + N1-N3 + N5 + N11-N12). Remaining items are Phase 3 architectural work documented below.

---

## Executive answer

> **Is PHI complete + fully functional with zero security flaw as of v0.17.2?**

**Closer, but not yet.** v0.17.2 closes 17 small/MEDIUM items across permissions, pattern coverage, scrub-config gating, lineage auditability, and zone assertions. The remaining items are **architectural** — mandatory k-anonymity on row-returning tools, l-diversity, PDF redaction before LLM upload, traceback sanitiser, parallel-run lock — and need their own focused PRs in Phase 3.

For the **current Phase 2 scope** (single-operator, single-study Indo-VAP, IRB-approved internal use), the remaining items are MEDIUM/HIGH not BLOCKER — the operator is the trusted recipient, no external party sees raw outputs, and the operator is expected to use FileVault / LUKS for at-rest protection. For **any external collaboration / multi-tenant / cloud-hosted scope**, the architectural Phase 3 items must close.

The honest current statement is: *"PHI architecture is Phase-2-ready for single-operator IRB-approved scope; Phase 3 architectural PRs (k-anon mandatory, PDF redaction, traceback sanitiser) needed before external deployment or multi-tenant use."*

---

## Closure status (as of v0.17.2)

### ✅ Closed by PR #10 (`fix/phi-phase2-polish`)

| Item | Closes |
|---|---|
| **P1a** | `conversations.py` × 3 sites — `chmod(0o600)` after every JSON write |
| **P1b** | `telemetry.py` event sink — `chmod(0o600)` after every append |
| **P1c** | sandbox per-call `spec.json` — `chmod(0o600)` after write |
| **P1d** | sandbox-persisted `run_*.py` + `code/` dir — `chmod(0o700)` dir + `chmod(0o600)` files |
| **P1e** | snapshot directory tree — recursive `_harden_tree_modes` (dirs 0o700, files 0o600) |
| **P1f** | 12 sensitive runtime dirs — `ensure_directories` chmod 0o700 |
| **P2** | Aadhaar regex `[\s\-\.]?` — catches dot-separated form |
| **P3** | `cli.py` adds NVIDIA AI Endpoints as option 5 |
| **P4** | smoke test parametrizes NVIDIA |
| **P5** | both `install_phi_redactor` callers wired with `SUBJECT_ID_PATTERNS` |
| **P6** | `_safe_rmtree` symlink guard for snapshot deletion |

### ✅ Closed by PR #11 (`fix/phi-pipeline-polish`)

| Item | Closes |
|---|---|
| **N1** | `run_scrub` fails closed when `phi_scrub.yaml` is absent (was: silent disabled). Dev override: `REPORTALIN_ALLOW_DISABLED_SCRUB=1` |
| **N2** | `_publish_leg` uses `secure_remove_tree` for old trio_bundle (was: plain `shutil.rmtree`) |
| **N3** | Lineage manifest carries `phi_key_fingerprint` (SHA-256 of HMAC key) |
| **N5** | `main.py` zone-asserts `pdf_extractions_dir` at the inlet |
| **N11** | Indo-VAP `IS_SCRNNUM`/`IC_SCRNNUM` covered by `id_fields` rule |
| **N12** | Sandbox `_sandbox_result.json` manifest chmod'd 0o600 (PR #10 missed this) |

### 🚧 Remaining for Phase 3 (architectural)

- **3.A** Mandatory k-anonymity on row-returning tools (`query_dataset`, `cross_reference_variables`)
- **3.B** l-diversity check (kanon_gate.py docstring tracks this gap)
- **3.F + 3.G + 3.H** PDF redaction pipeline (vision API)
- **3.I** Error traceback PHI sanitisation
- **N4** Parallel `main.py` execution race (file-lock design)
- **N6** Atomic publish copytree fallback (proper atomic-write pattern)
- **3.C** Encrypt logs at rest (defer until shared-host deployment)
- **3.D** Auto-reaper for orphaned staging
- **3.E** Narrative NER (Presidio rejected; design choice between custom regex / drop-only)
- **3.J** Streamlit hot-reload session-state hygiene
- **3.K** fcntl-locked log/telemetry writes
- **3.L** 4 IRB operator runbooks (non-code)
- **N7-N10** Doc/tidy items (lineage timing, zone assertions on inputs, etc.)

---

## What's solid (already shipping)

| Surface | Status |
|---|---|
| PHI scrub pipeline (8 actions) | Fully implemented + 104 tests + counts-only audit envelope |
| HMAC key management | mode-0600 sidecar, `secrets.token_bytes(32)` entropy, hard-fail on missing/wrong-mode key |
| Zone enforcement (RED/AMBER/GREEN/GREEN-PROTECT) | Unified chokepoint, symlink-safe via `commonpath`, runtime-enforced |
| Secure staging (AMBER) | mode 0700, umask 0077, zero-fill teardown, optional `/dev/shm` tmpfs |
| Agent boundary gates | `guard_user_prompt` (input) + `phi_safe_return` (output) + clinical allowlist suppresses warn-tier false positives |
| Logging | `PHIRedactingFilter` on root logger, deterministic per-subject HMAC, API-key patterns layered on top, best-effort |
| Conversations | Redacted at write via `redact_message_content`, incremental save preserves prior redactions |
| Sandbox child (PR #2) | Cannot read tmp/ (RED/AMBER); env stripped of API keys + PHI key; AST guards inside child as defense in depth |
| IRB dossier | 31/35 criteria architecturally satisfied; lineage manifest records raw → trio SHA chain; 4 follow-ups documented |
| Lineage manifest | Hashes only, no row content (verified) |
| variables.json schema | No sample values / default values (verified) |
| Agent system prompt | Explicitly forbids surfacing raw paths / IDs (verified) |
| LangChain tracing | Disabled by default in `config.py` (`LANGCHAIN_TRACING_V2=false`) |
| Test fixtures | Synthetic IDs / dates only — no real PHI in tests |

---

## Phase 2.x polish — bundle as PR #9 (~1 day, becomes v0.17.2)

These are 1–10 line code changes + a regression test each. Bundle into a single review-friendly PR.

### File-permission hardening (a "fix-permissions" sub-PR)

Six surfaces write files without explicit `chmod` and inherit umask (typically `0o022 → 0o644`):

| # | File:Line | Current mode | Fix |
|---|---|---|---|
| **P1a** | `scripts/ai_assistant/ui/conversations.py:111, 243, 259` | inherits | `fpath.chmod(0o600)` after each `write_text` |
| **P1b** | `scripts/utils/telemetry.py:56-83` (`_append_event`) | inherits | `sink_path.chmod(0o600)` after write |
| **P1c** | `scripts/ai_assistant/sandbox/__init__.py:148-149` (spec.json in temp dir) | inherits | `spec_path.chmod(0o600)` after write |
| **P1d** | sandbox-persisted `output/{STUDY}/agent/analysis/code/run_*.py` (written from `runner.py`) | inherits | `path.chmod(0o600)` after write; `output_dir.chmod(0o700)` after `mkdir` |
| **P1e** | `scripts/utils/snapshots.py:118-120` (`create_snapshot`) | `0o755` | `target.parent.chmod(0o700)`; recursively chmod restored tree to dirs=0o700, files=0o600 |
| **P1f** | `output/{STUDY}/` directory structure | `0o755` | New `prepare_output_dirs()` helper that creates all output subdirs with mode 0o700 at pipeline init |

### Pattern-coverage gaps

| # | Issue | Fix |
|---|---|---|
| **P2** | `scripts/security/phi_patterns.py:35` AADHAAR regex doesn't match dot-separated `1234.5678.9012` | Change `[\s\-]?` → `[\s\-\.]?` + add adversarial test |
| **P3** | `scripts/ai_assistant/cli.py:31-36` `_PROVIDER_CHOICES` lists 4 providers; UI lists 5 (NVIDIA missing from CLI) | Add `"5": ("nvidia-ai-endpoints", ...)` |
| **P4** | `tests/security/test_llm_construction_smoke.py:43-49` — NVIDIA missing from parametrize | Add `("nvidia-ai-endpoints", "meta/llama-3.3-70b-instruct")` to parametrize |
| **P5** | `scripts/utils/log_hygiene.py::install_phi_redactor(subject_id_patterns=None)` — every caller passes `None`, so per-subject HMAC redaction never fires | Wire `subject_id_patterns=phi_patterns.SUBJECT_ID_PATTERNS` at every install site |

### Quick wins

| # | Issue | Fix |
|---|---|---|
| **P6** | `scripts/utils/snapshots.py:116, 170, 188, 192` use `shutil.rmtree()` — TOCTOU vulnerable to symlink swaps | Replace with `scripts.utils.secure_staging.secure_remove_tree()` (already symlink-safe + zero-fill) |

**PR #9 effort:** ~6 hours including tests + review. Ships as **v0.17.2** (`fix:` Conventional Commit → patch bump).

---

## Phase 3 — architectural work (real design decisions, separate PRs)

### 3.A Mandatory k-anonymity on row-returning tools  *(verified BLOCKER for Phase 3)*

- **Where:** `scripts/ai_assistant/agent_tools.py::query_dataset` (~line 419) and `::cross_reference_variables` (~line 848) return row-level data from trio_bundle but do NOT call `guard_rows_with_kanon`.
- **Why MEDIUM today / BLOCKER for Phase 3:** trio data is already PHI-scrubbed (HMAC pseudonyms, jittered dates, drops), so this isn't raw-PHI exposure — but quasi-identifier combinations (age band + sex + district + outcome) remain a re-identification risk for any external recipient.
- **Recommended approach:** decorator (`@k_anon_required(quasi=("age_band","sex","district"))`) on every row-returning tool; runtime check fails closed. ~30 LoC + a regression test per tool.

### 3.B l-diversity check  *(documented gap in `kanon_gate.py:19-21`)*

- After k-anonymity passes, outcome homogeneity could still re-identify (e.g., all 5+ rows in an equivalence class share `outcome=DIED`).
- **Implementation:** new `l_diversity_check(rows, sensitive=("outcome",), l=2)` in `kanon_gate.py`; wire into `guard_rows_with_kanon` as a follow-on check. ~80 LoC + 10 tests.

### 3.C Encrypt logs at rest

- `.logs/RePORT AI Portal/*.log` files are plaintext, mode 0o644. Redaction reduces severity but doesn't eliminate (subject-ID HMAC tags are joinable to scrubbed dataset rows).
- **Options:** AES-256 at write-time via dedicated handler / encrypted volume (LUKS / FileVault) / ship to a SIEM.
- **Defer until** shared-host or non-laptop deployment.

### 3.D Auto-reaper for orphaned staging

- Mid-pipeline crash leaves plaintext PHI in `tmp/{STUDY}/`. Currently mitigated by mode 0700 + manual cleanup runbook.
- **Fix:** `atexit` handler or background timer that secure-removes any staging dir older than 30 minutes. Documented as opt-in via `--cleanup-on-startup` flag.

### 3.E Narrative NER

- `scripts/security/phi_ner.py` is a documented stub. Free-text inside narrative fields is dropped if the field name matches a drop pattern; not scanned otherwise.
- **Options:** (a) wire up Presidio (rejected once at 22.7% precision per `phi_gate.py:9-13`), (b) custom regex extension, (c) document the limitation in informed-consent and keep the drop-only model.

### 3.F PDF extraction → vision API redaction  *(NEW — verified BLOCKER for Phase 3)*

- **Where:** `scripts/extraction/extract_pdf_data.py:199-385` and lines 393, 444 — raw PDF bytes are base64-encoded and sent directly to Anthropic / Google APIs **before** any redaction.
- **Current scope:** MEDIUM (PDFs are operator-attested PHI-free + env flag gate). **Phase 3 scope:** BLOCKER.
- **Fix options:** in-memory PDF redaction before upload (call `phi_scrub.scrub_text()` on extracted PDF text); or PDF-specific redaction filter; or `--pdf-redaction` flag for opt-in redacted-only extraction.

### 3.G Vision API response PHI scanning  *(NEW — HIGH)*

- **Where:** `scripts/extraction/extract_pdf_data.py:476-493` — JSON returned from Claude / Gemini is parsed directly without scanning `description`/`summary` fields for PHI.
- **Risk:** if the LLM echoes a subject ID from the PDF, it persists unredacted in `_variables.json`.
- **Fix:** after JSON parse, iterate top-level `variables` and `sections`, calling `phi_safe.sanitise_text()` on string values before returning.

### 3.H Vision API retry idempotency  *(NEW — MEDIUM)*

- **Where:** `scripts/extraction/extract_pdf_data.py:828-835` — no retry logic today. If retry were added later via tenacity, full PDF bytes would be re-shipped on every retry.
- **Fix (preventive):** content-hash-based idempotent retry. Before retry, check whether prior JSON output exists with matching input PDF hash; if match, skip re-upload.

### 3.I Error traceback PHI sanitisation  *(NEW — HIGH for Phase 3)*

- **Where:** `scripts/utils/errors.py:107-118` (`wrap()`) — Python tracebacks include local variable `repr()` from frame headers. If an exception is raised while processing `{'SUBJID': 'SUBJ_abc123', ...}`, that dict's repr() lands in the traceback string.
- **Fix:** sanitise tracebacks in `wrap()`:
  1. Strip local variable values from frame headers (keep only names).
  2. Redact file paths to relative-from-project-root format.
  3. Pass the result through `_mask_phi(tb)` before storing.

### 3.J Streamlit hot-reload session-state hygiene  *(NEW — MEDIUM for dev environments)*

- **Where:** `scripts/ai_assistant/ui/state.py:12-56` (`init_state`) + `scripts/ai_assistant/keystore.py:145-164`.
- **Issue:** when Streamlit hot-reloads, session_state survives. The `rpln_keystore` object's `_keys` dict (with API keys) remains in memory and is NOT cleared.
- **Fix:** in `init_state()`, detect hot-reload via a sentinel and explicitly call `ss['rpln_keystore'].clear()` to wipe in-memory keys.

### 3.K File-locked log + telemetry writes  *(NEW — LOW)*

- **Where:** `scripts/utils/log_hygiene.py` + `scripts/utils/telemetry.py`.
- **Issue:** multiple Streamlit reruns may trigger concurrent log/telemetry writes. Append-mode writes are atomic on Linux but not guaranteed on macOS.
- **Fix:** document the limitation; for production, use `fcntl.flock` (Linux) / `msvcrt.locking` (Windows) for cross-platform locked writes.

### 3.L IRB conformance follow-ups  *(operator runbooks, not code)*

From `docs/irb_dossier/conformance_matrix.md:80`:
- **1.6** — district population ≥20k geography drop (Safe Harbor)
- **4.2** — pdfplumber hybrid extraction
- **5.3** — 72-hour breach notification runbook
- **5.5** — `consent-scope.yaml` (operator-owned IEC-approved field allowlist)

---

## LOW-severity / nice-to-have

- **L1** — `scripts/utils/step_cache.py:148-160` `extra_metadata` field stores arbitrary metadata plaintext. Document that it must not contain PHI / quasi-identifiers; consider a schema validator.
- **L2** — `scripts/ai_assistant/ui/providers.py:175, 193` Ollama localhost probes are unlogged. Add a debug log line; if Ollama ever supports remote, enforce HTTPS + cert pinning.
- **L3** — No active pre-commit hook for secret scanning. Add a hook that `grep`s staged diffs for `sk-`, `sk-ant-`, `nvapi-`, `AIza` patterns.
- **L4** — HMAC key stored as plaintext hex sidecar (mode 0o600). Documented design choice; the only at-rest protection is OS-level FDE (FileVault / LUKS). Document in operator runbook: "Ensure `~/.config` lives on an encrypted volume."
- **L5** — `tests/security/test_adversarial_phi_safe.py::test_aadhaar_with_dot_separators_evades_gate_known_gap` — assertion currently expects `result.ok is True` (documenting the gap). Once P2 ships, flip the assertion.

---

## NOT findings (verified compliant)

| Surface | Status |
|---|---|
| `LANGCHAIN_TRACING_V2` | Disabled |
| `LANGSMITH` / `LANGFUSE` / `SENTRY` / `WANDB` etc. | None wired |
| Lineage manifest | Hash-only, no row content |
| Dataset cleanup audit | Counts only |
| variables.json | No sample values |
| PDF storage | JSON only, never original PDFs |
| Agent system prompt | Explicitly forbids surfacing raw paths / IDs |
| Streamlit `query_params` | Only used for shutdown control signal |
| Test fixtures | Synthetic data only, no real PHI |
| `step_cache` manifest writes | Atomic (tempfile + rename) |

---

## Inconsistencies surfaced (silent footguns, fixed by the polish PR)

- **Provider list divergence**: cli.py (4) ≠ providers.py (5) ≠ keystore.py (4 keyed). → P3 closes.
- **k-anon helper exists but unused in production code**: only test files reference `guard_rows_with_kanon`. → 3.A closes.
- **Subject-ID patterns defined but not wired into log redactor**: `phi_patterns.SUBJECT_ID_PATTERNS` exists; `install_phi_redactor()` callers pass `None`. → P5 closes.
- **File modes inconsistent across writers**: conversations / telemetry / sandbox / snapshots / output — six writers all inherit umask. → P1a-f close.

---

## Recommended sequencing

1. **PR #9 — "Permissions + polish"** (~6 hours): bundles P1a-f + P2-P6. Ships as v0.17.2. Closes the file-mode footguns + dot-separated Aadhaar + provider list inconsistency + smoke-test gap + log-redactor wiring + symlink-safe snapshots.
2. **PR #10 — "Mandatory k-anon (3.A) + l-diversity (3.B)"**: same kanon_gate file family. The most impactful BLOCKER for any Phase 3 external scope.
3. **PR #11 — "PDF redaction pipeline (3.F + 3.G + 3.H)"**: redact PDFs before LLM upload, scan response, idempotent retry. Real architectural work for the vision-API path.
4. **PR #12 — "Traceback sanitiser (3.I)"**: short, focused; sanitises Python tracebacks before they land in logs / agent / conversations.
5. **Defer (3.C, 3.D, 3.E, 3.J, 3.K)** until either the deployment shape changes or specific operator demand surfaces.
6. **Operator-owned (3.L)**: schedule the 4 runbook drafts as a separate doc-only effort.

Until items 1–4 ship, the honest external statement is: *"PHI architecture is Phase-2-ready for single-operator IRB-approved scope; Phase 3 work needed before external deployment or multi-tenant use."*
