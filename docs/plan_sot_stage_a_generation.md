# Plan: SoT Stage A — Generation Pipeline

**Plan version:** 1.0 (2026-05-18). Owner: Solomon S Joseph.

**Authority:** This file is a **sister plan** to `docs/plan_sot_afk_pipeline.md`
(the Stage B Attestation pipeline). Stage A and Stage B together form the full
SoT pipeline. Operating rules from the parent plan apply here unless explicitly
overridden in the Stage A Operating Rules section below.

**Scope boundary:** Stage A produces a **candidate** lean YAML at
`output/<study>/llm_source/source_truth/<form>_policy.lean.yaml` with
`status: candidate`. It does not promote, attest, or sign. Handoff to Stage B
is at Phase 1's property validator (parent plan).

---

## Goal

Convert raw form inputs (PDF + dataset column headers + data dictionary) into a
candidate lean YAML that:

1. Passes the Stage B Phase 1 property validator.
2. Has at least one citation per variable in the Stage A audit log
   (PDF region, dictionary entry, or explicit "inferred — no evidence" marker).
3. Has been confirmed by the user on all reviewer-flagged uncertainty items.

Stage A is iterative, LLM-heavy, and subagent-driven. It is designed so that
**the user only spends time on what the LLM is genuinely uncertain about** —
not on rubber-stamping the certain parts.

---

## Operating Rules (Stage A specific; inherit parent plan rules 1–11)

A1. **Extractor ≠ reviewer.** A single LLM instance never both produces the
    YAML and reviews it. The reviewer runs in a fresh subagent, reads YAML +
    source pack only, and does NOT re-read the PDF (avoids the same blind
    spots). PDF re-read is reserved for citation-verification of explicitly
    flagged fields.

A2. **Skill prompt immutability per run.** The extractor/reviewer skill prompts
    at `skills/sot-lean-generator/` are pinned by version for the duration of a
    Stage A run. Hand-amending the prompt mid-run is forbidden (violates parent
    plan Operating Rule 6 — model/seed pinning analog for prompts). User
    feedback corrects the **YAML output**, not the **skill prompt**. Skill
    prompt changes are versioned releases; existing attestations stay valid
    against the prior version.

A3. **Citation presence is mandatory.** Every variable in the candidate YAML
    must have at least one entry in the Stage A audit log's `citations.json`.
    Allowed citation types:
    - `pdf_region`: page + bbox coordinates of the printed evidence
    - `dictionary_entry`: column name + dictionary file path + line number
    - `inferred_no_evidence`: explicit marker with rationale (e.g., "derived
      from widget shape — 3 character-boxes implies type=code")
    Missing citations are an automatic uncertainty flag.

A4. **Parallel-by-default for N > 1.** When invoked on multiple forms, Stage A
    MUST dispatch per-form subagents in parallel. Sequential per-form execution
    is forbidden unless explicitly requested by the user with a written reason.

A5. **Main agent is orchestrator, never approver.** The main agent's allowed
    actions: dispatch subagents, collate reports, surface flagged items to
    user, re-run validator, write audit log. The main agent does NOT decide
    whether a field is correct, edit the YAML on its own judgment, or
    suppress reviewer flags.

A6. **User confirmation is per-flag, not per-form.** The reviewer's
    uncertainty report is a list of items. The user confirms/corrects each
    item independently. Bulk "looks good, ship it" is not a valid confirmation
    when any items are flagged.

A7. **Data dictionary is load-bearing.** Stage A consumes the SAS data
    dictionary alongside PDF + headers. PDF–dictionary disagreements are
    recorded in the YAML's `discrepancies:` block per the parent plan's
    Authority Order (printed form > annotation > dataset header > dataset
    rows). The dictionary is never silently overridden.

A8. **Per-form audit log is write-once.** Every Stage A run writes its
    artifacts to `output/<study>/_audit/<form>/<run-id>/stage_a/`. Bundles are
    never modified or deleted by the pipeline. Re-runs produce new bundles
    with new run-ids.

---

## Definitions

- **Source pack** — JSON file produced by `scripts/source_truth/study_intake.py`
  containing `{headers, render_paths, raw_text}` for a single form. Already
  exists in the repo at `/tmp/sot_source_pack_<form>.json` by convention.
- **Data dictionary entry** — a record from the study's SAS data dictionary
  describing a single column: name, type, length, label, format. Stage A loads
  this for every header in the source pack.
- **Citation** — a structured pointer to evidence supporting a YAML field's
  value. See Operating Rule A3 for allowed types.
- **Uncertainty flag** — an item in the reviewer's report asking the user to
  confirm or correct a specific YAML field. Flag triggers:
  (a) cross-grader disagreement (Tier-A only),
  (b) missing citation,
  (c) reviewer's structural concern (e.g., undeclared PHI),
  (d) PDF–dictionary discrepancy without resolution.
- **Skill version** — the git SHA of the `skills/sot-lean-generator/` directory
  at the time of the run, recorded in `skill_version.json`.
- **Run-id** — `<UTC-ISO-8601>_<sha8>` — timestamp + first 8 chars of the
  candidate YAML SHA-256.

---

## Roles

- **Main agent** (Opus 4.7): orchestrates per-form dispatch, collates flagged
  items, drives the user-confirmation loop, runs the boundary-gate validator,
  writes the audit log. **Never edits the YAML or approves a flag.**
- **Extractor subagent** (Sonnet 4.6, one per form): reads source pack +
  PDF screenshots + dictionary; produces draft YAML with citations.
- **Reviewer subagent** (different LLM family OR different Claude temperature,
  one per form): reads YAML + source pack only; produces uncertainty report.
- **Cross-grader subagent** (Tier-A only, Sonnet 4.6 fresh instance, one per
  Tier-A form): independent re-extraction from scratch. Differences from
  extractor's output become uncertainty flags.
- **User**: confirms/corrects each flagged item. Does not review certain
  fields (that's the LLM's job). Has final say on candidate promotion.

---

## Stage A Audit Log Requirements

For every Stage A run on every form, the main agent writes
`output/<study>/_audit/<form>/<run-id>/stage_a/`:

- `manifest.json`: form, study, tier, model IDs/versions/seeds for all
  subagents, skill version (git SHA of `skills/sot-lean-generator/`),
  run start/end UTC, PDF SHA-256, dictionary SHA-256.
- `extractor_transcript.jsonl`: full extractor LLM transcript.
- `reviewer_transcript.jsonl`: full reviewer LLM transcript.
- `cross_grader_transcript.jsonl` (Tier-A only): cross-grader transcript.
- `citations.json`: per-field citation map (parent: variable name → list of
  citations per Operating Rule A3).
- `uncertainty_report.json`: the reviewer's structured list of flagged items.
- `user_feedback.json`: the user's response to each flag (confirm/correct +
  optional note).
- `candidate.yaml`: the final candidate handed off to Stage B Phase 1.
- `validator.log`: stdout/stderr + exit code of Phase 1 validator run on
  `candidate.yaml`.

Audit log retention: indefinite, write-once. No bundle is modified or deleted.

---

## Phase A.1 — Source Pack Assembly (no new code)

**Why first:** Without a source pack, no subagent has anything to read. This
phase is already implemented in `scripts/source_truth/study_intake.py`.

### Deliverables

A.1.1 Confirm `study_intake.py --study <S> --form <F>` produces:
- `/tmp/sot_source_pack_<form>.json` with non-empty `headers` and `render_paths`.
- PNG renders under `/tmp/sot_render_<form>/`.

A.1.2 No code change. Document the contract in this plan and reference it
from `skills/sot-lean-generator/AGENTS.md`.

### Acceptance gate

`uv run --all-groups python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV` exits 0 and writes both expected paths.

### Out of scope

- Changes to `study_intake.py` itself.
- Adding new image-rendering backends.

---

## Phase A.2 — Data Dictionary Integration

**Why next:** Dictionary entries supply field types, labels, and formats that
the PDF often does not show. Without the dictionary, the extractor either
guesses or omits.

### Deliverables

A.2.1 New module `scripts/source_truth/dictionary_loader.py` with:
- `load_dictionary(study: str, form: str, repo_root: Path) -> dict[str, DictEntry]`
- `DictEntry` is a dataclass with fields:
  `{name, type, length, label, format, source_file, source_line}`.

A.2.2 Extend the source pack contract: when the source pack is loaded by the
extractor subagent (Phase A.3), the dictionary entries for the form's headers
are merged in as a top-level `dictionary` key.

A.2.3 Unit test: round-trip a known Indo-VAP form's dictionary; assert every
header in the source pack has a corresponding `DictEntry`.

### Acceptance gate

Tests at `tests/skills/sot-lean-generator/test_dictionary_loader.py` pass.
`uv run --all-groups python -m pytest tests/skills/sot-lean-generator/test_dictionary_loader.py` exits 0.

### Out of scope

- Modifying the dictionary files themselves.
- Inferring missing dictionary entries from the PDF (that's the extractor's
  job, with explicit `inferred_no_evidence` citation).

---

## Phase A.3 — Extractor Subagent

**Why:** This is the LLM that reads PDF + headers + dictionary and produces
the draft YAML. Per Operating Rule A1, it does only this — never reviews.

### Deliverables

A.3.1 Skill prompt at
`skills/sot-lean-generator/extractor/EXTRACTOR_PROMPT.md`. Contract:
- Input: source pack JSON path + render PNG paths + dictionary entries
  (delivered via the prompt, not via tool calls).
- Output: lean YAML on stdout (parseable, validator-ready shape) +
  a `citations.json`-shaped sidecar covering every variable.
- Mandatory: every variable has at least one citation.
- Forbidden: editing source pack, calling external APIs other than the LLM.

A.3.2 Wrapper CLI at `skills/sot-lean-generator/scripts/extract.py`:
- Args: `--study`, `--form`, `--source-pack`, `--render-dir`, `--out-yaml`,
  `--out-citations`.
- Dispatches a single extractor subagent invocation, writes both outputs.
- Exit codes: 0 = produced both files; 2 = IO/argument error; 3 = LLM
  invocation failure.

A.3.3 Test fixture: a deterministic extractor stub that lets the CLI be
exercised without a real LLM call (for CI). Real LLM invocation is gated
behind an env var so CI does not burn tokens.

### Acceptance gate

`extract.py` produces a YAML that passes the Stage B Phase 1 property
validator on a known good form (6_HIV). Citations file has ≥1 entry per
variable. Tests at `tests/skills/sot-lean-generator/test_extract.py` pass.

### Out of scope

- Reviewer logic (Phase A.5).
- Cross-grader (Phase A.6).
- Iteration loop (Phase A.4).

---

## Phase A.4 — Self-Iteration Loop

**Why:** A single-pass extraction is rarely complete. Allow the extractor up
to N passes to tighten citations and remove noise, but bound it so it cannot
loop forever.

### Deliverables

A.4.1 Extend `extract.py` with `--max-passes N` (default 3, max 5). After
the first extraction, the extractor is re-invoked with the previous YAML +
citations as context and asked: "tighten any inferred citations to real
PDF evidence where possible; remove any field you cannot justify."

A.4.2 Stop condition: extractor produces a no-op pass (output byte-equal to
previous pass) OR max passes reached.

A.4.3 Per-pass transcripts written under
`output/<study>/_audit/<form>/<run-id>/stage_a/extractor_pass_<N>.jsonl`.

### Acceptance gate

On a synthetic form with deliberately weak first-pass citations, the loop
demonstrably tightens ≥1 citation between pass 1 and pass 2. Test at
`tests/skills/sot-lean-generator/test_extract_iteration.py` passes.

### Out of scope

- Changing the citation schema between passes (citations remain Phase A.3's
  shape).
- Stopping early on "user looks satisfied" — user confirmation is Phase A.7.

---

## Phase A.5 — Reviewer Subagent

**Why:** Independent check on the extractor. Per Operating Rule A1, reviewer
reads only YAML + source pack, not PDF.

### Deliverables

A.5.1 Skill prompt at `skills/sot-lean-generator/reviewer/REVIEWER_PROMPT.md`.
Contract:
- Input: candidate YAML + source pack JSON + citations.json.
- Output: `uncertainty_report.json` with shape:
  ```json
  [
    {"variable": "HIV_CD4", "flag": "missing_citation",
     "severity": "medium", "rationale": "..."},
    {"variable": "ICTC", "flag": "phi_undeclared",
     "severity": "high", "rationale": "..."},
    {"variable": "HIV_HIVST", "flag": "pdf_dict_disagreement",
     "severity": "medium", "rationale": "PDF says 'Positive/Negative'; dictionary label says 'HIV status code'"}
  ]
  ```
- Must run the Phase 1 property validator and include any validator errors
  as `flag: "validator_<code>"` entries.

A.5.2 Wrapper CLI at `skills/sot-lean-generator/scripts/review.py` mirroring
`extract.py`'s arg shape.

A.5.3 Reviewer uses a different LLM family from extractor when available
(e.g., Gemini for review if Claude was extractor). Falls back to same-family
different-temperature if cross-family unavailable. Recorded in
`manifest.json`.

### Acceptance gate

On a known-bad YAML (deliberate undeclared PHI, missing citation), the
reviewer flags both issues. Test at
`tests/skills/sot-lean-generator/test_review.py` passes.

### Out of scope

- Reviewer re-reading the PDF (forbidden by Operating Rule A1).
- Reviewer editing the YAML (reviewer flags only).

---

## Phase A.6 — Cross-Grader Subagent (Tier-A Only)

**Why:** For high-risk forms, a second independent extraction catches blind
spots both extractor and reviewer share.

### Deliverables

A.6.1 Reuse `extract.py` from Phase A.3 with a fresh subagent invocation
(different seed). Output: `cross_grader_candidate.yaml` +
`cross_grader_citations.json`.

A.6.2 New comparator `skills/sot-lean-generator/scripts/cross_grade.py`:
- Input: two YAMLs (extractor + cross-grader).
- Output: structured diff classified as
  `{cosmetic, within_rule, disagreement}` (reuses Phase B's
  `diff_against_gold.py` classifier as a library).
- `disagreement` items are appended to `uncertainty_report.json` with
  `flag: "cross_grader_disagreement"`.

A.6.3 Skip condition: form tier is B or C → A.6 is a no-op (Tier-A only).

### Acceptance gate

On Tier-A form 6_HIV, cross-grader runs. On a synthetic Tier-B form, A.6
exits 0 immediately and writes a `skipped_tier_b` marker to the audit log.
Tests at `tests/skills/sot-lean-generator/test_cross_grade.py` pass.

### Out of scope

- Resolving disagreements automatically (user does this in A.7).
- Running cross-grader on Tier-B/C (cost waste).

---

## Phase A.7 — User Confirmation Loop

**Why:** The user is the only one who can resolve true ambiguity. Per
Operating Rule A6, the user reviews per-flag, not the whole YAML.

### Deliverables

A.7.1 CLI at `skills/sot-lean-generator/scripts/confirm.py`:
- Args: `--audit-dir <stage_a/ dir>`, `--candidate <yaml>`.
- Loads `uncertainty_report.json`.
- For each flag, prints: variable + flag + rationale + relevant citation/PDF
  region + current YAML value.
- Prompts user: confirm / correct (with new YAML value) / defer.
- Writes `user_feedback.json` with the per-flag responses.

A.7.2 If user corrects a field, the corrected value is written into the
candidate YAML directly (no skill prompt edit). The audit log records
both the original LLM-generated value and the user's correction.

A.7.3 If any flag is `deferred`, A.7 exits with non-zero (cannot promote).
The user must re-run A.7 or re-run from A.3 with the user's feedback as
additional context.

### Acceptance gate

On a synthetic form with 3 flags (1 confirmed, 1 corrected, 1 deferred),
A.7 exits non-zero and the audit log captures all three actions correctly.
Test at `tests/skills/sot-lean-generator/test_confirm.py` passes.

### Out of scope

- A UI (CLI only for v1).
- Editing the skill prompt based on user feedback (forbidden by A2).

---

## Phase A.8 — Stage A → Stage B Handoff

**Why:** Codify the boundary gate so handoff is mechanical, not judgmental.

### Deliverables

A.8.1 CLI at `skills/sot-lean-generator/scripts/handoff.py`:
- Args: `--audit-dir <stage_a/ dir>`, `--candidate <yaml>`,
  `--study`, `--form`.
- Verifies all three boundary conditions (parent plan Operating Rule 11):
  1. Phase 1 property validator passes on the candidate.
  2. Every variable has ≥1 citation in `citations.json`.
  3. Every flag in `uncertainty_report.json` has a confirm or correct
     response in `user_feedback.json` (no `deferred`).
- On pass: writes candidate to
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml` with
  `status: candidate` in the YAML frontmatter; writes a copy of the
  audit log path to the Stage B Phase 3 audit bundle's manifest.
- On fail: exits non-zero with the specific boundary condition that failed.

A.8.2 Makefile target `sot-stage-a STUDY=<S> FORM=<F>` that drives A.1 → A.8
end-to-end for a single form.

A.8.3 Makefile target `sot-stage-a-all STUDY=<S>` that dispatches per-form
parallel subagents per Operating Rule A4 for all forms in `data/raw/<S>/`.

### Acceptance gate

`make sot-stage-a STUDY=Indo-VAP FORM=6_HIV` runs cleanly end-to-end (with
the user confirming flags interactively or via a non-interactive test
fixture). Resulting candidate at the promoted path passes Stage B Phase 1
validator. Tests at `tests/skills/sot-lean-generator/test_handoff.py` pass.

### Out of scope

- Anything Stage B does (attestation, signing, diff classification).
- Promoting a candidate that fails any of the three boundary conditions
  (forbidden by Operating Rule 11).

---

## Plan Amendment Procedure

Same as parent plan. Any deviation requires:

1. A new "Amendment N — <YYYY-MM-DD> — <topic>" section appended to this file.
2. Description of what changed and why.
3. Committed BEFORE the deviating code change.
4. Phase acceptance gate re-evaluated against the amended plan.

Append-only. Original text never rewritten.

---

## Phase Status Log

Append one line per phase transition. Format:
`Phase A.N: <pending|in-progress|green|blocked> | <YYYY-MM-DD> | <commit SHA> | <signatory or "machine"> | <note>`

- Phase A.1: green | 2026-05-18 | (pre-existing) | machine | `study_intake.py` already implements source pack assembly
- Phase A.2: pending | — | — | — | initial state
- Phase A.3: pending | — | — | — | initial state
- Phase A.4: pending | — | — | — | initial state
- Phase A.5: pending | — | — | — | initial state
- Phase A.6: pending | — | — | — | initial state
- Phase A.7: pending | — | — | — | initial state
- Phase A.8: pending | — | — | — | initial state
