# Plan: SoT AFK-by-Default, HITL-by-Exception Pipeline

Status: ACTIVE. Owner: Solomon S Joseph. Plan version: 1.0 (2026-05-18).

This plan is followed in strict order. No phase begins until the prior phase's
acceptance gate is green. No work is performed inside a phase that is not
listed in that phase's deliverables. Deviations require an explicit written
amendment to this file before any code is touched.

## Goal

Convert the current SoT generation flow into an **AFK-by-default, HITL-by-exception**
pipeline. A qualified human attests each form **once** (the anchor ceremony) and
afterwards is only contacted when the system detects a genuinely novel change that
the deterministic gates and adversarial graders cannot resolve.

Compliance bar: 21 CFR Part 11, ICH-E6(R3), GCP. Every promoted YAML must trace to
either (a) a fresh qualified-signatory attestation, or (b) an unbroken chain of
zero-diff regenerations from an attested gold.

## Operating Rules (apply to every phase, no exceptions)

1. **No phase skipping.** Phases 1 → 6 in strict order. A phase is "done" only when
   its acceptance gate is green and recorded in the Phase Status Log at the bottom
   of this file.
2. **No out-of-scope work inside a phase.** Each phase has a deliverables list.
   Anything not on the list is deferred to a later phase or out of plan. If
   something looks necessary that isn't listed, stop and amend the plan.
3. **No human sign-off can be replaced by automation in Tier-A.** Tier-A forms
   always require a named human signatory per regeneration that produces a
   non-empty diff.
4. **No gold may be modified silently.** A gold YAML changes only via the Anchor
   or Re-anchor workflow with electronic signature.
5. **No promotion without all gates green.** Promotion to
   `output/<study>/llm_source/source_truth/` requires: deterministic verifier
   pass + property validator pass + diff-against-gold resolved + (if Tier-A)
   signed attestation.
6. **Model and seed pinning.** Every machine-produced artifact records the model
   ID, model version, and seed/temperature in the audit bundle. No "latest" tags.
7. **Per-phase model profile.** Opus 4.7 for planning/review, Sonnet 4.6 for
   execution agents, Haiku 4.5 for test scaffolding. Enforced when dispatching
   agents.
8. **No Claude attribution** in commits, code, or comments.
9. **No destructive operations without rollback.** `git reset --hard`, `rm -rf`
   on tracked paths, force pushes — all require an explicit rollback procedure
   documented in the same change.
10. **Cross-LLM portability is mandatory.** Every new capability must be invokable
    from a non-Claude LLM via CLI + AGENTS.md, not only via the Claude `Skill`
    tool.

## Definitions

- **Gold** — a frozen, human-attested lean YAML at
  `data/SoT/<study>/<form>_policy.lean.yaml` with a corresponding signed
  attestation record at
  `data/SoT/<study>/_attestations/<form>.attestation.json`.
- **Candidate** — a freshly generated lean YAML at `/tmp/<form>_lean.yaml`, not
  yet promoted.
- **Promoted** — a lean YAML at
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml`, written only
  after all gates pass.
- **Tier-A** — forms whose variables drive PHI handling, safety/AE signals,
  eligibility, or primary/secondary endpoints. Examples on Indo-VAP: 6_HIV,
  anything with `phi:` fields, any AE form, any visit-completion form whose
  dates gate outcome windows.
- **Tier-B** — forms with administrative metadata only, no PHI, no endpoint
  variables.
- **Tier-C** — forms purely structural (e.g. lab-instrument calibration records).
- **Cosmetic diff** — whitespace only, key reordering only, equivalent phrasing
  that the typed semantic model normalizes to the same value.
- **Within-rule diff** — matches a known correction pattern catalogued under
  `data/SoT/_diff_patterns/<pattern-id>.yaml`.
- **Novel diff** — anything else. Always queues for human review regardless of
  tier.

## Roles

- **Machine** (this pipeline): generation, deterministic validation, adversarial
  verify, diff classification, audit bundle assembly, queue management.
- **Qualified signatory** (clinician or data manager named in IRB
  documentation): anchor attestation, novel-diff review, incident root-cause
  sign-off.

## Audit Bundle Requirements

For every regeneration of every form, the machine writes
`output/<study>/_audit/<form>/<run-id>/`:

- `manifest.json`: model IDs/versions, seeds, temperature, run start/end, git
  SHA, PDF SHA-256, dataset SHA-256, signatory (if applicable), tier.
- `source_pack.json`: copy of Stage 0 output.
- `render.png`: copy of Stage 0 render.
- `exhaustive.yaml`: Stage 1–2 output.
- `lean.yaml`: Stage 3 output (the candidate).
- `verifier.log`: Stage 4 stdout/stderr + exit code.
- `validator.log`: property validator stdout/stderr + exit code.
- `graders/<grader-id>.json`: each adversarial grader's structured discrepancy
  list with crop citations.
- `diff_vs_gold.json`: structured diff classification.
- `decision.json`: final outcome (auto-promoted / queued / blocked) with reason.
- `attestation.json` (Tier-A only): signatory, timestamp, e-signature payload,
  reason.

Audit bundle retention: indefinite, write-once. No bundle is modified or deleted
by the pipeline.

---

## Phase 1 — Deterministic Foundation (no LLM, no human required)

**Why this phase first:** The cheapest, most reliable gains are property-based
invariants and gold diffs. They catch the bulk of defects before any LLM or
human is involved. This phase has no external dependencies and no compliance
ambiguity.

### Deliverables (do exactly these, nothing more)

**1.1** Extend `scripts/ai_assistant/sot_loader.py` with a
`validate(data: dict) -> ValidationReport` function. The report is a typed
dataclass with `passed: bool` and `errors: list[ValidationError]`. Invariants
enforced:

- Every variable's `section` resolves to a key in `sections`.
- Every `skip_logic` clause that names another variable (regex
  `[A-Z][A-Z0-9_]+`) resolves to a key in `variables`.
- Mutex reciprocity: if `A.skip_logic` contains `mutually exclusive with B`,
  then `B.skip_logic` must contain `mutually exclusive with A`.
- Every `arrows[].from.variable` and `arrows[].to.variable` (when not null)
  resolves to a key in `variables`.
- Every `arrows[].from.option` (when present) appears in the source variable's
  `options` (when the source variable has `options`).
- Every `instructions[].id` referenced in any `skip_logic` clause matches an
  actual entry in `instructions`.
- Every `free_text` typed variable has either explicit `phi:` set or explicit
  `notes:` containing the literal substring "no PHI expected" with an explicit
  reason.
- Every variable with `phi: jitter_date` has a column name matching the
  allowlist regex `(_COMPDAT|_VISIT|_SIGNDAT|_ENTDAT)$`. Allowlist editable
  only via plan amendment.
- Every variable with `phi: drop` has `type` in
  `{signature, initials, datetime}`.
- Every variable with `phi: pseudonymize` is either typed `identifier` or typed
  `code` with a `notes:` field documenting the quasi-identifier rationale.

**1.2** Wire `validate()` into
`skills/sot-lean-generator/scripts/check_lean_policy.py`:

- After existing verifier passes, run `validate()` on the loaded YAML.
- On failure: print errors to stderr in the same format as existing errors;
  exit 1.
- On success: append "Property validator passed" line; exit 0.

**1.3** Freeze current state as gold. For each
`output/Indo-VAP/llm_source/source_truth/*_policy.lean.yaml` that currently
exists (initially only `6_HIV_policy.lean.yaml`):

- Copy to `data/SoT/Indo-VAP/<form>_policy.lean.yaml` if not already there.
- Generate `data/SoT/Indo-VAP/_attestations/<form>.attestation.json` with
  `status: "pre-plan"`, `signatory: null`, `attested: false`. These are NOT
  real attestations — they are placeholders that will be replaced in Phase 6.
  They explicitly mark the gold as "needs anchor."

**1.4** Build `scripts/source_truth/diff_against_gold.py` CLI:

- Args: `--study`, `--form`, `--candidate <path>`.
- Loads candidate and gold (from
  `data/SoT/<study>/<form>_policy.lean.yaml`).
- Produces structured diff at `/tmp/diff_<form>.json` with three fields:
  `cosmetic: list`, `within_rule: list`, `novel: list`. In Phase 1,
  classification is stub: only `cosmetic` detection is implemented —
  whitespace-only and key-order-only. Within-rule and novel are placeholders
  for Phase 4.
- Exit 0 if all diffs are cosmetic OR diff is empty. Exit 1 otherwise.

**1.5** Add Makefile target `sot-validate STUDY=… FORM=…` that runs validator
+ verifier + diff-against-gold in one command. Failure on any of the three is
a hard fail.

**1.6** Tests at `tests/skills/sot-lean-generator/`:

- `test_validate_invariants.py`: one unit test per invariant in 1.1, each with
  a passing input and a failing input.
- `test_diff_against_gold.py`: cosmetic diff returns exit 0; substantive diff
  returns exit 1.

### Acceptance gate (machine-checkable)

```
make sot-validate STUDY=Indo-VAP FORM=6_HIV
uv run --all-groups python -m pytest tests/skills/sot-lean-generator/ -v
```

Both must exit 0. The validator test file must have ≥1 passing test per
invariant in 1.1.

### Out of scope in Phase 1

- Any LLM calls.
- Within-rule diff classification.
- Adversarial graders.
- Tier assignment.
- Audit bundle writer.
- Anchor ceremony.

---

## Phase 2 — Risk Stratification (no LLM)

**Why next:** Tier classification gates every later decision. Without it, the
diff queue can't route, and the audit bundle can't tag attestation
requirements.

### Deliverables

**2.1** Create `data/SoT/<study>/_tiers.yaml` for each study (initially
Indo-VAP). Format:

```yaml
forms:
  6_HIV:
    tier: A
    reason: "PHI fields + HIV/CD4 drive endpoint and PHI policy"
  # ...
```

**2.2** Classify all 28 Indo-VAP forms by walking the form list and the
variable PHI catalog. Rule:

- Any form with ≥1 `phi:` field → Tier-A.
- Any AE / safety / SAE / pregnancy / death form → Tier-A.
- Any form whose variables are referenced in the primary/secondary endpoint
  definition (per protocol section, look in `docs/protocols/` if present,
  otherwise default to Tier-A for any HIV/TB/treatment-outcome/visit-completion
  form) → Tier-A.
- Forms with only administrative metadata, no PHI, no endpoint variables →
  Tier-B.
- Forms with purely structural/calibration content → Tier-C.
- Default if uncertain: Tier-A. Never default to lower tier.

**2.3** Extend `scripts/ai_assistant/sot_loader.py` with
`get_tier(study: str, form: str) -> Literal["A", "B", "C"]`.

**2.4** Extend `check_lean_policy.py` to print the resolved tier as part of
its success line.

**2.5** Tests: `test_tier_resolution.py` — every Indo-VAP form returns a
tier; default behavior on unknown form is Tier-A.

### Acceptance gate

```
uv run --all-groups python -c "from scripts.ai_assistant.sot_loader import get_tier; \
  forms = ['6_HIV', ...all 28...]; print({f: get_tier('Indo-VAP', f) for f in forms})"
```

Returns a dict where every form has a tier and no form is unknown.
`_tiers.yaml` committed.

### Out of scope in Phase 2

- Any LLM calls.
- Diff classifier upgrade.
- Anchor ceremony.

---

## Phase 3 — Audit Bundle Writer (no LLM)

**Why next:** Adversarial graders and diff queue both produce audit material.
The writer must exist before either generates output, or the material is lost.

### Deliverables

**3.1** Create `scripts/source_truth/audit_bundle.py` with class
`AuditBundle`:

- `__init__(study, form, run_id)`: creates
  `output/<study>/_audit/<form>/<run-id>/`.
- `attach_source_pack(path)`, `attach_render(path)`,
  `attach_exhaustive(path)`, `attach_lean(path)`,
  `attach_verifier_log(text, exit_code)`,
  `attach_validator_log(text, exit_code)`,
  `attach_grader(grader_id, payload_dict)`, `attach_diff(diff_dict)`,
  `attach_decision(outcome, reason)`.
- `attach_attestation(signatory, signature_payload, reason)` — Tier-A only.
- `write_manifest()`: assembles the manifest.json with all required metadata
  (run_id is a UUID4; git SHA from `git rev-parse HEAD`; PDF/dataset SHA-256
  computed by the writer; model IDs/seeds passed in by caller).
- Bundle is write-once: each `attach_*` call refuses to overwrite an existing
  file.

**3.2** Wire the existing skill scripts (`extract_sources.py`,
`check_lean_policy.py`, and the new `diff_against_gold.py`) to accept an
optional `--audit-bundle <dir>` flag and call the matching `attach_*` method
when present.

**3.3** Update `scripts/source_truth/study_intake.py` wrapper to create a
new `AuditBundle` per invocation by default and pass it through to each
stage.

**3.4** Tests: `test_audit_bundle.py` — bundle creation, write-once
enforcement, manifest schema validation, SHA computation.

### Acceptance gate

`make sot-source-pack STUDY=Indo-VAP FORM=6_HIV` produces a populated
`output/Indo-VAP/_audit/6_HIV/<run-id>/` with at minimum manifest, source
pack, render, and a partial verifier log. All required manifest fields
populated.

### Out of scope in Phase 3

- Any LLM calls.
- Diff classifier upgrade.
- Adversarial graders.

---

## Phase 4 — Diff Triage Classifier (no LLM in the classifier itself)

**Why next:** Adversarial graders only need to run when the diff is novel.
Cosmetic and within-rule diffs auto-resolve. Without a working classifier, the
system defaults to running expensive LLM verify on every regeneration.

### Deliverables

**4.1** Upgrade `scripts/source_truth/diff_against_gold.py` classification:

- **Cosmetic**: whitespace, key order, equivalent string normalization
  (lowercase punctuation differences in widget prose where the typed semantic
  model produces the same parsed value).
- **Within-rule**: matches a pattern in `data/SoT/_diff_patterns/*.yaml`.
  Each pattern file:
  ```yaml
  pattern_id: mutex-symmetry-correction
  description: "Writer added reciprocal half of a mutex relationship"
  trigger:
    diff_kind: skip_logic_change
    direction: bidirectional_added
  action: auto_accept_with_log
  ```
- **Novel**: anything else.

**4.2** Create `data/SoT/_diff_patterns/` with the initial pattern
catalogue (empty, seeded only when real diffs appear; never seeded
preemptively).

**4.3** Build `scripts/source_truth/promote.py`:

- Args: `--study`, `--form`, `--candidate`, `--audit-bundle`.
- Reads tier. Reads diff classification.
- Decision matrix:

  | Tier | Empty diff | Cosmetic only      | Within-rule        | Novel            |
  |------|------------|--------------------|--------------------|------------------|
  | A    | promote    | promote, log       | queue              | queue + block    |
  | B    | promote    | promote, log       | promote, log       | queue            |
  | C    | promote    | promote, log       | promote, log       | queue            |

- Promotion copies candidate to
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml`.
- Queueing writes a card to
  `data/SoT/_review_queue/<study>_<form>_<run-id>.card.yaml`.
- Blocking refuses to promote and exits non-zero.
- Attestation requirement (Tier-A non-empty diff): refuse to promote unless
  `--attestation <path>` is supplied with a signed attestation file.

**4.4** Define the queue card format at
`data/SoT/_review_queue/_schema.yaml`: includes diff summary, PDF crop
references, recommended action, tier, audit-bundle pointer, sign-off
requirement.

**4.5** Tests: `test_promote.py` covers every cell of the decision matrix
with synthetic candidate/gold pairs.

### Acceptance gate

For each cell of the Tier × diff-class matrix, the test demonstrates the
correct promotion decision. Real-world smoke: regenerating 6_HIV with no
changes auto-promotes; regenerating with a synthetic novel change blocks and
queues.

### Out of scope in Phase 4

- Adversarial graders.
- The human review UI/CLI (queue file is the surface; rendering it is Phase 6).
- Anchor ceremony.

---

## Phase 5 — Adversarial Verify (LLM stage, runs only on novel diffs)

**Why next:** With Phase 1–4 in place, the LLM verifier is only invoked on the
small fraction of diffs classified as novel. Cost stays bounded.

### Deliverables

**5.1** Create `skills/sot-lean-generator/scripts/run_adversarial_verify.py`
CLI:

- Args: `--exhaustive <path>` (NOT the lean YAML — verify pre-trim, where
  evidence anchors still exist), `--render <png>`, `--source-pack <json>`,
  `--audit-bundle <dir>`, `--graders <comma-list>`.
- Available grader types:
  - `g-pixel-claude`: Claude grader; visual fidelity; required output
    includes `cited_crop_bbox` for every claim.
  - `g-pixel-gemini`: Gemini grader; visual fidelity; same citation
    requirement. Different model family for true adversarial diversity.
  - `g-inference-claude`: clinical-logic inference; required to cite both
    pixel evidence and the rule name it instantiates.
- Each grader runs in its own subprocess with a fresh prompt and no writer
  history.
- Each grader output is written to
  `<audit-bundle>/graders/<grader-id>.json`.

**5.2** k-of-n agreement logic:

- Run each grader N=3 times with temperature 0.3 (small variance), each run
  records its discrepancy list.
- For each grader type, the consensus discrepancy list is the intersection of
  ≥2 of 3 runs (k=2-of-3). Discrepancies appearing in only 1 run are logged
  but not enforced.
- A grader "passes" when its consensus list is empty.
- Promotion-eligible when all grader types pass for two consecutive full
  invocations (writer fix loop in between).

**5.3** Writer fix loop:

- Up to 3 fix rounds. Each round: writer reads the union of consensus
  discrepancy lists, applies fixes to the exhaustive YAML, re-runs lean trim,
  re-runs Stage 4 + validator + adversarial verify.
- Round budget exhausted with non-empty consensus → hard block, escalate to
  human queue.

**5.4** Add to `promote.py`: when diff classification is novel AND tier
permits auto-promotion, the orchestrator must first invoke
`run_adversarial_verify.py` and only proceed if it returns exit 0.

**5.5** Cross-LLM portability requirement: the script must run via plain
`uv run --all-groups python …`. API keys for Claude and Gemini come from
`config.py` / environment, not from the Claude Code session. A non-Claude
tool must be able to invoke this and get the same result.

**5.6** Tests: `test_adversarial_verify.py` with mocked grader subprocesses
exercising k-of-n logic, citation enforcement, fix-round budget, and queue
escalation on budget exhaustion.

### Acceptance gate

Regenerate 6_HIV after deliberately introducing one defect in the exhaustive
YAML. The adversarial verify catches it (consensus discrepancy non-empty), the
writer fixes it, the second round comes back empty, promotion proceeds. A test
with a defect the graders cannot resolve (e.g., a cross-form-only
relationship) results in a queued card with the audit bundle intact.

### Out of scope in Phase 5

- Anchor ceremony.
- Queue UI (still file-based).

---

## Phase 6 — Anchor Ceremony and Operational Loop

**Why last:** Anchoring is meaningless until the machinery exists to detect
when a re-anchor is needed. Operating the loop requires all prior phases.

### Deliverables

**6.1** Build `scripts/source_truth/anchor.py` CLI:

- Args: `--study`, `--form`, `--signatory <name>`, `--reason <text>`.
- Loads the current
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml`.
- Validates with Stage 4 + property validator.
- Requires a terminal e-signature ceremony: prompts the signatory for a
  per-attestation passphrase, computes HMAC-SHA-256 over the lean YAML +
  audit-bundle manifest, writes to
  `data/SoT/<study>/_attestations/<form>.attestation.json`:
  ```json
  {
    "signatory": "...",
    "signatory_role": "...",
    "tier": "A",
    "lean_yaml_sha256": "...",
    "audit_bundle_run_id": "...",
    "signed_at": "ISO-8601 UTC",
    "hmac": "...",
    "reason": "...",
    "status": "attested"
  }
  ```
- Copies the lean YAML to
  `data/SoT/<study>/<form>_policy.lean.yaml` (this is the frozen gold).

**6.2** Build `scripts/source_truth/review_queue.py` CLI for batch review:

- `list`: show all pending queue cards with summary + tier.
- `show <card-id>`: render diff, citations, recommended action.
- `approve <card-id> --signatory <name>`: Tier-A requires anchor-style
  signature; Tier-B/C just records signatory.
- `reject <card-id> --reason <text>`: removes candidate, retains audit
  bundle.

**6.3** Conduct first-anchor ceremony for all Tier-A Indo-VAP forms. This is
a manual session with the qualified signatory. Each anchor invocation
produces a real attestation file. The placeholder attestations from Phase 1
are replaced.

**6.4** Document the operational loop at `docs/runbook_sot_operations.md`:

- Daily/weekly: `make sot-regenerate-all STUDY=Indo-VAP` runs all forms,
  most auto-promote, some queue.
- Quarterly: signatory opens the queue, reviews cards in batch, signs/rejects.
- Incident: when graders systematically disagree, investigate before changing
  rules.

**6.5** Add `make sot-regenerate-all STUDY=…` target that loops every form
in `data/SoT/<study>/` through Stage 0 → 5 → diff → promote (or queue).

### Acceptance gate

- All Tier-A Indo-VAP forms have attestation files with `status: "attested"`.
- `make sot-regenerate-all STUDY=Indo-VAP` runs end to end. Forms with no
  diff auto-promote; forms with novel diffs (none expected on a clean
  re-run) would queue, demonstrating the path works.
- `docs/runbook_sot_operations.md` committed.

### Out of scope (and out of plan entirely)

- A graphical UI for the review queue. CLI is sufficient.
- Multi-study orchestration. One study at a time.
- Model-upgrade automation. New model = Re-anchor workflow, which is a
  separate plan amendment.

---

## Plan Amendment Procedure

Any deviation from this plan requires:

1. A new section appended to this file titled
   "Amendment N — <YYYY-MM-DD> — <topic>".
2. A description of what changed and why.
3. The amendment must be committed BEFORE the deviating code change.
4. The phase acceptance gate is re-evaluated against the amended plan.

Amendments are append-only. The original plan text is never rewritten.

## Phase Status Log

Append one line per phase transition. Format:
`Phase N: <pending|in-progress|green|blocked> | <YYYY-MM-DD> | <commit SHA> | <signatory or "machine"> | <note>`

- Phase 1: green | 2026-05-18 | 0a499f2 | machine | acceptance gate passed after Amendment 1 gold cleanup + smoke-test gold path fix; 54+ tests green; sot-validate clean
- Phase 2: pending | — | — | — | initial state
- Phase 3: pending | — | — | — | initial state
- Phase 4: pending | — | — | — | initial state
- Phase 5: pending | — | — | — | initial state
- Phase 6: pending | — | — | — | initial state

---

## Amendment 1 — 2026-05-18 — Phase 1 lexical refinements + authorized gold freeze cleanup

### What changed
1. **Invariant (b) lexical refinement** (validator code in `scripts/ai_assistant/sot_loader.py`). The plan-spec regex `[A-Z][A-Z0-9_]+` for skip_logic variable references is restricted to tokens containing an underscore: `\b[A-Z][A-Z0-9_]*_[A-Z0-9_]+\b`. Reason: the literal regex matches prose acronyms (HIV, ART, CD4) and instruction IDs (I1, I2), producing false positives on every skip_logic string that mentions clinical concepts. The underscore filter catches all dataset column names (HIV_HIVDAT, VAR_A, etc.) while silently ignoring bare acronyms. Documented in code with `# NOTE:` comments at module and inline scope.
2. **New error code `malformed-root`** for the root-not-a-dict guard. Previously reused `section-ref-missing` (a structurally different condition), which collides for callers filtering on error codes.
3. **Mutex reciprocity word-boundary fix.** Previously used plain substring search `f"mutually exclusive with {var_name}" not in partner_skip`, which silently missed broken reciprocity when one variable name is a prefix of another (e.g., `VAR_A` falsely found inside `VAR_AB`). Replaced with `re.search(r"mutually exclusive with\s+" + re.escape(var_name) + r"\b", partner_skip)`.

### Authorized one-time gold freeze cleanup
The placeholder attestation for `6_HIV` is `status: "pre-plan"`, `attested: false`. Plan rule 4 binds **attested** gold. The freeze ceremony for Phase 1 is therefore not yet complete; cleanup to satisfy the validator gates established in this same phase is permitted, recorded here as the authorization trail.

The following surgical edits to `data/SoT/Indo-VAP/6_HIV_policy.lean.yaml` are authorized:
- **HIV_ARTND.skip_logic**: remove the word `completing` so the partner phrase becomes `"inferred mutually exclusive with HIV_ARTDAT (not printed on form)"`. Restores reciprocity with HIV_ARTDAT.
- **HIV_CD4ND.skip_logic**: same pattern — remove `completing` so partner phrase becomes `"inferred mutually exclusive with HIV_CD4 (not printed on form)"`.
- **HIV_CD4LYND.skip_logic**: same pattern — remove `completing` so partner phrase becomes `"inferred mutually exclusive with HIV_CD4LY (not printed on form)"`.
- **HIV_HIVNDOTH**: add `notes:` containing the literal substring `"no PHI expected"` with a clinical rationale, e.g.:
  ```yaml
  notes: "no PHI expected — captures clinical reason for not performing HIV test (e.g., 'patient refused', 'equipment unavailable'); free-text content is operational, not a patient identifier"
  ```
- **ICTC**: add `notes:` documenting the quasi-identifier rationale for pseudonymization, e.g.:
  ```yaml
  notes: "ICTC site code (Integrated Counselling and Testing Centre) is a facility identifier and a quasi-identifier — small cardinality could re-identify a clinic when combined with other fields, hence phi: pseudonymize"
  ```
- **instructions I1 and I2**: remove the `effect:` key. The pre-existing `check_lean_policy.py` lean-instructions allowlist is `{id, text, location}`. Per the checker's own message: "gating belongs on per-variable skip_logic" — and the `effect:` content is already encoded in skip_logic on the affected variables (HIV_HIVNDOTH does not need effect since it has skip_logic; HIV_CD4DAT etc. already reference I2 in their skip_logic).

These edits change semantics minimally: mutex phrases are tightened to satisfy reciprocity regex; PHI documentation is added without changing actual `phi:` field values; `effect:` keys on instructions are removed (their semantics live in skip_logic). No clinical meaning is altered.

### Why
Phase 1 cannot complete its acceptance gate until the validator passes on the gold. Either the validator must be loosened (violating the plan's invariant intent) or the gold must be cleaned. Since the gold is unattested and the cleanup preserves clinical semantics, cleaning is the right call. This amendment is the authorization record.

### Phase Status Log entry to be appended after the gate passes (1.7 owns this entry)
Will be: `Phase 1: green | 2026-05-18 | <commit SHA> | machine | acceptance gate passed after Amendment 1 gold cleanup`.

---

## Amendment 2 — 2026-05-18 — Phase 1.x post-phase patches authorized by integration review

### Background

Phase 1 closed `green` in the Phase Status Log (commit 0a499f2). The Opus
integration reviewer — running the SDD workflow's closing-review step — marked
the phase **APPROVED WITH FOLLOW-UPS** and surfaced five items. Items 1 and 2
are applied immediately as a Phase 1.x patch before Phase 2 onboards any
non-Claude collaborator. Items 3, 4, and 5 are deferred (see below).

Per Operating Rule 2, none of these items appeared on Phase 1's deliverables
list. Per the Amendment Procedure, this section is the written authorization
record and is committed before any of the file changes below land.

### Item 1 — Gold-to-promoted resync (applied by sibling agent)

**What:** Copy `data/SoT/Indo-VAP/6_HIV_policy.lean.yaml` over
`output/Indo-VAP/llm_source/source_truth/6_HIV_policy.lean.yaml`.

**Why:** The runtime consumer `find_lean_yaml` at
`scripts/ai_assistant/agent_tools.py` (lines 1643, 1665) reads from
`output/.../source_truth/`. Post-Amendment-1 the gold file was surgically
cleaned to satisfy the property validator; the promoted copy was not updated,
so it now differs from the gold and would fail the validator. The resync brings
the runtime artifact into alignment with the attested-as-of-pre-plan gold.

**Applied by:** sibling agent (concurrent with this amendment).

### Item 2 — AGENTS.md + runbook portability update (applied by this agent)

**What:** Append a "Stage 4.5 — Property validator + diff-against-gold" section
to `AGENTS.md` after the existing Stage 4 block. Rewrite the
`docs/runbook_sot_build.md` section that describes `data/SoT/` to drop the
now-incorrect "reference-only" framing.

**Why:** Operating Rule 10 requires cross-LLM portability. `AGENTS.md` is the
canonical cross-LLM contract; `docs/runbook_sot_build.md` is the operator
guide. Neither mentioned `make sot-validate`, `diff_against_gold.py`, the
`data/SoT/<study>/_attestations/` directory, or the property validator. A
non-Claude tool reading those two docs before Phase 2 would not discover the
new Phase 1 gate. Additionally, under Operating Rule 4, the attested gold IS
the authority — the old "reference-only" label is no longer accurate.

**Applied by:** this agent (commit on `PHI_handing_review` branch, 2026-05-18).

### Items 3, 4, 5 — Deferred

- **Item 3 (CLI signature normalization):** Align `--study`/`--form` arg names
  across all Phase 1 scripts. Deferred to Phase 3 wiring when `study_intake.py`
  is updated.
- **Item 4 (validator dead-code comment):** Minor cosmetic — remove a stale
  `# TODO` comment in `sot_loader.py`. Deferred to anytime cosmetic cleanup;
  no functional impact.
- **Item 5 (Phase 6 attestation shape clarification):** The reviewer noted the
  Phase 6 anchor ceremony section does not specify whether `hmac` covers the
  canonical-normalized YAML bytes or the raw bytes. Deferred to Phase 6 plan
  amendment when the ceremony CLI is being built.

### Amendment scope

This amendment authorizes touching the following files that were NOT on Phase
1's deliverables list:

- `AGENTS.md` (insert Stage 4.5 section)
- `docs/runbook_sot_build.md` (rewrite "Reference: Gold Diff" stale framing)
- `output/Indo-VAP/llm_source/source_truth/6_HIV_policy.lean.yaml` (sibling
  agent only — copy gold over promoted; not touched by this agent)

No code, tests, Makefile, gold YAML (`data/SoT/...`), or attestation JSON is
modified by Item 2.

---

## Amendment 3 — 2026-05-18 — Two-stage model (Generation vs Attestation) + Stage A sister plan

### Background

A grilling pass on 2026-05-18 surfaced a missing piece: this plan only describes
**how a candidate YAML is proven trustworthy** (Phases 1–6). It does not describe
**how the candidate is produced from PDF + dataset headers + data dictionary**.
For 6_HIV the candidate was hand-produced pre-plan, so the gap did not bite.
For the remaining 27 Indo-VAP forms (and every future study), generation is the
actual bottleneck.

Additionally, the user's stated workflow (iterative LLM extraction with
screenshot loops, LLM-flagged uncertainty surfaced to the user, separate
extractor and reviewer subagents, parallel per-form dispatch) is a **legitimate
mini-pipeline in its own right**, but it must NOT be conflated with the
6-phase attestation pipeline. Conflation causes:

- Skill prompts edited mid-run (breaks reproducibility, violates 21 CFR Part 11
  computerized-system controls).
- LLM-self-reported uncertainty trusted without cross-grader check (regulators
  reject self-attestation).
- Main agent rubber-stamping subagent reports (no real final review).

### What changes

The pipeline is reframed as **two stages**:

- **Stage A — Generation.** Takes raw inputs (PDF, dataset column headers, data
  dictionary) and produces a candidate lean YAML at
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml` with
  `status: candidate`. Stage A is iterative, LLM-heavy, and subagent-driven.
  It has its own plan: `docs/plan_sot_stage_a_generation.md`.
- **Stage B — Attestation.** This file. The existing 6 phases. Takes a
  candidate (with `status: candidate` metadata), proves it via validator +
  audit bundle + diff classifier + adversarial verify, and ends with HMAC
  e-signature in Phase 6. Result: `status: attested` gold at
  `data/SoT/<study>/<form>_policy.lean.yaml`.

Stage A produces inputs for Stage B Phase 1. Phase 1's property validator is
the **boundary gate**: a Stage A artifact only enters Stage B once it passes
the validator. A failing validator stays in Stage A for another iteration.

### New Operating Rule 11

**Stage A → Stage B boundary.** A candidate YAML enters Stage B only when:

1. The Phase 1 property validator returns `passed: True` on it.
2. Every variable has at least one citation in the audit log (PDF region,
   dictionary entry, or explicit "inferred — no evidence" marker).
3. The user has explicitly confirmed all Stage A reviewer-flagged items.

Stage A workflow details (how citations are produced, which subagent does what,
how parallel dispatch works for N forms) are out of scope for this file — see
`docs/plan_sot_stage_a_generation.md`.

### Grilling resolutions baked in

These are answers to gaps raised by the 2026-05-18 grilling pass. They are
binding on Stage A and reinforce existing rules in Stage B:

1. **Uncertainty signal.** LLM-self-reported confidence is NOT a valid
   uncertainty signal under regulated use. Stage A must use
   (a) cross-grader disagreement (Tier-A only) and/or
   (b) citation-presence check (every Tier).
   Stage B Phase 5 keeps its 2-of-3 multi-LLM verify — the 4-phase
   simplification floated earlier in the grilling is rejected because the
   AI Portal is regulated.
2. **Skill versioning.** The Stage A extractor/reviewer skill is a
   computerized system. Hand-amending the skill prompt mid-run is forbidden.
   Skill version is pinned per form in the audit log. Skill prompt changes =
   new minor version; existing attestations stay valid against the prior
   version. Material behavior change = re-anchor.
3. **Final review.** Main agent never approves. Approval = named human
   signatory in Stage B Phase 6 via HMAC e-signature. Main agent's role is
   orchestration + validator re-run + report collation.
4. **Extractor ≠ reviewer separation.** Reviewer subagent reads YAML +
   source pack ONLY. Does not re-read PDF (avoids being subject to the same
   LLM blind spots as the extractor). PDF re-read is reserved for
   citation-verification of explicitly flagged fields.
5. **Data dictionary as load-bearing input.** Stage A consumes the SAS data
   dictionary alongside PDF + headers. PDF–dictionary disagreements go in
   the YAML's `discrepancies:` block. The dictionary is not silently
   overridden.

### Amendment scope

This amendment authorizes:

- Creating `docs/plan_sot_stage_a_generation.md` (Stage A sister plan).
- Adding Operating Rule 11 above to the rules-of-the-road.
- No code, no test, no Makefile, no gold YAML, no attestation file is
  modified by this amendment. Stage A implementation is its own future work,
  scoped by the sister plan and currently `pending` in that plan's Phase
  Status Log.

### Phase Status Log

No changes to existing Phase 1–6 status rows. Stage A phases (A.1–A.8) are
tracked in the sister plan's own Phase Status Log, not here.
