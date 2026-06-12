# Decision 3 — the 58 quarantined date rows (review package)

This is the one item that needs your eyes on actual data values. Everything in this
file is PHI-safe (form names, counts, field names, masked shapes only). The shapes
mask every digit as `9` and every letter as `X` — so `99999999` means "8 digits",
not literally all nines.

## What happened

During the fresh Indo-VAP publish, 58 rows (out of ~25,000 published) had a date
value the parser could not safely interpret. Fail-closed rules quarantined each row;
the rest of every form published normally. No form was flagged elevated.

## Where they are

| Form | Rows held | Field(s) seen | Masked shape(s) seen |
|---|---|---|---|
| 3_Specimen_Collection | 35 | (not captured per-field) | — |
| 96_Specimen_Tracking | 12 | ST_LOUTDAT, ST_LNDAT | `99999999`, `9999999`, `9` (8 / 7 / 1 digits) |
| 7_Culture | 3 | — | — |
| 98A_FOA | 3 | FOA_VISDAT | `99/99/9999` |
| 13_TxCompliance | 2 | — | — |
| 101_HHC_Recontact | 1 | — | — |
| 19_Smear | 1 | — | — |
| 98B_FOB | 1 | FOB_VISDAT | `99/99/9999` |

All reason codes are `date_unshiftable` — the value reached the date-jitter rule but
could not be parsed into a real calendar date (invalid date, ambiguous layout, or
year out of the 1900–2100 plausible range). Note: genuine all-9 placeholder values
(`99999999` etc.) are already kept as missing-data sentinels, so these are NOT
simple placeholders — they are digit strings that fail to parse as dates.

## What deciding requires (and why I stopped)

Telling whether each value is (a) a typo to fix at the source, (b) an unrecognized
missing-data convention to add to `date_null_tokens`, or (c) garbage to accept as
quarantined requires reading the raw cell values — which the audit rules forbid me
from doing. That's your call.

## How to review them yourself

The quarantine staging copies are destroyed after each run (by design), so look at
the source workbooks. For example, to see the distinct unparseable `ST_LOUTDAT`
values, open `data/raw/Indo-VAP/datasets/96_Specimen_Tracking.xlsx` and filter
`ST_LOUTDAT` for non-date-looking entries. Do the same per form above.

## Your three options

1. **Fix at source** — correct the workbook cells, delete
   `output/Indo-VAP/audit/.dataset_processing.manifest.json`, re-run. A clean run
   now auto-commits a snapshot (the Step-7 verifier bug is fixed).
2. **Declare conventions** — if some values are a known missing-data code, add them
   to `date_null_tokens` in `scripts/security/phi_scrub.yaml` and re-run.
3. **Accept the partial** — 58 rows out of ~25k stay held; every published row is
   correct and audited. This is a designed, documented state (exit 8), not an error.
   The only cost: no committed Indo-VAP snapshot until the run is fully clean.
