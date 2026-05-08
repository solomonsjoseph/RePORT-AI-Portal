# Coverage Section: HIPAA §164.514(b)(2)(i)(A-R)

**Generated:** 2026-05-08
**Anchor:** 45 CFR §164.514 — De-identification of Protected Health Information.
**Inventory ground:** `docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`
(techniques §1-§20).

## Anchor scope

HIPAA's Privacy Rule defines two routes to de-identify PHI: **Expert
Determination** (§164.514(b)(1)) and **Safe Harbor** (§164.514(b)(2)).
Safe Harbor requires removal of all 18 listed identifiers (A-R) of the
individual *and* of their relatives, employers, or household members,
plus that the covered entity has no actual knowledge that the residual
information could be used to re-identify the subject (§164.514(b)(2)(ii)).
A **Limited Data Set** (§164.514(e)) is a softer regime: it strips a
narrower 16-item direct-identifier list (notably retaining dates,
city/town/state/ZIP, and unique non-direct codes) but requires a Data
Use Agreement signed by the recipient. §164.514(c) governs the
re-identification code: the code must not be derived from subject
information, and the mechanism must not be disclosed.

This pipeline ingests Indo-VAP / RePORT India clinical-research data,
which is U.S.-collaborator PHI under HIPAA. The project's default
posture is **Safe Harbor** with an opt-in **Limited Dataset** posture
(technique §8) gated on an IRB authority note. Safe Harbor is the
default the gates and audit log are calibrated against.

## Requirement ↔ technique map

| ID | HIPAA Identifier | Inventoried Technique(s) | Status | Notes |
|---|---|---|---|---|
| (A) | Names | §3 DROP (person-name regexes), §10 blocking regex (`PERSON_NAME_PREFIX`), §11 warn (`PERSON_NAME_GENERIC`), §20 allowlist (false-positive suppression on warn tier), §14 free-text drop | COVERED | Field-level drop for `*NAME*` columns; gate-level blocking for prefixed names ("Dr X"); narrative-name handling falls back to whole-value drop on free-text fields. |
| (B) | All geographic subdivisions smaller than a State (street, city, county, precinct, zip, geocode); first 3 ZIP digits allowed only if pop > 20,000 | §3 DROP (geography regex catalog at YAML 374) | COVERED | Whole-field drop for sub-state geography. |
| (C) | All elements of dates (except year) directly related to individual: birth, admission, discharge, death; ages > 89 collapsed to "90 or older" | §2 SANT date jitter, §4 CAP (age→90+), §8 birthdate posture, §3 DROP (death dates, place-of-death, system timestamps) | COVERED | Date jitter preserves intervals; birthdate dropped under Safe Harbor (or jittered with same offset under Limited Dataset). Age cap matches the Safe Harbor ≥90 rule. |
| (D) | Telephone numbers | §3 DROP (phone regex), §10 blocking regex (`INDIAN_PHONE`), §14 free-text drop | COVERED | |
| (E) | Fax numbers | §3 DROP (phone regex catch-all) | PARTIALLY_COVERED | No dedicated `FAX` field-name regex in `drop_fields`; relies on phone-pattern match and free-text drop. Indo-VAP CRFs do not collect fax, so risk is low — but a `(?i)\bfax\b` field-name guard would harden against future schema additions. |
| (F) | Electronic mail addresses | §3 DROP (email regex), §10 blocking regex (`EMAIL`), §14 free-text drop | COVERED | |
| (G) | Social Security numbers | §3 DROP (SSN regex), §10 blocking regex (`SSN`) | COVERED | US-form SSN; India equivalent (Aadhaar) covered by §10 (`AADHAAR`) and DPDPA scope. |
| (H) | Medical record numbers | §3 DROP (MRN regex), §10 blocking regex (`MRN`), §1 HMAC pseudonymization (`MED`, `CASE`, `IDCH` labels) | COVERED | Where MRN is needed for join, pseudonymized; otherwise dropped. |
| (I) | Health plan beneficiary numbers | §3 DROP (catch-alls covering plan/beneficiary IDs), §1 HMAC if labeled | PARTIALLY_COVERED | No explicit `(?i)beneficiary|health.?plan` field-name regex in YAML. India clinical-research collaborator data rarely carries US plan IDs, but a dedicated catch is missing. |
| (J) | Account numbers | §3 DROP (financial / payment narrative drops), §14 free-text drop | PARTIALLY_COVERED | No explicit `account` field-name regex. Catch-all coverage relies on narrative drops. |
| (K) | Certificate / license numbers | §3 DROP (Indian DL, Voter ID, Passport regexes), §10 blocking regex (`INDIAN_DL`, `INDIAN_VOTER_ID`, `INDIAN_PASSPORT`) | COVERED | Indian gov-ID forms covered; generic US license/certificate string forms not specifically targeted but caught by free-text drop. |
| (L) | Vehicle identifiers and serial numbers (incl. license plate) | (none) | UNCOVERED | No field-name or regex pattern targets vehicle/VIN/license-plate strings. Likely zero exposure in Indo-VAP CRFs (no vehicle data collected), so **operational risk is low** but **regulatory completeness is missing**. |
| (M) | Device identifiers and serial numbers | (none) | UNCOVERED | No targeted handling for medical-device serial numbers (e.g., implant IDs, pump serials). Possible exposure in lab/specimen data and AE narratives. |
| (N) | Web URLs | §3 DROP (URL regex), §10 blocking regex (`URL`), §14 free-text drop | COVERED | |
| (O) | Internet Protocol (IP) address numbers | §3 DROP (IP regex), §10 blocking regex (`IP`) | COVERED | |
| (P) | Biometric identifiers, including finger and voice prints | (none) | UNCOVERED | Identified in design spec as a special-attention item. No biometric-handling control. Indo-VAP forms do not currently collect biometrics, but TB programs frequently link to NIKSHAY (Indian government TB registry) which is moving to biometric (Aadhaar fingerprint) authentication. Future-coupling risk. |
| (Q) | Full face photographic images and any comparable images | §13 file exclusions (signed-consent images), §3 DROP (image / photo / scan field-name catch-alls) | PARTIALLY_COVERED | File-level exclusion list in YAML is documentation-only (per technique §13 note: "not consumed at runtime"). Image-typed fields rely on the JSONL extraction never producing image bytes. No content-aware image gate. |
| (R) | Any other unique identifying number, characteristic, or code | §1 HMAC (subject IDs, screening, family, facility, case, ID-chain, TB-IN, lab, specimen, study labels), §3 DROP (initials, signatures, staff IDs, batch metadata, religion, caste, occupation, income), §15 k-anonymity gate, §16 l-diversity gate, §17 small-cell mask, §6 SUPPRESS_SMALL_CELL, §14 free-text drop | COVERED | This catch-all bucket is where the bulk of inventoried techniques land; the k-anon / l-diversity gates address the "characteristic or code" combinatorial-uniqueness reading explicitly. |

**Rollup:**
- COVERED: 12 (A, B, C, D, F, G, H, K, N, O, R, plus structural Limited-Dataset support via §8)
- PARTIALLY_COVERED: 4 (E fax, I beneficiary, J account, Q image)
- UNCOVERED: 3 (L vehicle, M device, P biometric)

## Gaps and remediation proposals

### (E) Fax numbers — PARTIALLY_COVERED
**Proposal:** extend technique §3 (DROP). Add to `drop_fields` in
`scripts/security/phi_scrub.yaml`:
```yaml
- "(?i)\\bfax(_?(num|number|no))?\\b"
```
Rationale: closes a Safe Harbor letter without adding a new technique.

### (I) Health plan beneficiary numbers — PARTIALLY_COVERED
**Proposal:** extend technique §3 (DROP) with explicit field-name
regex (`(?i)\b(beneficiary|health.?plan|insurance.?id|policy.?no)\b`).
Low Indo-VAP exposure today; matters for any future US-cohort import.

### (J) Account numbers — PARTIALLY_COVERED
**Proposal:** extend technique §3 (DROP) with `(?i)\baccount(_?(num|no))?\b`
plus `(?i)\b(bank|ifsc|upi)\b` for Indian financial fields.

### (L) Vehicle identifiers / VIN / license plate — UNCOVERED
**Proposal:** extend technique §3 (DROP) with
`(?i)\b(vin|vehicle|license_?plate)\b`. Add a regex to technique §10
(`BLOCKING_PATTERNS` in `phi_patterns.py`) for the `[A-HJ-NPR-Z0-9]{17}` VIN
shape and the Indian license-plate shape `[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}`.
Risk surface is small but completeness is required.

### (M) Device identifiers / device serial numbers — UNCOVERED
**Proposal:** extend technique §3 (DROP) with
`(?i)\b(device|implant|pacemaker|pump|catheter|serial|udi)(_?id|_?no)?\b`.
Closes a known clinical-data vector (UDI = Unique Device Identifier).

### (P) Biometric identifiers — UNCOVERED
**Proposal:** new technique. Today: no fields, no patterns. Recommend:
- Field-name `drop_fields` regex covering `(?i)\b(biometric|fingerprint|face_?print|voice_?print|iris|retina|aadhaar_?bio|nikshay_?bio)\b`.
- A new **schema-time gate** that reads each form's `evidence_pack`
  schema and refuses to ingest any column whose dtype suggests binary
  biometric (image bytes, audio bytes). This is a *data-loader* control,
  not a row-level scrub control.
- Cross-reference: NIKSHAY integration roadmap should include explicit
  biometric exclusion per HIPAA §164.514(b)(2)(i)(P).

### (Q) Full face / comparable images — PARTIALLY_COVERED
**Proposal:** extend technique §13 (file exclusions) from documentation-only
to **enforced**. Either wire `file_exclusions` into `load_scrub_config()`
or replicate the image-shape catch in
`scripts/extraction/io/file_discovery.py::DEFAULT_JUNK_FILENAMES`. Add
content-type rejection for `image/*` MIME and binary blob columns at
the extraction stage.

## Citations

- **45 CFR §164.514(b)(2)(i)(A-R)** — Safe Harbor 18-identifier list:
  https://www.law.cornell.edu/cfr/text/45/164.514 (mirror of eCFR;
  eCFR.gov has CAPTCHA-gated programmatic access).
- **45 CFR §164.514(b)(2)(ii)** — actual-knowledge clause:
  same source, paragraph (b)(2)(ii).
- **45 CFR §164.514(c)** — re-identification code rules
  ("not derived from / capable of being translated", "does not disclose
  the mechanism for re-identification"): same source, paragraph (c).
- **45 CFR §164.514(e)** — Limited Data Set + Data Use Agreement:
  same source, paragraph (e). Direct identifiers excluded from a
  Limited Data Set: (i)-(xvi) at §164.514(e)(2).
- **HHS de-identification guidance** (cited for context only —
  CAPTCHA-blocked at fetch time, citation needed for direct quote):
  https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html
- **NIST SP 800-188** (referenced by inventoried techniques §1, §6,
  §11, §13, §14, §15, §16, §17, §18, §19, §20 as ancillary anchor):
  citation needed for fetched URL — see other coverage sections.
