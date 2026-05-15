# SoT Build Runbook — Source-of-Truth YAML generation for a clinical study

This runbook is the **full behavior reference** for
`python -m scripts.source_truth.study_intake`. It covers inputs, outputs, the
pairing algorithm, exclusion reason codes, re-run policy, the headers-only
data-isolation threat model, and a concrete walkthrough on Indo-VAP. It is
written for any agentic LLM tool or developer who has not previously seen this
codebase.

Target study throughout: `Indo-VAP`.

## Prerequisites

Confirm each of these before running step 1.

- **Python environment is available via `uv`.** Run all commands as
  `uv run --all-groups python -m scripts.source_truth.study_intake …`.
  The repo's `.venv` is Python 3.9; `uv` selects the correct interpreter
  (3.11+) automatically.

- **Raw data is laid out** under `data/raw/<study>/`:

  ```
  data/raw/Indo-VAP/
  ├── annotated_pdfs/   ← PDF forms, already annotated upstream
  └── datasets/         ← xlsx and/or csv files, one per CRF form
  ```

  Neither directory is created by this CLI. If either is absent the CLI
  exits with a descriptive error.

- **Annotated PDFs are present.** The PDFs are assumed to be pre-annotated
  upstream (PDF annotation tooling is out of scope for this CLI). Forms whose
  PDF is missing or unreadable are routed to `SoT_intake_review.md` with the
  `unpaired_pdf` reason code.

- **PHI scrub config is NOT required** for this CLI. The headers-only
  invariant means the dataset rows are never read; the scrub layer is
  a downstream concern. The only PHI-guard applied here is a pattern check
  on column-header strings (see §6).

## 1. Inputs

### Dataset directory: `data/raw/<study>/datasets/`

Each file must be `.xlsx` or `.csv`. Other extensions are routed to
`SoT_intake_review.md` (reason: `unsupported_extension`).

**Filename convention and form-code prefix rule:**
The CLI normalizes filenames before pairing: lowercase, strip extension, strip
version suffixes (`v1.0`, `v2`, etc.), collapse runs of `[\s_\-\.]+` to `_`.
The normalized name's leading form-code prefix — `^[0-9]+[A-Z]?` (e.g. `1A`,
`12`, `101`) — is what drives pairing. The portion after the prefix is treated
as a human-readable label and ignored during matching.

Example: `1A_ICScreening.xlsx` normalizes to `1a_icscreening`; its prefix is
`1a`. A PDF named `1A Index Case Screening v1.0.pdf` also normalizes to prefix
`1a`. They are paired.

**Duplicate handling:**
- Two files with the same prefix and identical SHA-256 → deduplicated silently.
- Two files with the same prefix but different SHA-256 → both routed to review
  (reason: `duplicate_mismatch`).

### PDF directory: `data/raw/<study>/annotated_pdfs/`

Only files named `*.pdf` are processed. Form-code prefix extraction follows the
same normalization rules as datasets.

## 2. Running the build

```bash
# Standard run — skips any YAML that already exists on disk
uv run --all-groups python -m scripts.source_truth.study_intake Indo-VAP

# Force-overwrite all YAMLs (including human-curated files — use with care)
uv run --all-groups python -m scripts.source_truth.study_intake Indo-VAP --force

# Show all options
uv run --all-groups python -m scripts.source_truth.study_intake --help
```

`--help` output (spec — Agent A is implementing the module in parallel; flags
are from the phase plan, not from a live binary):

```
usage: python -m scripts.source_truth.study_intake [-h] [--force] study

Build Source-of-Truth YAMLs for a clinical study.

positional arguments:
  study       Study name matching a directory under data/raw/, e.g. Indo-VAP

optional arguments:
  -h, --help  show this help message and exit
  --force     Overwrite existing YAMLs. Default: skip files already on disk.

Inputs:  data/raw/<study>/annotated_pdfs/*.pdf
         data/raw/<study>/datasets/*.{xlsx,csv}
Outputs: data/SoT/<study>/<form>_policy.yaml  (one per aligned pair)
         data/SoT/<study>/human_review/SoT_intake_review.md

Full runbook: docs/runbook_sot_build.md
```

**Success:** Console prints a summary line such as:

```
Indo-VAP: 31 aligned → 29 YAMLs written, 2 skipped (already exist).
          2 items routed to human_review/SoT_intake_review.md.
```

Exit code is `0` if every file was either built or routed to review. Exit code
is non-zero only on unexpected failures (LLM error, schema validation failure,
unreadable input directory).

## 3. Outputs

### `data/SoT/<study>/<form>_policy.yaml` — one per aligned pair

Written only for pairs where:
1. Exactly one PDF and one dataset share the same form-code prefix.
2. `read_headers_only` succeeds (no exclusion reason fires).
3. The extractor agent + reviewer agent complete without error.
4. The resulting YAML validates against `scripts/source_truth/record.py` schema.

Schema version is `2`. Top-level keys include `schema_version`, `pdf_sections`,
`option_sets`, `pdf_visible_text`, `variables`, and `policy_status`.

**Success:** `data/SoT/Indo-VAP/1A_ICScreening_policy.yaml` (and 30 peers)
exist and are valid YAML with `schema_version: 2`.

### `data/SoT/<study>/human_review/SoT_intake_review.md` — checklist for everything else

Single Markdown file. Each entry has a typed reason code (see §4), a file path,
a one-line note, and a blank `Disposition:` field for the human reviewer to fill.

Example entry:

```markdown
## Unpaired PDFs (no matching dataset)
- [ ] **9_EEval** — `data/raw/Indo-VAP/annotated_pdfs/9 Environmental Eval v1.0.pdf`
      Reason: `unpaired_pdf`
      Notes: No xlsx/csv with canonical key `9_eeval` found.
      Disposition: __________ (add_with_override / delete / keep_in_review / rename)
```

On re-run the CLI respects `- [x]` checked items and `Disposition: <verb>` lines
— it does not re-append entries that have already been triaged.

Sections are suppressed when empty (no entries for that reason on a given run).

**Success:** `data/SoT/Indo-VAP/human_review/SoT_intake_review.md` exists and
contains at minimum the 2 migrated exclusions (`30_Air_Quality`,
`96_Specimen_Tracking`).

## 4. Exclusion reason codes

The following 8 reason codes are the only values that appear in
`SoT_intake_review.md`. They are derived verbatim from phase plan §6.

| Code | Meaning |
|---|---|
| `unpaired_pdf` | A PDF exists in `annotated_pdfs/` but no dataset shares its form-code prefix. |
| `unpaired_dataset` | A dataset exists in `datasets/` but no PDF shares its form-code prefix. |
| `empty_header_row` | Row 1 of the xlsx/csv is absent, all-None, or all-blank strings — the CLI never guesses a row. |
| `multi_sheet_workbook` | The xlsx has more than one visible sheet — the CLI does not pick one silently. |
| `formula_header` | At least one cell in row 1 starts with `=` — formula headers are excluded to prevent cached-value leakage. |
| `phi_in_header` | At least one header string matches a pattern in `scripts/security/phi_scrub.py` — the column name itself looks like a PHI value. |
| `duplicate_mismatch` | Two or more files share the same form-code prefix but have different SHA-256 digests — the CLI cannot know which is canonical. |
| `fuzzy_match_low_confidence` | A pair was found only via fuzzy string matching below the confidence threshold — the CLI routes it to review rather than pair silently. |

## 5. Re-run policy

**Default (skip-if-exists):** If `data/SoT/<study>/<form>_policy.yaml` already
exists on disk, the CLI skips that pair and logs `SKIP <form> (already exists)`.
This protects human-curated `policy_status` values, hand-edited
`unresolved_review_required` lists, and any other post-generation edits.

**`--force` (overwrite):** Passing `--force` regenerates every YAML regardless
of what is on disk. All human curation in the existing YAMLs is lost.

**Warning:** Never run `--force` against `data/SoT/Indo-VAP/` (or any study
with curated YAMLs) without explicit authorization from the data owner. The
`--force` flag is intended for fresh-checkout bootstrapping only, or for
re-running after a known schema change.

**Review file on re-run:** The CLI reads the existing `SoT_intake_review.md`
before writing. Entries with `- [x]` (checked) or a non-blank `Disposition:`
value are not re-appended. New exclusions discovered on re-run are appended
under their section.

## 6. Threat model — headers-only invariant

**Goal: row 2+ bytes of any xlsx/csv never enter the Python process.**

This is a process-safety guarantee (preventing inadvertent data loading during
SoT construction), separate from the downstream PHI-handling pipeline.

**How the invariant is enforced in code:**

- xlsx: `openpyxl.load_workbook(path, read_only=True, data_only=False)` +
  `ws.iter_rows(max_row=1, values_only=True)` + `next()` once + stop. The
  workbook is opened as a context manager; the file handle never escapes
  `read_headers_only`.
- csv: `csv.reader(f)` + `next(reader)` once + stop. No `readline()`,
  no `read()`, no `Sniffer`.
- Return type is `list[str]`. The caller receives strings only — no worksheet
  objects, no file handles, no row iterators.

**PHI patterns:** After extraction, each header string is passed through the
same regex patterns used by `scripts/security/phi_scrub.py`. A match triggers
the `phi_in_header` exclusion — the form is routed to review, not processed.

**What tests guard the invariant:**

| Test file | What it checks |
|---|---|
| `tests/source_truth/test_study_intake_phi_canary.py` | Row 2 of a fixture xlsx contains `PHI_POISON_CANARY_XYZ`; asserts the canary never appears in any output of `read_headers_only`, any LLM call (agents mocked), or any written YAML. |
| `tests/security/test_study_intake_static.py` | Greps `scripts/source_truth/study_intake.py` for forbidden tokens: `pandas`, `.comment`, any `max_row=` value other than `1`, `Sniffer`. Any match is a test failure. |
| `tests/source_truth/test_study_intake_exclusions.py` | One test per exclusion reason; each asserts the correct entry appears in `SoT_intake_review.md` under the correct section. |

**Additional mitigations (from phase plan §7 threat table):**

- `wb.defined_names` is never called (named-range enumeration forbidden).
- `.comment` access is banned; enforced by static-analysis test.
- `data_only=False` is pinned to prevent formula cells from resolving to cached
  values; a `=` prefix in any header still triggers `formula_header` exclusion.
- Multi-sheet workbooks are excluded (`multi_sheet_workbook`) rather than
  silently picking a sheet.
- CSV delimiter misdetection is prevented by requiring `,` for `.csv` and `\t`
  for `.tsv`; `csv.Sniffer` is forbidden.

## 7. Example walkthrough — Indo-VAP

**Scenario:** Fresh checkout; no YAMLs exist yet under `data/SoT/Indo-VAP/`.

**Step 1 — Confirm inputs exist:**

```
data/raw/Indo-VAP/
├── annotated_pdfs/   (33 PDF files)
└── datasets/         (31 xlsx files)
```

**Step 2 — Run:**

```bash
uv run --all-groups python -m scripts.source_truth.study_intake Indo-VAP
```

**Expected console output (sketch):**

```
[study_intake] Indo-VAP: scanning annotated_pdfs/ (33 PDFs) + datasets/ (31 xlsx)
[study_intake] Pairing by form-code prefix …
[study_intake]   31 aligned pairs
[study_intake]    2 items → human_review (unpaired_pdf: 9_EEval, 30_Air_Quality)
[study_intake] Building YAMLs for 31 aligned pairs …
[study_intake]   1A_ICScreening … OK
[study_intake]   1B_IndexCase … OK
  … (29 more) …
[study_intake] Done. 31 YAMLs written. 2 items in SoT_intake_review.md.
```

**Expected files emitted:**

```
data/SoT/Indo-VAP/
├── 1A_ICScreening_policy.yaml
├── 1B_IndexCase_policy.yaml
├── … (29 more _policy.yaml files)
└── human_review/
    └── SoT_intake_review.md
```

**Step 3 — Re-run (idempotent):**

Running the same command again produces:

```
[study_intake] Indo-VAP: 31 aligned pairs.
[study_intake]   31 YAMLs skipped (already exist). Pass --force to overwrite.
[study_intake]   2 items in SoT_intake_review.md already triaged — no new entries.
```

Exit code `0`. Nothing on disk changes.

## 8. Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `ExcludeForReview: duplicate_mismatch` for a form | Two xlsx files in `datasets/` share the same form-code prefix but differ in content (e.g. `1A_ICScreening.xlsx` and `1A_ICScreening_v2.xlsx`). | Remove or rename the stale copy; re-run. |
| `ExcludeForReview: multi_sheet_workbook` | The xlsx has more than one visible sheet. | Delete or hide extra sheets; re-run. Or accept the entry in `SoT_intake_review.md` and add a manual `add_with_override` disposition. |
| `ExcludeForReview: formula_header` | A column header in row 1 begins with `=`. | Edit the xlsx to replace the formula with its literal string value; re-run. |
| `ExcludeForReview: phi_in_header` | A column name matches a PHI pattern in `scripts/security/phi_scrub.yaml`. | Review the header; if it is a false positive, update the scrub config allowlist and re-run. |
| YAML already exists but is stale / wrong schema | A prior run used an older extractor agent. | Run with `--force` after confirming human curation in the existing YAML can be discarded or re-applied. |
| `FileNotFoundError: data/raw/<study>/datasets/` | Raw data directory absent. | Confirm the study name matches the directory name under `data/raw/` exactly (case-sensitive). |
| `FileNotFoundError: data/raw/<study>/annotated_pdfs/` | No annotated PDFs staged. | Annotate PDFs upstream before running the intake CLI. |
| `record.validate_record` raises on a generated YAML | The extractor agent produced output that violates the `schema_version: 2` contract in `scripts/source_truth/record.py`. | Check the extractor agent prompt for regressions; file a bug; the form is not written to disk (the error surfaces in the console). |

---

Source-of-truth for the full architectural rationale, threat model, and open-question
resolutions:
`docs/superpowers/plans/2026-05-15-phase-6-sot-skill-refactor.md`
