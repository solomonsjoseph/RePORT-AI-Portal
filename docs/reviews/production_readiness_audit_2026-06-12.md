# Production-Readiness Audit — 2026-06-12

Comprehensive audit of branch `PHI_handing_review`: live pipeline run, PHI containment,
code quality, eval harnesses, and documentation. Audit mode: auto-fix low-risk findings,
hold high-risk items for maintainer review.

## Scorecard

| Dimension | Result | Evidence |
|---|---|---|
| Pipeline flow | ✅ | Fresh `extract_to_llm_source run --study Indo-VAP` (cache cleared): 37 forms approved, 0 held, exit 8 (partial — 58 quarantined rows, see residuals). Verifier: **all 14 assertions PASS, exit 0**. |
| PHI containment | ✅ | `scan_tree_for_phi(llm_source)` → ok, **0 findings**. Assertion 8 (PHI absence) + 9 (no runtime keys) pass. Quarantine empty post-destruction. Only 3 tracked files under `data/` (manifest + .gitkeep). SoT force-drop cross-check verified live: `Image_Seq`/`Remote_Cmp` force-dropped on 98B_FOB. |
| PHI rules applied | ✅ | Assertion 12 (decided-vs-applied lattice) and 14 (ledger covers all columns) pass on fresh run. Every published column has a ledger event, keep_decision, or non-keep configured rule. |
| Tests | ✅ | Full deterministic suite green (431 tests) before and after all fixes. |
| Lint / format / types | ✅ | `ruff check` clean (3 errors fixed), `ruff format` clean (9 files reformatted). `mypy` unavailable in env (not installed) — held. |
| Tool-call accuracy/speed | ✅ | Track A: resolvability **1.0000**, routing **3/3 (100%)**, direct-read p50 0.62 ms. Track B (fake-local smoke): answered 100%, tools_used_ok 100%. |
| Documentation | ✅ | `make doc-freshness` OK; Sphinx builds **warning-free** after RST fix; no stale tool/exit-code references (one wording drift fixed). |
| Dead code / bloat | ✅ | Zero truly-dead production symbols. 3 TODO markers repo-wide. Untracked working artifacts now gitignored. |

## Root-cause note: the assertion-12 failure that was found and cleared

The first `verify` run failed assertion 12 (`98B_FOB.xlsx:Image_Seq decided=drop applied=keep`).
Investigation showed the published bundle was **stale output produced in the old
`RePORT-AI-Portal` checkout** (run dirs `run_36b…`/`run_d9e4…` reference that absolute path),
predating the SoT force-drop cross-verification. The step cache ("inputs unchanged") had
skipped re-extraction. A fresh publish through the trusted wrapper applied the SoT-driven
force-drop correctly and the verifier passed 14/14. **The gate logic itself was correct —
it caught real drift.** This validates the decided-vs-applied design.

## Fixes applied (commits on `PHI_handing_review`)

- `c0d1592` style: ruff lint/format sweep (incl. clearer `_sot_confirms_benign` return in
  `phi_review.py`); exit-10 description now matches assertion-14 neither/nor logic in both
  the module docstring and `_STATUS_BANNER`; `cloud_eval.py` uses `config._infer_provider`
  (import idiom).
- `29b0f06` chore/docs: `.gitignore` entry for `docs/abstracts/`; fixed RST enumeration for
  the verifier's 12→14→13 execution order in `extract_to_llm_source.rst` (Sphinx now
  warning-free).
- `2ceac0c` docs(eval): refreshed eval results — Q-D3 (98A_FOA joined view) now resolves;
  100% across the board.

## Held findings (maintainer decision required)

1. **Logger convention conflict** — 23 production modules use `logging.getLogger(__name__)`
   while CLAUDE.md mandates `get_logger()`. But `get_logger()` triggers `setup_logging()`
   (creates `.logs/` + file handlers) on first call — converting module-level loggers would
   add filesystem side effects to bare imports. Decide: relax the documented convention for
   library modules, or make `get_logger()` lazy. Do not bulk-convert as-is.
2. **Stale cross-checkout artifacts under `output/Indo-VAP/`** — `trio_bundle/` (pre-Phase-5b
   legacy; `make clean-legacy` handles it) and run dirs `run_36b…`/`run_d9e4…` whose
   `status.json` paths point at the old `RePORT-AI-Portal` checkout. Recommend pruning after
   confirming nothing references them. Also: the shell `VIRTUAL_ENV` still points at
   `RePORT-AI-Portal/.venv` (harmless under uv, but noisy — unset it).
3. **58 quarantined `date_unshiftable` rows** across 8 forms (largest: 35 in
   3_Specimen_Collection, 12 in 96_Specimen_Tracking; none elevated). These are data-quality
   tails, individually correct fail-closed outcomes. Because exit is 8 (partial), Step 7 does
   **not** commit a snapshot — resolving these rows (or accepting the partial) is what stands
   between the run and a committed immutable snapshot.
4. **Test-only modules** — `scripts/artifact_versions.py` and `scripts/ai_assistant/ui/wizard.py`
   have no production callers (test-referenced only). Wire in or remove deliberately.
5. **mypy not installed** in the dev group — type-check claim unverified this audit.

## Production-readiness confidence: **≈90% — production-ready for its IRB-gated purpose**

**Proven, not inferred:** live end-to-end publish; 14/14 verifier assertions; zero leak-gate
findings; 100% eval resolvability/routing; 431 green tests; warning-free docs build.

**The remaining ~10%:** held findings 1–5 above, plus two structural residuals — no committed
snapshot for the current bundle (blocked only by the 58-row quarantine tail), and the
single-study (Indo-VAP) validation surface: a second study exercising the manifest/locale/
duplicate machinery would materially de-risk generalization. None of the residuals is a PHI
leak or a correctness defect; all are hygiene, convention, or data-quality items with named
owners' decisions attached.
