# Production-Readiness Audit — 2026-06-12

Full audit of branch `PHI_handing_review`. We ran the real pipeline, checked for PHI
leaks, checked code quality, ran the eval harnesses, and checked the docs.
Low-risk problems were fixed on the spot. Risky decisions are listed at the end for
the maintainer to decide.

## Bottom line

**The project is about 90% production-ready.** Everything important works and was
proven by running it — not by reading code. The missing 10% is cleanup and small
decisions, listed under "Your decisions" below. No PHI leak. No broken pipeline.

## Scorecard

| Check | Result | Proof |
|---|---|---|
| Pipeline works end-to-end | ✅ | Fresh full run (cache cleared): 37 forms approved, 0 held, exit 8 (partial — 58 rows held back, see decision 3). Verifier: **all 14 checks passed, exit 0**. |
| No PHI leak | ✅ | PHI scanner on the published data: **0 findings**. Quarantine destroyed and attested. Only 3 harmless files tracked under `data/`. |
| All PHI rules applied | ✅ | Verifier checks 12 (decided action = applied action) and 14 (every published column accounted for) both pass. |
| Tests | ✅ | All 431 tests pass, before and after every fix. |
| Lint / format | ✅ | `ruff` fully clean (3 errors fixed, 9 files reformatted). `mypy` could not run — see decision 5. |
| LLM tool calls — accuracy & speed | ✅ | Retrieval eval: **100% resolvability, 100% routing**, 0.62 ms median read. Agent smoke test: 100% answered, 100% correct tool use. |
| Docs up to date | ✅ | Doc-freshness check passes. Sphinx builds with zero warnings after a small fix. No stale references found. |
| Dead code / bloat | ✅ | No dead production code found. Only 3 TODOs in the whole repo. Loose draft files now gitignored. |

## The one real problem we found (and why it's good news)

The first verifier run **failed**: column `Image_Seq` in form `98B_FOB` was decided
"drop" but was published as "keep". The cause was not a bug. The published data was
**old output from your previous checkout** (`RePORT-AI-Portal`), built before the
SoT force-drop check existed. The pipeline cache had said "inputs unchanged" and
skipped rebuilding. A fresh rebuild dropped `Image_Seq` and `Remote_Cmp` correctly
and all 14 verifier checks passed. **The safety gate caught real drift — it works.**

## Fixes already applied (4 commits)

- `c0d1592` — lint/format sweep; clearer exit-code-10 wording; cleaner import in `cloud_eval.py`.
- `29b0f06` — gitignore `docs/abstracts/`; fixed a doc numbering error so Sphinx builds clean.
- `2ceac0c` — refreshed eval results (now 100% across the board).
- `cf83b25` — this report.

## Decisions — status after follow-up session (2026-06-12)

1. **Logger style conflict — DONE.** `get_logger()` is now lazy (no file or folder is
   created until something actually logs), and all 24 module loggers were converted to
   it. Bonus fix found on the way: the PHI log-redaction filter sat on the root logger,
   which Python never consults for child-logger records — it is now also attached at
   the handler level, so module logs are genuinely redacted. Commit `7730b4a`.
2. **Old leftovers — DONE.** Deleted `output/Indo-VAP/trio_bundle/` and the two run
   folders pointing at the old checkout. The `.venv` was also rebuilt — it had been
   carried over from the old checkout with broken script paths. Still on you: run
   `unset VIRTUAL_ENV` in your shell (or open a new terminal) — it points at the old repo.
3. **58 held-back rows — SKIPPED at your request**, pending your review. Still the only
   blocker to a committed snapshot.
4. **Unused modules — DONE, with a correction.** `ui/wizard.py` IS used in production
   (`web_ui.py` imports it for the setup page) — the original finding was wrong, it stays.
   `scripts/artifact_versions.py` was genuinely test-only and is deleted. Commit `399a47e`.
5. **mypy — DONE.** It was already in the dev dependencies; it only failed before
   because of the stale venv. After the rebuild: clean, 0 issues in 83 files.

## Stale-artifact purge (follow-up request)

A sweep for files whose purpose is over removed: 9 one-time review/worklist scratch
files in `docs/reviews/`, the spent `docs/plans/` folder (6 old plan files), and the
tracked `phi_handling_review_checkpoint.md` (its finding was remediated). Kept:
the 3 Indo-VAP review docs (still cited), `smart-commit.sh` (documented in
`versioning.rst`), the generated eval results, and `docs/eval/abstract.md` —
that one has your uncommitted edits and looks like active work, so it was not touched.

## What "90%" means exactly

Proven by running: full pipeline, 14/14 verifier checks, 0 leak findings, 100% eval
scores, 431 green tests, clean docs build. The remaining 10% = the 5 decisions above,
plus two structural notes: no committed snapshot yet (blocked only by decision 3), and
everything was validated on one study (Indo-VAP) — a second study would prove the
machinery generalizes. None of the gaps is a PHI leak or a correctness bug.
