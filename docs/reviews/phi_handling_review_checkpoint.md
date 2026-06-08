# PHI_handing_review — Five-Axis Review Checkpoint

**Status:** IN PROGRESS (resume point). Written 2026-06-04 so review state survives `/compact` or `/exit`.

## Goal of the review (user's words, restated)
Verify that the **plugin** (`plugins/report-ai-study-pipeline`, which replaces the old pipeline)
correctly produces, from raw study data, an `output/` folder split into
**(a) audit files** documenting every PHI-handling step and **(b) cleaned, PHI-free output**,
so that **chat-UI users querying existing study data get PHI-free answers**.
The branch's intent: *simplify* the process, *raise accuracy before & after* PHI handling,
*document every step for audit/IRB defensibility*, and use the *best PHI method* — drop what must
be removed while **keeping clinically-useful data** (avoid over- and under-redaction).

Axis weighting: **Security + Correctness heaviest** (PHI escape, quarantine bypass, sandbox read
boundary, over-redaction); Architecture/Readability/Performance secondary.

## Scope
Whole `PHI_handing_review` branch vs `main` (merge-base `6091cdd`): 264 files, ~40.6k ins / 25.9k del,
223 commits. Reviewed by **resulting state of touched files**, not line-by-line diff.

## HARD constraint (AMBER zone)
Never read `data/raw/*` or `tmp/*` (may contain real PHI). Review only `scripts/`, `plugins/`,
`main.py`, configs, `docs/`, and synthetic `tests/fixtures/`.

## Confirmed finding (first-hand, before workflow synthesis)
**[Important->Critical] Read-boundary enforced on inline render but NOT on ZIP export.**
- `validate_agent_read` (`scripts/ai_assistant/file_access.py:85`) restricts agent reads to the
  cleaned `STUDY_LLM_SOURCE_DIR` + `AGENT_STATE_DIR` zones, denies the audit zone, allowlists only
  `config/study_knowledge.yaml`.
- Inline figure render **calls it** (`scripts/ai_assistant/ui/streaming.py:871`). OK
- But `_export_plots_as_zip` (`scripts/ai_assistant/ui/conversations.py:583-684`) **never calls it** -
  after candidate-path resolution (now incl. `repo_root / clean_str` and bare absolute paths, broadened
  by commit `2f89365`) it goes straight to `p.read_bytes()` (line 618) / `p.read_text()` (line 683).
- Impact: an agent-emitted figure-marker path pointing outside the safe zones (absolute, or `../`
  traversal) would be read and bundled into the user's ZIP download - bypassing the boundary the inline
  path enforces. PHI-facing download path with zero zone validation.
- Fix: route both `read_bytes`/`read_text` in `_export_plots_as_zip` through `is_agent_readable` /
  `validate_agent_read` (skip-on-violation), and prefer basename-only candidates over full-string ones.

## Workflow (recover its findings to finish the review)
- Script: `~/.claude/projects/.../21474d7f-.../workflows/scripts/phi-pipeline-five-axis-review-wf_61e205ae-b07.js`
- Run ID: `wf_61e205ae-b07`  (killed by `/exit` mid-run; resume to recover synthesized findings)
- Resume: `Workflow({scriptPath: "<above>", resumeFromRunId: "wf_61e205ae-b07"})` - cached agents return instantly.
- Returns `{reviewed:[9 subsystem reviews w/ verified findings], critic, traceAvailable}`.

### 9 subsystems under review
1. PHI scrubbing engine (drop-vs-keep, Verhoeff/Aadhaar/phone) - `scripts/security/phi_scrub.{py,yaml}`, `phi_patterns.py`, `phi_redactor.py`, `phi_id_masker.py`, `phi_allowlist.py`
2. PHI gates & quarantine (the guarantee) - `phi_gate.py`, `kanon_gate.py`, `llm_source_gate.py`, `phi_review.py`, `phi_safe.py`, `audit/zone_guards.py`
3. Extraction pipeline -> cleaned output - `scripts/extraction/dataset_pipeline.py`, `dataset_cleanup.py`, `dedup.py`, `io/sheet_split.py`, ...
4. extract_to_llm_source (1444-line new core) - `scripts/skills/extract_to_llm_source.py`
5. Audit ledger & provenance - `scripts/audit/ledger.py`, `zone_guards.py`
6. SoT / lean output - `scripts/source_truth/*`, `sot_loader.py`, `sot_joined_view.py`
7. Plugin = pipeline replacement - `plugins/report-ai-study-pipeline/**`
8. Chat UI query path (PHI-free at query time) - `agent_tools.py`, `citations.py`, `file_access.py`, `sandbox/runner.py`, `ui/streaming.py`, `ui/conversations.py`
9. Docs vs implementation (IRB defensibility) - `docs/sphinx/{developer_guide,irb_auditor,user_guide}/*`

## Next steps
1. Resume workflow -> collect `{reviewed, critic}`.
2. Synthesize five-axis report (Critical / Important / Suggestion, file:line), corroborating top
   findings against the code first-hand (as done for the ZIP-export gap).
3. Write final report next to this file; THEN compaction is safe.

## Model policy (user directive 2026-06-04)
Use Sonnet/Haiku where useful to cut tokens: Sonnet for execution/mechanical agent work, Haiku for
tests, Opus reserved for planning/review/synthesis. Do NOT edit the dead workflow script to swap
models — that invalidates the resume cache and re-runs everything.
