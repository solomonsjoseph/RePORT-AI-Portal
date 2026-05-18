# Plan: SoT Stage A — Generation Pipeline (Trimmed)

**Plan version:** 2.0 (2026-05-18). Owner: Solomon S Joseph.

**Supersedes:** `docs/plan_sot_stage_a_generation.md` v1.0 (8 phases). Council
review on 2026-05-18 cut six phases as ceremony duplicating Stage B's regulated
record; two controls survived as load-bearing for catching verifier-passing
semantic errors.

**Authority:** Sister plan to `docs/plan_sot_afk_pipeline.md` (Stage B
Attestation). Parent operating rules apply unless overridden here.

**Scope boundary:** Stage A produces a **candidate** lean YAML at
`output/<study>/llm_source/source_truth/<form>_policy.lean.yaml` with
`status: candidate`. It does not promote, attest, or sign. Handoff to Stage B
is at Phase 1's property validator (parent plan).

---

## Goal

Convert raw form inputs (PDF + dataset column headers + data dictionary) into a
candidate lean YAML that:

1. Passes the deterministic Stage A verifier (`check_lean_policy.py`).
2. Passes the Stage B Phase 1 property validator.
3. Has a citation per variable (PDF page or dictionary entry name).
4. Has been user-confirmed on every reviewer-flagged uncertainty item.

---

## Operating Rules (Stage A specific; inherit parent plan rules 1–11)

Only the rules that catch a failure mode survive. The rest were ceremony.

A1. **Extractor ≠ reviewer.** A single LLM instance never both writes the
    YAML and reviews it. The reviewer runs in a fresh session, reads
    `candidate.yaml` + source pack + dictionary only, and does NOT re-read the
    PDF (avoids shared blind spots). PDF re-read is for citation verification
    of explicitly flagged fields. *Why this survives:* verifier is a syntax
    gate — wrong widget type, wrong PHI flag, wrong skip-rule all pass it. An
    independent judge is the only defense.

A2. **Per-variable citations are mandatory.** Every variable in the candidate
    YAML carries at least one citation. Allowed forms:
    - `pdf_page: <int>` — page of the printed evidence (no bbox required)
    - `dict_entry: <column_name>` — name as it appears in the data dictionary
    - `inferred: "<rationale>"` — explicit marker when no evidence exists
    *Why this survives:* lets a human spot-check a field in under a minute
    instead of re-reading the PDF. Without it, batch human review degenerates
    to rubber-stamping.

A3. **Rules files are the compounding artifact.** Every Stage B rejection
    upgrades `exhaustive_yaml_rules.md`, `lean_yaml_rules.md`, or the
    verifier — never the pipeline shape. The pipeline stays small; the rules
    grow. *Why this survives:* prevents the v1 mistake of formalizing process
    based on N=1 forms.

A4. **Main agent orchestrates; never approves.** Allowed: dispatch subagents,
    collate flags, surface to user, run verifier, write audit log. Not
    allowed: deciding a field is correct, editing YAML on its own judgment,
    suppressing a reviewer flag. *Why this survives:* keeps PHI judgment in a
    human's hands.

**Cut from v1:** A2 (skill prompt immutability per run — moved to Stage B
attestation contract where it actually matters), A4 (parallel-by-default —
not a "mode," just `xargs -P` if you want it), A6 (per-flag confirmation —
kept as Phase A.4 behavior, doesn't need its own rule), A8 (audit log
write-once — inherited from parent plan's Operating Rule 4).

---

## Roles

- **Main agent** (Opus 4.7): orchestrates, runs verifier, drives flag loop,
  writes audit log. Never edits YAML or approves a flag.
- **Author subagent** (Sonnet 4.6, one per form): reads source pack + PDF
  renders + dictionary; produces candidate YAML with citations.
- **Reviewer subagent** (fresh Sonnet 4.6 session, one per form): reads
  candidate YAML + source pack + dictionary only; emits `flags.json`.
  Cross-LLM family (Gemini) is a future upgrade, not v1.
- **User**: resolves each flag (confirm / correct / defer). Reviews flagged
  items only, not the whole YAML — the LLM owns the certain parts.

**Cut from v1:** Cross-grader subagent (Tier-A only) — deferred until Tier-A
forms exist and the budget is justified by observed error rate, not by plan.

---

## Stage A Audit Log

Per form per run at `output/<study>/_audit/<form>/<utc_iso>/stage_a/`:

- `candidate.yaml` — the YAML handed to Stage B
- `flags.json` — reviewer's uncertainty report + user resolutions inline
- `transcript.jsonl` — author + reviewer LLM transcripts concatenated
- `verifier.log` — `check_lean_policy.py` stdout/stderr + exit code

Four files, not eight. Manifest data (model IDs, skill SHA, PDF SHA, dict SHA)
lives in the YAML frontmatter, not a separate file. Write-once retention is
inherited from parent plan Operating Rule 4.

---

## Phase A.1 — Source Pack (existing, deterministic)

Already implemented in `scripts/source_truth/study_intake.py`.

### Deliverables

A.1.1 Confirm `study_intake.py --study <S> --form <F>` produces:
- `/tmp/sot_source_pack_<form>.json` (headers + PDF SHA-256)
- `/tmp/sot_render_<form>/<form>.pdf.png` (600 DPI render)

A.1.2 **CI determinism gate (new).** Pin the source-pack output hash in CI.
Run `study_intake.py` on a reference form (6_HIV) in the test job and assert
the JSON SHA-256 matches the committed expected value. If it drifts across
locales, line endings, or Ghostscript versions, the verifier and Stage B
gold-anchor both break silently — fail fast in CI instead. *Why:* cross-LLM
portability dies if the deterministic foundation isn't actually deterministic
across user shells.

### Acceptance gate

`uv run --all-groups python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV` exits 0 and writes both expected paths. CI source-pack
hash test passes.

---

## Phase A.2 — Data Dictionary Loader

**Why:** Dictionary entries supply field types, labels, formats the PDF
doesn't show. Without them, the author guesses or omits.

### Deliverables

A.2.1 New module `scripts/source_truth/dictionary_loader.py` with
`load_dictionary(study, form, repo_root) -> dict[str, DictEntry]`. `DictEntry`
fields: `{name, type, length, label, format, source_file, source_line}`.

A.2.2 Dictionary entries are merged into the source pack as a top-level
`dictionary` key when the author subagent loads it.

A.2.3 Unit test: round-trip a known Indo-VAP form's dictionary; assert every
header in the source pack has a matching `DictEntry`.

### Acceptance gate

`uv run --all-groups python -m pytest tests/skills/sot-lean-generator/test_dictionary_loader.py` exits 0.

---

## Phase A.3 — Author + Reviewer + Verify Loop

**Why:** This is the LLM work. Old v1 split this into A.3 (extractor), A.4
(self-iteration), A.5 (reviewer) — three phases for one loop. Council
verdict: one phase with two LLM roles and a deterministic gate.

### Deliverables

A.3.1 Author skill prompt at
`skills/sot-lean-generator/author/AUTHOR_PROMPT.md`. Contract:
- Input: source pack JSON + render PNG paths + merged dictionary entries.
- Output: lean YAML (validator-ready) with citations per Operating Rule A2.
- Mandatory: every variable has at least one citation.
- Forbidden: editing source pack, external API calls beyond the LLM.

A.3.2 Reviewer skill prompt at
`skills/sot-lean-generator/reviewer/REVIEWER_PROMPT.md`. Contract:
- Input: candidate YAML + source pack + dictionary only (no PDF re-read).
- Output: `flags.json` with shape:
  ```json
  [
    {"variable": "HIV_CD4", "flag": "missing_citation",
     "severity": "medium", "rationale": "..."},
    {"variable": "ICTC", "flag": "phi_undeclared",
     "severity": "high", "rationale": "..."},
    {"variable": "HIV_HIVST", "flag": "pdf_dict_disagreement",
     "severity": "medium", "rationale": "..."}
  ]
  ```
- Must include any verifier errors as `flag: "verifier_<code>"` entries.

A.3.3 Wrapper CLI at `skills/sot-lean-generator/scripts/author_and_review.py`:
- Args: `--study`, `--form`, `--source-pack`, `--render-dir`, `--audit-dir`.
- Step 1: dispatch author subagent → write `candidate.yaml` + citations.
- Step 2: run `check_lean_policy.py` on candidate. If it fails, feed errors
  back to author for one re-pass. Cap: 3 author passes total. Stop on pass
  or cap. *Cut from v1:* byte-equality stop condition — meaningless when the
  verifier output is the actual signal.
- Step 3: dispatch reviewer subagent (fresh session) → write `flags.json`.
- Exit codes: 0 = candidate + flags written; 2 = IO error; 3 = LLM failure;
  4 = verifier still failing after 3 author passes (escalate to user).

A.3.4 Test fixture: deterministic author + reviewer stubs for CI without
burning LLM tokens. Real LLM gated behind env var.

### Acceptance gate

On 6_HIV: `author_and_review.py` produces a `candidate.yaml` that passes
`check_lean_policy.py`. Citations present for every variable. `flags.json`
includes at least one item (the LLM should not claim full certainty on a
real form). Tests at `tests/skills/sot-lean-generator/test_author_and_review.py`
pass.

### Out of scope

- Cross-grader on Tier-A forms (deferred; add when error rate justifies cost).
- Cross-LLM-family review (deferred; same-family fresh session is v1).

---

## Phase A.4 — Flag Resolution + Promote

**Why:** User is the only authority for true ambiguity. Old v1 split this
into A.7 (confirm CLI) and A.8 (handoff CLI). Council verdict: one phase, and
the human review is **batched across the run** — one consolidated flags table
sorted by field type, not per-form interrupt.

### Deliverables

A.4.1 CLI at `skills/sot-lean-generator/scripts/resolve_and_promote.py`:
- Args: `--audit-dir <dir>` (single form) OR `--run-root <dir>` (batch mode
  across all forms in a run).
- **Single-form mode:** loads `flags.json`, walks each item, prompts user,
  writes resolutions inline back into `flags.json`.
- **Batch mode:** aggregates `flags.json` across all forms in the run,
  presents one consolidated table sorted by `flag` type (all
  `pdf_dict_disagreement` together, then all `phi_undeclared`, etc.). User
  resolves the table once; CLI writes resolutions back to each form's
  `flags.json`. *Why batched:* 27 forms × per-flag interrupt = death by
  pings; one review session beats 27 context switches.

A.4.2 Resolution applies to `candidate.yaml` directly. Audit records both
the original LLM value and the user correction. Skill prompts are never
edited based on user feedback — that's a versioned skill release.

A.4.3 Promote step (same CLI, `--promote` flag):
- Verifies three boundary conditions (parent plan Operating Rule 11):
  1. `check_lean_policy.py` passes on `candidate.yaml`.
  2. Every variable has ≥1 citation.
  3. Every flag has a `confirm` or `correct` resolution (no `deferred`).
- On pass: copies `candidate.yaml` to
  `output/<study>/llm_source/source_truth/<form>_policy.lean.yaml`.
- On fail: exits non-zero with the failing condition.

A.4.4 Makefile targets:
- `make sot-stage-a STUDY=<S> FORM=<F>` — single form, A.1 → A.4.
- `make sot-stage-a-all STUDY=<S>` — dispatches per-form subagents in
  parallel (just `xargs -P`, no "mode" framing), then batched flag
  resolution at the end.

### Acceptance gate

`make sot-stage-a STUDY=Indo-VAP FORM=6_HIV` runs end-to-end (interactive or
non-interactive test fixture). Promoted candidate passes Stage B Phase 1
validator. Tests at `tests/skills/sot-lean-generator/test_resolve_and_promote.py`
pass.

---

## Cross-LLM Portability

Per the standing portability rule: the canonical artifact is the CLI + the
runbook at `docs/runbook_sot_build.md`, not the Claude Code SKILL.md. The
SKILL.md is a thin wrapper that points Claude Code at the same CLIs and
rules files any other LLM operator would use from the runbook. Updates to
flow shape land in the runbook first.

---

## Plan Amendment Procedure

Same as parent plan. Any deviation requires an appended
"Amendment N — <YYYY-MM-DD> — <topic>" section, committed before the
deviating code change. Append-only. Original text never rewritten.

---

## Phase Status Log

Append one line per phase transition:
`Phase A.N: <pending|in-progress|green|blocked> | <YYYY-MM-DD> | <commit SHA> | <signatory or "machine"> | <note>`

- Phase A.1: green | 2026-05-18 | (pre-existing) | machine | `study_intake.py` already implements source pack assembly; CI determinism gate pending
- Phase A.2: pending | — | — | — | initial state (carried from v1)
- Phase A.3: pending | — | — | — | initial state (merges old A.3 + A.4 + A.5)
- Phase A.4: pending | — | — | — | initial state (merges old A.7 + A.8)

---

## Council Audit Trail (2026-05-18)

This trim was produced by `ecc:council` review. Voices:

- **Skeptic** said collapse further: drop the flag channel entirely, let
  Stage B catch what slips. *Rejected:* the verifier is a syntax gate, not a
  semantic one; without a flag channel, semantic errors reach Stage B's
  gold-anchor where regen for the 27 LLM-produced forms is circular.
- **Pragmatist** said collapse, plus pin source-pack CLI determinism in CI
  and batch human review. *Adopted both* (A.1.2 and A.4.1 batch mode).
- **Critic** said don't collapse fully; keep Extractor ≠ Reviewer and
  per-variable citations. *Adopted both* (A1 and A2).
- **Architect** initial position dropped reviewer + citations. *Reversed by
  Critic's failure-mode argument*: silent semantic error reaches canonical
  path → downstream LLM source builder lies about clinical fields →
  clinician notices a wrong patient answer months later with no provenance
  to triage which forms were poisoned.

Cut from v1 (deliberate, recorded for future re-introduction if warranted):
- 8 phases → 4
- 8-file audit bundle → 4 files
- A1–A8 Operating Rules → A1–A4
- Tier-A/B/C gating (deferred)
- Cross-grader subagent (deferred)
- Two execution modes framing (replaced by single CLI with `--run-root`)
- Run-id format spec (replaced by UTC ISO directory name)
- Byte-equality stop condition (replaced by verifier-pass stop)
- Skill prompt immutability rule (moved to Stage B attestation contract)
