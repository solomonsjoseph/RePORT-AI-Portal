# Indo-VAP — Date data-quality & leak-gate findings (2026-06-08)

Surfaced during a **full clean rebuild** of `output/Indo-VAP/` (host publish path)
while verifying the Form 14 SoT-binding fix. These are **pre-existing
source-data and heuristic issues** — none were introduced by the Form 14 / FID
fixes. They are documented here for **data-owner / security-team remediation**;
they are NOT a blocker for the current published bundle (see "Impact").

## What works (verified this pass)

- The pipeline runs end-to-end: SoT generation (28 forms, deterministic),
  dataset extraction (37 forms / 55,488 records), PHI header review (held 2
  forms via the by-design Option-C coverage hold), and the PHI scrub.
- The PHI **fail-closed safety gates work**: the scrub refuses to emit dates it
  cannot safely jitter rather than guessing or leaking them.
- Form 14 SoT now binds the kept dataset (`14_Case_Control.xlsx`, 128 cols).
- The **production** leak gate (dataset-files scope) is **clean** on the
  published bundle — no PHI residuals in patient data.

## Why a fully-fresh dataset rebuild cannot complete cleanly today

The PHI date-jitter is fail-closed: a date it cannot parse/shift quarantines
the row and aborts the run (this replaced a pre-T2.3 *passthrough* that leaked
raw values — i.e. the current behavior is **safer**). A fresh rebuild on the
current strict code surfaces these source-data conditions, one form at a time.

### Category A — date columns mislabeled DMY but actually MDY (locale fix)

Evidence: a subset of values are *impossible* under DMY (month > 12, e.g.
`03/13/2020`) yet **all** values parse under MDY. Declaring DMY both fail-closes
the scrub on the impossible rows AND silently month/day-swaps the parseable
ones (so even the "working" rows had wrong dates).

Columns (form-scoped; counts = rows that fail DMY but pass MDY):

| Form | Column(s) |
|------|-----------|
| 101_HHC_Recontact | `RE_VISDAT`, `RE_COMPDAT` (19/43 impossible-as-DMY), `RE_CAREDAT`, `RE_TBDIAGDAT` |
| 7_Culture | `CX_PROCDAT`, `CX_VISDAT` |
| (others) | `CM_COMPDAT`, `CM_MBDAT2`, `FUA_PREGOUTDAT_2`, `FUB_COMPDAT_2`, `TC_xraydat` |

**Fix:** set these columns to `MDY` under `date_locales:` in
`data/raw/Indo-VAP/_forms_manifest.yaml` — **after data-owner confirmation**
that they are MM/DD/YYYY at source (date interpretation is clinically
significant). This is the intended use of the `date_locales:` override.

### Category B — isolated malformed values in otherwise-clean columns

A handful of rows hold a date-shaped but invalid value (e.g. `IS_COMPDAT`
1/1652, `SC_BLDDAT` 3/4985, `99A_FSA.FA_VISDAT` 2/1328). These are individual
data-entry errors. **Fix:** correct at source, or null the offending cells.

### Category C — non-date data in date-named columns

| Column(s) | Observed format | Nature |
|-----------|-----------------|--------|
| `SC_GENOPROCDAT`, `ST_LOUTDAT`, … | `YYYYMMDD` (8 digits, no separator) | real dates in a **compact format the parser does not support** |
| `CX_PROCDAT_ND`, `SC_LNDATND` | single digit (`0/1/9`) | a **Not-Done flag**, mis-detected as a date by name |
| `ZN_MBDATNR`, `ZN_MBDATNR2` | free text | **free-text reason**, mis-detected as a date by name |

**Fix options:** (a) extend `parse_date` to accept compact `YYYYMMDD`;
(b) tighten the date-field detector (`field_is_date`) so `*_ND` flag columns and
free-text reason columns are not treated as dates; (c) reformat at source.

## Leak-gate metadata false-positives (whole-tree / snapshot scan only)

`scan_tree_for_phi` over the *whole* `llm_source/` tree (run on **snapshot
activation**, not on the main publish) flags column **names** and dictionary
**documentation**, not patient values:

- **FID column names** `FID`, `FID2`…`FID5` in SoT schema metadata matched the
  `SUBJECT_ID` heuristic. **FIXED** this pass: the `FID` pattern now requires
  ≥4 digits (`\bFID\d{4,}\b`), matching real FID *values* but not the
  family-member-index header tokens. (Confirm real FID values are ≥4 digits.)
- **Dictionary doc text** `dictionary_mapping/.../Codelists`: the literal
  `"Use 1900-01-01 as an Unknown date"` matches `DATE_ISO`. **Open** — this is
  help text, not PHI. Options: allowlist dictionary documentation, or scope the
  snapshot whole-tree scan to data + schema (excluding dictionary prose).

## Impact / recommendation

- **Current published bundle is usable** for the chat: readiness OK, production
  (dataset-only) leak gate clean, Form 14 fixed.
- A **100%-fresh dataset rebuild** is blocked until Categories A–C are resolved
  by the data owner (locale confirmation + source corrections + a small parser/
  detector enhancement). Recommend addressing A (locale, highest data-impact)
  and the date-field-detector part of C first.
