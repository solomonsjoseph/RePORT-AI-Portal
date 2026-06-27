# Planted-identifier synthetic benchmark

> Documentation pointer: this is a working-artifact README for the PHI manuscript
> head-to-head; durable docs live in `docs/sphinx`.

## Why this exists

Indo-VAP cannot score the **leakage axis**. Its raw corpus contains essentially no
regex-detectable structured identifiers (1 government-ID-shaped hit in 1,495,216
cells), so both RePORTal and stock Presidio leave 0 residual — *degenerate, not a
tie* (`../headtohead/FINDINGS.md`, Finding 1). To measure **recall** (does a tool
remove a planted identifier?) and **precision** (does it destroy benign clinical
data?) we need a corpus whose ground truth is known per cell. This is that corpus.

All values are **synthetic / fake**. The generator self-validates every label
against the production detector (`scripts/security/phi_patterns.py`): every "valid"
value provably trips the real ruler, every "invalid/placeholder" provably evades it.

## What got built

```
generate_synthetic.py          # the generator (declarative spec, one pass)
ground_truth.jsonl             # 4,680 rows — one per cell: category/placement/edge_case/is_identifier
ground_truth_columns.json      # per-column catalog + header_reveals_category flag
data/Synth-India/datasets/*.xlsx   # India-jurisdiction arm (6 forms)
data/Synth-US/datasets/*.xlsx      # US-jurisdiction arm (6 forms)
data/<arm>/_forms_manifest.yaml + _study_privacy.yaml   # so each arm can feed `make study`
```

Two jurisdiction arms, 6 CDISC-style forms each (40 rows/form = 16 systematic
edge-case rows + 24 bulk rows), modelled on the Indo-VAP form anatomy
(`SUBJID`/`FID` keys, form-prefixed columns, `*DAT` date columns).

| Form | Purpose |
|---|---|
| `01_DM_Demographics` | names, DOB, age, address, postal code, sex/race, visit date |
| `02_CON_Contact` | phone/mobile, fax, email, URL, IP, emergency contact, phone-in-note |
| `03_GID_GovIDs` | every structured government / health / financial ID for the arm |
| `04_LB_Specimen` | accession, device, biometric, photo, dates + **mislabeled** ID column |
| `05_AE_AdverseEvent` | free-text narrative with **embedded** name/phone/ID + eponym FP bait |
| `06_FU_FollowUp` | visit/death dates, age>89, vital + **embedded** ID in notes |

## Coverage: HIPAA Safe Harbor §164.514(b)(2) — all 18 identifiers

| # | HIPAA identifier | Column(s) | Arm |
|---|---|---|---|
| 1 | Names | `DM_FNAME/DM_LNAME`, `CON_EMERGNAME`, `AE_NARRATIVE` | both |
| 2 | Geographic < state | `DM_STREET`, `DM_CITY`, `DM_ZIP`/`DM_PIN` | both |
| 3 | Dates (all elements) | `DM_DOB`, `*_COMPDAT/VISDAT/COLLDAT/ONSETDAT/DTHDAT` | both |
| 3 | Age > 89 | `DM_AGE`, `FU_AGE` (89/90/92/95/103 boundary cells) | both |
| 4 | Telephone | `CON_MOBILE/CON_PHONE`, `AE_CONTACTLINE`, `CON_NOTE` | both |
| 5 | Fax | `CON_FAX` | both |
| 6 | Email | `CON_EMAIL` | both |
| 7 | SSN | `GID_SSN`, `LB_RESULT`/`AE_REFID`/`FU_NOTES` (US) | US |
| 8 | Medical record number | `GID_MRN` | US |
| 9 | Health-plan beneficiary | `GID_MEDICARE`, `GID_INSID` | US |
| 10 | Account numbers | `GID_BANKACCT`, `GID_CREDITCARD` | US |
| 11 | Certificate/license | `GID_DL`, `GID_PLATE`, `GID_VOTERID` (IN) | both |
| 12 | Vehicle identifiers | `GID_VIN`, `GID_PLATE` | US |
| 13 | Device identifiers/serials | `LB_DEVICE`, `LB_SERIAL` | both |
| 14 | URLs | `CON_URL` | both |
| 15 | IP addresses | `CON_IP` | both |
| 16 | Biometric identifiers | `LB_FINGERPRINT` | both |
| 17 | Full-face photos | `LB_PHOTO` (filename) | both |
| 18 | Any other unique number | `SUBJID`, `FID`, `LB_ACCESSION`, `GID_UHID/ABHA/GSTIN` | both |

## Coverage: India-specific identifiers (DPDPA / Aadhaar Act / ICMR)

`GID_AADHAAR` (Verhoeff-validated 12-digit) · `GID_PAN` · `GID_VOTERID` (EPIC) ·
`GID_PASSPORT` (Indian) · `GID_DL` (Indian) · `GID_RATION` · `GID_ABHA` (health ID) ·
`GID_UHID` · `GID_GSTIN` · `DM_PIN` (6-digit) · `CON_MOBILE` (+91, 6-9 start).

## The fairness lever: three placements

A header-based de-identifier can only catch PHI whose **column name** reveals it.
A value scanner must earn the rest. Every "hard" category is therefore planted in
more than one placement:

| placement | meaning | who should win |
|---|---|---|
| `named` (4,200 cells) | header reveals category (`DM_AADHAAR`) | header classifier (RePORTal) |
| `mislabeled` (160 cells) | benign header, cell holds the ID (`LB_RESULT` = an SSN) | value scanner (Presidio) |
| `freetext` (320 cells) | benign header, ID embedded in a sentence (`AE_NARRATIVE`) | value scanner (Presidio) |

This is what makes the benchmark fair: it is deliberately built so a header-only
system **loses** on `mislabeled`/`freetext`, and a value-only system **loses** on
precision and on categories with no value signature (age>89, bare PIN, ration card,
ABHA, UHID, device serials).

## The edge-case matrix (per category, the 16 systematic rows)

`valid` · `valid_formatted` (alt separators / country code) · `invalid`
(checksum-fail Aadhaar, broken-Luhn card, wrong-shape) · `placeholder` (repeating
sentinel) · `null_unk` / `null_na` / `null_dot` / `empty` · `embedded` (inside a
sentence) · `prefixed` (`PIN:`, `+91`, `+1`) · `whitespace_case` (leading/trailing
space + lowercase).

Dates additionally exercise: ISO · DMY · MDY · ambiguous d/m · 2-digit year ·
textual (`14 Mar 2021`) · datetime · **future** (>`plausible_max_year`) · all-9s /
all-0s sentinels · partial (year only).

Age exercises the HIPAA boundary explicitly: 89 (benign) vs 90/92/95/103
(identifier → must cap).

## Ground-truth schema (`ground_truth.jsonl`)

```json
{"study":"Synth-US","jurisdiction":"USA","form":"03_GID_GovIDs","row":0,
 "column":"GID_SSN","category":"ssn","placement":"named","edge_case":"valid",
 "is_identifier":true,"note":"canonical"}
```

- **`is_identifier`** is the recall denominator: `true` = a real identifier is present
  and a correct system must remove/transform it. A checksum-invalid Aadhaar or a
  repeating-digit placeholder is `false` (not a real identifier — a value validator
  ignoring it is *correct*, not a miss).
- **`category` + `is_identifier=false` + `category=="non_phi"`** is the precision
  denominator: 560 benign cells (incl. 32 clinical eponyms like *Koch*/*Mantoux* and
  32 clinical phrases) that a correct system must **leave untouched**.

## What the benchmark is built to reveal (the hypothesis, both directions)

| Axis | Expected RePORTal | Expected stock Presidio |
|---|---|---|
| Structured IDs in `named` columns | removes whole column (recall 1.0) | per-value, misses no-signature cats (UHID/ABHA/ration/device) |
| IDs in `mislabeled`/`freetext` | **at risk** — header is benign; relies on value safety-net / free-text rules | should catch (value-based) |
| Plain names in free text | **at risk** — only `Dr. X` matches its value ruler | NER (`PERSON`) should catch |
| Age > 89 | caps (HIPAA §164.514(b)(2)(i)(C)) | no AGE recognizer → **misses** |
| Bare PIN / ZIP, ration, ABHA | header-classified → removed | no signature → **misses** |
| Precision (benign cells, eponyms) | 0 false positives by design (header-scoped) | false positives expected (US recognizers, PERSON on eponyms) |
| Dates | jitter (interval-preserving) | destroys |

The honest expectation: **RePORTal wins recall on structured/no-signature PHI and on
precision; Presidio wins recall on free-text names and on identifiers hidden under
benign headers.** That two-sided result is the point — it tells the manuscript exactly
where each approach's residual risk lives.

## Run

```bash
uv run --all-groups python docs/eval/synthetic_phi_benchmark/generate_synthetic.py
uv run --all-groups python docs/eval/synthetic_phi_benchmark/generate_synthetic.py --selfcheck-only
```

## Scoring (BUILT + RUN — see `SCORE.md`)

`score_synthetic.py` drives the **production** RePORTal engines (`classify_headers` +
the OR-combined publish gate) and **stock Presidio** over both arms, joins every cell
to `ground_truth.jsonl`, and emits a per-category / per-placement confusion matrix
(`score_results.json`). Count-only, no value leaves the harness.

```bash
uv run --all-groups python docs/eval/synthetic_phi_benchmark/score_synthetic.py
```

**Result (2026-06-26, after the Note 34 fixes):**

| | RePORTal | stock Presidio |
|---|---:|---:|
| recall (identifiers protected) | **100.0%** (0 leaked) | 73.79% (897 leaked) |
| precision (benign untouched) | **100.0%** (0 over-redacted) | 85.8% (100 over-redacted) |

The benchmark did its job: the **first** run exposed real classifier gaps (recall
76.7%, 798 leaks — names, India IDs, US financial/device IDs). Closing them in the
*production* classifier (Note 34, all collision-checked against Indo-VAP) took
RePORTal to **0% leakage / 0% over-redaction**. Full analysis + the opposite-direction
Presidio weakness in `SCORE.md`.

### A note on column-name realism (why `RATIONNO`, not `RATION`)

Header-only classification depends on honest header semantics. Bare `RATION` is the
APL/BPL socioeconomic *category* (kept, like Indo-VAP's `IC_RATION`); a ration *card
number* field is named `RATIONNO`/`RATION_CARD_NO`. The benchmark uses the realistic
number-suffixed name. Likewise `IP` is intentionally left to value-based
disambiguation (intraperitoneal/inpatient vs. IP-address) rather than dropped by
header — see `SCORE.md`.
