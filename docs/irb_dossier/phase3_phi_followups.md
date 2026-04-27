# PHI Handling — Phase 2.x Polish + Phase 3 Implementation Plan

**Audit date:** 2026-04-27 (post v0.17.1)
**Author of plan:** in-house security audit
**Scope:** PHI-handling gaps surfaced by deep audit + inconsistency hunt across phi_safe, phi_scrub, phi_gate, kanon_gate, log_hygiene, agent_tools, conversations, sandbox.

---

## Executive answer

> **Is PHI complete + fully functional with zero security flaw as of v0.17.1?**

**No.** The PHI architecture is solid (4-tier zone model enforced; 8-action scrub catalog implemented; HMAC pseudonymization with mode-0600 sidecar; agent-boundary input/output gates; redacted conversations; subprocess-isolated sandbox; pip-audit clean) — but a deep audit found **3 verified items that are BLOCKERS for Phase 3 deployment** plus 2 polish items.

For the **current scope** (single-operator, single-study Indo-VAP, IRB-approved internal use), these are MEDIUM/HIGH not BLOCKER — the operator is the trusted recipient, no external party sees raw outputs, and the data being returned is already PHI-scrubbed (HMAC pseudonyms + date jitter + drops).

For **any external collaboration / multi-tenant / cloud-hosted scope**, all three become BLOCKERS and must be closed before that scope is exercised.

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

---

## Phase 2.x polish — small fixable items (~1 PR, ~6 hours)

These are 1–10 line code changes + a test each. Bundle as **PR #9** and ship as **v0.17.2**.

### P1. Conversation file mode = world-readable

- **Where:** `scripts/ai_assistant/ui/conversations.py:111, 243, 259` — three `write_text(json.dumps(...))` calls, no `chmod(0o600)` follow-up.
- **Risk:** files inherit process umask (typically 0o022) → world-readable. Conversations include redacted user prompts + tool returns.
- **Fix:** add `fpath.chmod(0o600)` after each write.
- **Test:** write a conversation, assert `stat.S_IMODE(fpath.stat().st_mode) == 0o600`.

### P2. Dot-separated Aadhaar escape

- **Where:** `scripts/security/phi_patterns.py:35` regex `\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b` doesn't match `1234.5678.9012`.
- **Risk:** real Aadhaar numbers do appear with dots in PDFs; the input gate lets them through.
- **Fix:** change separator class to `[\s\-\.]?`.
- **Test:** add adversarial case to `tests/test_phi_safe_input_gates.py` AND flip the existing "known gap" assertion in `tests/security/test_adversarial_phi_safe.py::test_aadhaar_with_dot_separators_evades_gate_known_gap`.

### P3. CLI missing NVIDIA provider

- **Where:** `scripts/ai_assistant/cli.py:31-36` `_PROVIDER_CHOICES` lists only 4 providers.
- **Risk:** UI's `_PROVIDER_CONFIG` lists 5 (incl. NVIDIA AI Endpoints); KeyStore supports NVIDIA. CLI users can't pick it. Surface inconsistency = silent footgun for any future feature gating on provider list.
- **Fix:** add `"5": ("nvidia-ai-endpoints", "NVIDIA AI Endpoints", "NVIDIA_API_KEY", "meta/llama-3.3-70b-instruct")`.
- **Test:** assert NVIDIA option present in `tests/test_cli.py`.

### P4. NVIDIA missing from LLM construction smoke test

- **Where:** `tests/security/test_llm_construction_smoke.py:43-49` parametrize covers anthropic / openai / google-genai only.
- **Risk:** a future `langchain-nvidia-ai-endpoints` 1.x regression would not be caught at PR-time (this is exactly the failure mode that broke v0.17.0).
- **Fix:** add `("nvidia-ai-endpoints", "meta/llama-3.3-70b-instruct")` to the parametrize.

### P5. Subject-ID HMAC patterns may not be wired into log redactor

- **Where:** `scripts/utils/log_hygiene.py::install_phi_redactor(subject_id_patterns=None)` — caller(s) likely pass `None`, so the per-subject HMAC redaction never fires; only generic catalog redactions run.
- **Risk:** subject-ID identifiers in log messages (which `phi_patterns.SUBJECT_ID_PATTERNS` would catch) are not HMAC-redacted.
- **Fix:** find every `install_phi_redactor()` call site (likely in `main.py`); pass `subject_id_patterns=phi_patterns.SUBJECT_ID_PATTERNS`.
- **Test:** log a message containing a SUBJID; assert it's redacted to `<SUBJ_*>`.

---

## Phase 3 — architectural work

These are real design decisions, not one-line fixes. Each justifies its own PR.

### 3.A Mandatory k-anonymity on row-returning tools  *(verified BLOCKER for Phase 3)*

- **Where:** `scripts/ai_assistant/agent_tools.py::query_dataset` (line ~419) and `::cross_reference_variables` (line ~848) return row-level data from trio_bundle but do NOT call `guard_rows_with_kanon`.
- **Why MEDIUM today / BLOCKER for Phase 3:** the trio data is already PHI-scrubbed (HMAC pseudonyms, jittered dates, drops), so this is not raw-PHI exposure — but quasi-identifier combinations (age band + sex + district + outcome) remain a re-identification risk for any external recipient.
- **Design options:**
  - (a) **Decorator approach:** `@k_anon_required(quasi=("age_band","sex","district"))` decorator on every row-returning tool; runtime check fails closed.
  - (b) **Registry approach:** `tools/registry.yaml` lists which tools are row-returning + their quasi-identifier sets; CI fails if a row-returning tool isn't registered.
  - (c) **Tool-class approach:** introduce `RowReturningTool` base class that wraps return values through `guard_rows_with_kanon` automatically.
- **Recommendation:** (a) — least architectural disruption, ~30 LoC + a regression test per tool.

### 3.B l-diversity check  *(documented gap in `kanon_gate.py:19-21`)*

- After k-anonymity passes, outcome homogeneity could still re-identify (e.g., all 5+ rows in an equivalence class share `outcome=DIED`).
- **Implementation:** new `l_diversity_check(rows, sensitive=("outcome",), l=2)` in `kanon_gate.py`; wire into `guard_rows_with_kanon` as a follow-on check. ~80 LoC + 10 tests.

### 3.C Encrypt logs at rest

- `.logs/RePORT AI Portal/*.log` files are plaintext, mode 0o644 (umask default). Redaction reduces severity but doesn't eliminate (subject-ID HMAC tags are joinable to scrubbed dataset rows).
- **Options:** (a) AES-256 at write-time via dedicated handler, (b) encrypted volume (LUKS / FileVault), (c) ship to a SIEM.
- **Defer until** shared-host or non-laptop deployment.

### 3.D Auto-reaper for orphaned staging

- Mid-pipeline crash leaves plaintext PHI in `tmp/{STUDY}/`. Currently mitigated by mode 0700 + manual cleanup runbook.
- **Fix:** background reaper (cron / systemd timer / pipeline-startup check) that secure-removes any staging dir older than 30 minutes.

### 3.E Narrative NER

- `scripts/security/phi_ner.py` is a documented stub. Free-text inside narrative fields is dropped if the field name matches a drop pattern; not scanned otherwise.
- **Options:** (a) wire up Presidio (rejected once at 22.7% precision per `phi_gate.py:9-13`), (b) custom regex extension, (c) document the limitation in informed-consent and keep the drop-only model.

### 3.F IRB conformance follow-ups  *(operator runbooks, not code)*

From `docs/irb_dossier/conformance_matrix.md:80`:
- **1.6** — district population ≥20k geography drop (Safe Harbor)
- **4.2** — pdfplumber hybrid extraction
- **5.3** — 72-hour breach notification runbook
- **5.5** — `consent-scope.yaml` (operator-owned IEC-approved field allowlist)

---

## Inconsistencies surfaced by the audit (not full gaps, but worth noting)

- **Provider list divergence**: cli.py (4) ≠ providers.py (5) ≠ keystore.py (4 keyed). Item P3 closes this.
- **k-anon helper exists but is unused in production code**: only test files reference `guard_rows_with_kanon`. Items 3.A closes this.
- **Subject-ID patterns defined but possibly not wired**: `phi_patterns.SUBJECT_ID_PATTERNS` exists; need to confirm every `install_phi_redactor()` call seeds it. Item P5 closes this.

---

## Recommendation

1. **Ship PR #9 (Phase 2.x polish)** within the next session. Closes 5 items in ~6 hours. Bumps to v0.17.2 (`fix:` Conventional Commit).
2. **Plan PR #10 — 3.A (mandatory k-anon)** as the first Phase 3 PR; this closes the most-impactful BLOCKER for any external/multi-tenant scope.
3. **Defer 3.C (log encryption) and 3.D (staging reaper)** until the deployment shape changes (shared host, cloud, or real PHI loaded into this checkout — currently `data/raw/` is empty).
4. **Implement 3.B (l-diversity)** in tandem with 3.A (same kanon_gate file, same test family).
5. **Operator-owned (3.F)**: schedule the 4 runbook drafts as a separate doc-only effort.

Until items 1–5 ship and 3.A is implemented, the honest external statement is: *"PHI architecture is Phase-2-ready for single-operator IRB-approved scope; Phase 3 work needed before external deployment or multi-tenant use."*
