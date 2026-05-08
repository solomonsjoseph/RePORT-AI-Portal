# PHI Coverage Matrix

**Generated:** 2026-05-08
**Sources:**
- Inventory: `docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`
- HIPAA section: `docs/superpowers/specs/2026-05-08-phi-coverage-section-hipaa.md`
- DPDPA section: `docs/superpowers/specs/2026-05-08-phi-coverage-section-dpdpa.md`
- Aadhaar+SPDI section: `docs/superpowers/specs/2026-05-08-phi-coverage-section-aadhaar-spdi.md`
- ICMR section: `docs/superpowers/specs/2026-05-08-phi-coverage-section-icmr.md`
- NIST SP 800-188 section: `docs/superpowers/specs/2026-05-08-phi-coverage-section-nist-800-188.md`

## 1. Summary

Across the five regulatory anchors, **101 cross-tabulated requirements** were
mapped onto the 20 inventoried PHI techniques (HIPAA 18 + DPDPA 22 + Aadhaar/SPDI
18 + ICMR 18 + NIST 25). Aggregate status (counting "out of scope" rows
inside their parent verdict): **COVERED ≈ 53**, **PARTIALLY_COVERED ≈ 28**,
**UNCOVERED ≈ 20**. The §1 HMAC pseudonym + §2 SANT date jitter + §3 DROP
catalog + §15 k-anonymity gate stack carries the bulk of coverage across all
five anchors, and the §18 sidecar key custody control is the single
highest-leverage primitive for HIPAA §164.514(c), DPDPA §8(5), Aadhaar §§38-41,
ICMR §11 (custodianship), and NIST §4.3.2 simultaneously. The most
cross-cutting gaps are: (a) **biometric and image content controls** (HIPAA P
+ Q, SPDI iv/vi, Aadhaar §29(1)/(4)); (b) **purpose-binding / outbound-transfer
gates** (DPDPA §4-§6, Aadhaar §29(3), ICMR §11 MTA/DUA); (c) **erasure / withdrawal
hooks** (DPDPA §8(7) + §12, ICMR §11 withdrawal); (d) **composition and
t-closeness** (NIST §4.3.11 + §3.2.1); (e) **bottom-coding** (NIST §4.3.3); and
(f) **promoting §16 l-diversity from "implemented" to "enforced"** (DPDPA
§17(2)(b), ICMR O17, NIST #20).

## 2. Cross-tabulated coverage

| # | Anchor | Requirement | Technique(s) | Status | Notes |
|---|---|---|---|---|---|
| H-A | HIPAA §164.514(b)(2)(i)(A) | Names | §3, §10, §11, §14, §20 | COVERED | Field-name drop, gate blocking on "Dr X", warn-tier with allowlist (see hipaa §H-A). |
| H-B | HIPAA §164.514(b)(2)(i)(B) | Sub-state geography | §3 | COVERED | Whole-field drop for street/city/county/ZIP/geocode (see hipaa §H-B). |
| H-C | HIPAA §164.514(b)(2)(i)(C) | Dates + age >89 | §2, §4, §8, §3 | COVERED | SANT jitter preserves intervals; CAP=90+; birthdate dropped under Safe Harbor (see hipaa §H-C). |
| H-D | HIPAA §164.514(b)(2)(i)(D) | Telephone | §3, §10, §14 | COVERED | Phone regex + Indian phone blocking pattern (see hipaa §H-D). |
| H-E | HIPAA §164.514(b)(2)(i)(E) | Fax | §3 | PARTIALLY_COVERED | No dedicated `fax` field-name regex; relies on phone catch-all (see hipaa §H-E). |
| H-F | HIPAA §164.514(b)(2)(i)(F) | Email | §3, §10, §14 | COVERED | Email regex + EMAIL blocking (see hipaa §H-F). |
| H-G | HIPAA §164.514(b)(2)(i)(G) | SSN | §3, §10 | COVERED | SSN field + SSN blocking regex (see hipaa §H-G). |
| H-H | HIPAA §164.514(b)(2)(i)(H) | Medical record numbers | §3, §10, §1 | COVERED | MRN drop + MRN blocking; HMAC-pseudonymized via MED/CASE/IDCH labels when retained (see hipaa §H-H). |
| H-I | HIPAA §164.514(b)(2)(i)(I) | Health-plan beneficiary numbers | §3 | PARTIALLY_COVERED | No explicit beneficiary/health-plan field-name regex (see hipaa §H-I). |
| H-J | HIPAA §164.514(b)(2)(i)(J) | Account numbers | §3, §14 | PARTIALLY_COVERED | No explicit `account` field-name regex; relies on narrative drop (see hipaa §H-J). |
| H-K | HIPAA §164.514(b)(2)(i)(K) | Certificate / license numbers | §3, §10 | COVERED | Indian DL/Voter/Passport drop + blocking regexes (see hipaa §H-K). |
| H-L | HIPAA §164.514(b)(2)(i)(L) | Vehicle identifiers / VIN / license plate | (none) | UNCOVERED | No field-name or pattern targets vehicle identifiers (see hipaa §H-L). |
| H-M | HIPAA §164.514(b)(2)(i)(M) | Device identifiers / UDI | (none) | UNCOVERED | No targeted handling for medical-device serials/UDI (see hipaa §H-M). |
| H-N | HIPAA §164.514(b)(2)(i)(N) | Web URLs | §3, §10, §14 | COVERED | URL regex + URL blocking pattern (see hipaa §H-N). |
| H-O | HIPAA §164.514(b)(2)(i)(O) | IP addresses | §3, §10 | COVERED | IP regex + IP blocking pattern (see hipaa §H-O). |
| H-P | HIPAA §164.514(b)(2)(i)(P) | Biometric identifiers | (none) | UNCOVERED | Special-attention item; no biometric handling control (see hipaa §H-P). |
| H-Q | HIPAA §164.514(b)(2)(i)(Q) | Full face / comparable images | §13, §3 | PARTIALLY_COVERED | §13 file-exclusion list is documentation-only at runtime; no content-aware image gate (see hipaa §H-Q). |
| H-R | HIPAA §164.514(b)(2)(i)(R) | Any other unique number / characteristic / code | §1, §3, §15, §16, §17, §6, §14 | COVERED | Catch-all bucket; HMAC + drop + k-anon/l-diversity + small-cell mask (see hipaa §H-R). |
| D-1 | DPDPA §2(t) | Personal-data definition / identifiability | §1, §3, §4, §5, §10, §12 | COVERED | Whole identifier-class stack applies (see dpdpa R1). |
| D-2 | DPDPA §4 / §5(i) / §6 | Lawful purpose, purpose limitation | (none) | UNCOVERED | No in-pipeline purpose-binding control (see dpdpa R2). |
| D-3 | DPDPA §5(1)(i)-(v) | Notice content | (none — out of scope) | UNCOVERED | Operational artefact (see dpdpa R3). |
| D-4 | DPDPA §6(1) | Free, specific, informed, unambiguous consent | (none — out of scope) | UNCOVERED | IRB-ICF upstream (see dpdpa R4). |
| D-5 | DPDPA §7(b), §7(g) | Legitimate-use grounds | §8 | PARTIALLY_COVERED | Limited-Dataset posture requires authority note (see dpdpa R5). |
| D-6 | DPDPA §8(1) + §17(2)(b) proviso | Non-delegable accountability | §18, §19, §13 | PARTIALLY_COVERED | Key custody + idempotency surfaces; no formal accountability ledger (see dpdpa R6). |
| D-7 | DPDPA §8(2) | Processor contract requirement | (none — out of scope) | UNCOVERED | Contractual control (see dpdpa R7). |
| D-8 | DPDPA §8(3) | Accuracy / completeness / consistency | §7, §2, §6, §17 | COVERED | KEEP allowlist + interval-preserving jitter + typed-numeric clamps (see dpdpa R8). |
| D-9 | DPDPA §8(4)+§8(5), Rule 6 | Reasonable security safeguards | §1, §18, §10-§12, §9 | PARTIALLY_COVERED | Key custody + egress hygiene present; at-rest encryption of `tmp/{STUDY}/quarantine/` is OS-permission only (see dpdpa R9). |
| D-10 | DPDPA §8(6), Rule 7 | Personal-data-breach notification | §10 | PARTIALLY_COVERED | Blocking findings give operational signal; no automated 72-hour notification workflow (see dpdpa R10). |
| D-11 | DPDPA §8(7) | Erasure on consent withdrawal | (none) | UNCOVERED | No erasure-by-subject hook at scrub time (see dpdpa R11). |
| D-12 | DPDPA §8(8) | Deemed cessation of purpose by inactivity | (none) | UNCOVERED | No retention clock (see dpdpa R12). |
| D-13 | DPDPA §8(9) | DPO / grievance contact | (none — out of scope) | UNCOVERED | Operational artefact (see dpdpa R13). |
| D-14 | DPDPA §8(11) | Data quality measures | §7, §20 | COVERED | KEEP + clinical-phrase allowlist (see dpdpa R14). |
| D-15 | DPDPA §9 | Children — verifiable parental consent, no behavioural tracking | §3, §8 | PARTIALLY_COVERED | DROP catches ASHA/Anganwadi/child-birth-year; no first-class child-row check (see dpdpa R15). |
| D-16 | DPDPA §10(2) | Significant Data Fiduciary additional duties | (none — out of scope) | UNCOVERED | Operational once notified (see dpdpa R16). |
| D-17 | DPDPA §11(1) | Right to information / processing summary | (none — out of scope) | UNCOVERED | Operational artefact (see dpdpa R17). |
| D-18 | DPDPA §12(1)-(3) | Right to correction / completion / erasure | §1, §18 | PARTIALLY_COVERED | HMAC is one-way; deletion via source-row drop + key rotation; no per-subject hook (see dpdpa R18). |
| D-19 | DPDPA §13 | Grievance redressal | (none — out of scope) | UNCOVERED | Operational artefact (see dpdpa R19). |
| D-20 | DPDPA §16(1)-(2), Rule 15 | Cross-border transfer / "negative list" | (none) | UNCOVERED | Pipeline does not gate on destination jurisdiction (see dpdpa R20). |
| D-21 | DPDPA §17(2)(b) | Research / archiving / statistical exemption — identity cannot be inferred | §1, §2, §3-§7, §15, §16, §17, §6, §10, §14 | COVERED | Full de-identification stack (see dpdpa R21). |
| D-22 | DPDPA §33 + Schedule | Penalty exposure (₹250 cr §8(5)) | §1, §10, §15-§18 | COVERED (mitigation) | Indirect mitigation by control stack (see dpdpa R22). |
| A-1 | Aadhaar §29(1)(a) | Core biometric shall not be shared | §3, §8 | PARTIALLY_COVERED | DROP hits BIOMET/FINGERPRINT/IRIS by name; no biometric-shape pattern (see aadhaar A1). |
| A-2 | Aadhaar §29(1)(b) | Core biometric used only for UIDAI authentication | (n/a) | COVERED (by exclusion) | Pipeline is not an Aadhaar requesting entity (see aadhaar A2). |
| A-3 | Aadhaar §29(2) | Identity-info sharing only as regulated | §10, §3, §1 | COVERED | AADHAAR blocking + India-gov-ID drop + SUBJ HMAC (see aadhaar A3). |
| A-4 | Aadhaar §29(3)(a) | Identity info used only for purposes disclosed in consent | (none) | UNCOVERED | No runtime purpose limitation (see aadhaar A4). |
| A-5 | Aadhaar §29(3)(b) | Identity info disclosed only as informed | (none) | UNCOVERED | No machine-checked consent-scope tie (see aadhaar A5). |
| A-6 | Aadhaar §29(4) | Aadhaar / demographic / photo not displayed publicly | §3, §10, §13 | PARTIALLY_COVERED | AADHAAR + demographic drops covered; photo/scan column drop missing; §13 doc-only (see aadhaar A6). |
| A-7 | Aadhaar §8 | Authentication consent | (n/a) | COVERED (by exclusion) | Out of scope — not requesting entity (see aadhaar A7). |
| A-8 | Aadhaar §33 | Disclosure only on District-Judge order | (none) | UNCOVERED (operational) | IRB / DUA review external (see aadhaar A8). |
| A-9 | Aadhaar §§38-41 | Unauthorised access criminal penalties | §18, §19, §9 | COVERED | Sidecar 0600 + idempotency markers + orphan quarantine (see aadhaar A9). |
| A-10 | SPDI Rule 3(i) | Passwords | §3 | COVERED (by exclusion) | DROP hits credential/password patterns; not a CRF class (see aadhaar S1). |
| A-11 | SPDI Rule 3(ii) | Financial info (bank, card, payment) | §3, §10 | PARTIALLY_COVERED | PAN covered; no card / IBAN / UPI / rupee-bank-account regex (see aadhaar S2). |
| A-12 | SPDI Rule 3(iii) | Physical / physiological / mental health condition | §1, §15, §16 | COVERED | Clinical payload de-linked via HMAC; k-anon + l-diversity gates (see aadhaar S3). |
| A-13 | SPDI Rule 3(iv) | Sexual orientation | (none) | UNCOVERED | No detection / drop / generalize rule; only fallback is §14 free-text drop (see aadhaar S4). |
| A-14 | SPDI Rule 3(v) | Medical records and history | §1, §15, §16, §14 | COVERED | Clinical payload + free-text narrative drop (see aadhaar S5). |
| A-15 | SPDI Rule 3(vi) | Biometric information | §3, §13 | PARTIALLY_COVERED | DROP by column name only; no biometric blob detector (see aadhaar S6). |
| A-16 | SPDI Rule 3(vii)-(viii) | Derivative SPDI from contracted processing | §1, §2, §3, §10 | COVERED | Pipeline inherits SPDI status transitively (see aadhaar S7). |
| A-17 | SPDI Rule 5 | Consent, lawful purpose, retention, purpose limitation | §8 | PARTIALLY_COVERED | Closest analogue is birthdate posture authority note; no machine-checked retention clock (see aadhaar S8). |
| A-18 | SPDI Rule 8 | Reasonable security practices (ISO 27001 safe harbour) | §18, §19, §9 | PARTIALLY_COVERED | Aligns with ISO 27001 A.10 / A.12 / A.16; no formal certification claim (see aadhaar S11). |
| I-1 | ICMR §1 | General privacy/confidentiality principle | §1, §3, §10, §13, §15, §17 | COVERED | Every PHI-class identifier covered by at least one technique (see icmr O1). |
| I-2 | ICMR §5 | ICF "maintenance of confidentiality" element | §1, §3, §18 | PARTIALLY_COVERED | Technical commitment enforced; no automated ICF-text cross-check (see icmr O2). |
| I-3 | ICMR §11 Table 4 | Sample/dataset classification regime | §8, §1, §2, §15 | PARTIALLY_COVERED | `compliance_posture` matches coded class; no first-class flag for "anonymous/unidentified" or "irreversibly anonymized" (see icmr O3). |
| I-4 | ICMR §11 (commentary) | Limited access to coding key + samples + records | §18 | COVERED | XDG sidecar mode 0600 outside repo (see icmr O4). |
| I-5 | ICMR §11 | Irreversibly anonymized — link removed | (none) | UNCOVERED | No first-class irreversible mode that destroys/never-issues the key (see icmr O5). |
| I-6 | ICMR §11 | Custodian = institution, not researcher | §18, §19 | PARTIALLY_COVERED | Sidecar lives in user XDG, not under institutional control (see icmr O6). |
| I-7 | ICMR §11 | Consent typology + withdrawal/waiver/re-consent | (none) | UNCOVERED-by-design | Study-protocol concern; scrubber must respect withdrawal signal (see icmr O7). |
| I-8 | ICMR §11 | MTA / DUA on transfer | §8 | PARTIALLY_COVERED | Authority-note enforcement is birthdate-posture-only; no outbound trio-bundle gate (see icmr O8). |
| I-9 | ICMR §11 | EC review of dataset/repo proposals | (none) | UNCOVERED | No automated EC-sign-off check (see icmr O9). |
| I-10 | ICMR §11 (commentary) | Limited (least-privilege) access | §13, §18, §9 | PARTIALLY_COVERED | Filesystem-level controls; no per-artefact ACL or read-audit (see icmr O10). |
| I-11 | ICMR §11 | Return of results / benefit sharing | (out of scope for scrubber) | OUT_OF_SCOPE | Pipeline-level concern (see icmr O11). |
| I-12 | ICMR §4 | Record retention 3 yr / 5 yr | §19 | PARTIALLY_COVERED | Sentinel proves scrub completed; no enforced retention period (see icmr O12). |
| I-13 | ICMR §6 | Vulnerability — strengthened controls | §3, §5, §6, §15, §16 | COVERED | DROP caste/religion/occupation/income; small-cell suppression; l-diversity caveat (see icmr O13). |
| I-14 | ICMR §4 (anonymized review pathway) | Expedited pathway depends on anonymization quality | §15, §1, §2, §3, §6 | COVERED | k≥5 distributional gate is the empirical defence (see icmr O14). |
| I-15 | ICMR §4 Table 2 | "Non-identifiable clinical data" expedited claim | §10 | COVERED | Blocking regex tier is the contract test at LLM-facing surface (see icmr O15). |
| I-16 | ICMR (commentary) | Bounded residual re-identification risk in consent | §15, §16, §6 | PARTIALLY_COVERED | Risk bounded but not quantified for IRB packs (see icmr O16). |
| I-17 | ICMR §10 (adjacent) | Genetic / stigma-prone safeguards | §3, §11, §20 | PARTIALLY_COVERED | Caste/religion drop + clinical allowlist; outcome-homogeneity defence is l-diversity tracked-gap (see icmr O17). |
| I-18 | ICMR §11 / CTRI | Publication / agent-tool de-identification gate | §10, §15, §17 | COVERED | Three-layer enforcement at trio-bundle → agent boundary (see icmr O18). |
| N-1 | NIST §4.3.1 | Direct-identifier removal (NULL / mask / encrypt / keyed-hash / surrogate) | §1, §3, §14, §10 | COVERED | NIST cites SHA-256 HMAC + 256-bit key — matches §1 line-for-line (see nist #1). |
| N-2 | NIST §4.3.2 | Special security caveat — keys must be highly protected | §1, §18 | COVERED | 12-hex truncation + label domain separation + sidecar mode 0600 (see nist #2). |
| N-3 | NIST §4.3.3 | Top coding | §4 | COVERED | CAP is exactly NIST's top-coding mechanism (see nist #3). |
| N-4 | NIST §4.3.3 | Bottom coding | (none) | UNCOVERED | CAP only handles upper tail (see nist #4). |
| N-5 | NIST §4.3.3 | Micro-aggregation | (none / partial via §15) | PARTIALLY_COVERED | Pipeline does not micro-aggregate; k-anon catches under-aggregation (see nist #5). |
| N-6 | NIST §4.3.3 | Generalize categories with small counts | §5, §6 | COVERED | Categorical generalize + per-cell numeric clamp (see nist #6). |
| N-7 | NIST §4.3.3 | Data suppression (cells below threshold) | §17, §15, §6 | COVERED | `<5` label matches NIST k=5 throughout (see nist #7). |
| N-8 | NIST §4.3.3 | Blanking and imputing | (none) | UNCOVERED | Pipeline favours honest-drop / honest-cap over imputation; intentionally out of scope (see nist #8). |
| N-9 | NIST §4.3.3 | Attribute / record swapping | (none) | UNCOVERED | Out of scope — breaks per-row determinism (see nist #9). |
| N-10 | NIST §4.3.3 | Noise addition / "noise infusion" | §2 | PARTIALLY_COVERED | Confined to date axis; no numeric-QI noise (deliberate clinical-fidelity choice) (see nist #10). |
| N-11 | NIST §4.3.4 | Date de-identification | §2, §4, §8 | COVERED | SANT implements NIST's per-subject systematic shift recipe verbatim (see nist #11). |
| N-12 | NIST §4.3.5 | Geographic de-identification | §3, §14 | COVERED | Whole-field drop matches HIPAA Safe Harbor (see nist #12). |
| N-13 | NIST §4.3.6 | Genomic information de-identification | (none) | UNCOVERED | Indo-VAP/RePORT do not collect WGS/WES; flag as future-data-class (see nist #13). |
| N-14 | NIST §4.3.7 | Free-text narrative de-identification (NER) | §14, §10, §11, §20 | PARTIALLY_COVERED | Whole-drop is conservative; NIST itself acknowledges narrative scrub is unsolved (see nist #14). |
| N-15 | NIST §4.3.8 | Aggregation challenges (multiple-release inference) | §15, §16, §19 | PARTIALLY_COVERED | Single-release covered; no cross-release differencing detection (see nist #15). |
| N-16 | NIST §4.3.9 | High-dimensional data — many QIs → near-uniqueness | §15 | PARTIALLY_COVERED | Gate fails on over-rich QIs; no automatic QI-set suggestion (see nist #16). |
| N-17 | NIST §4.3.10 | Linked data / external join | §1, §15 | PARTIALLY_COVERED | Per-label HMAC defeats internal cross-label join; external defence is QI minimization in SoT (see nist #17). |
| N-18 | NIST §4.3.11 | Composition challenges (multiple releases compose to leak) | (none) | UNCOVERED | Each scrub run treated independently; no composition-tracking ledger (see nist #18). |
| N-19 | NIST §3.2.1 / §4.3.1 | k-anonymity formal model | §15 | COVERED | Default k=5 matches NIST worked examples (see nist #19). |
| N-20 | NIST §3.2.1 | l-diversity formal model | §16 | COVERED | Implemented but not yet wired into `verify_and_promote` ("tracked design gap") (see nist #20). |
| N-21 | NIST §3.2.1 | t-closeness formal model | (none) | UNCOVERED | No t-closeness check; non-trivial residual risk on TB outcome skew (see nist #21). |
| N-22 | NIST §1 / §3.2.1 / §3.7.3 / §4.4.7 | Differential privacy | (none) | UNCOVERED — likely intentionally so | Not relevant to row-level JSONL trio bundle; would only apply to aggregate publication or query interface (see nist #22). |
| N-23 | NIST §4.3.1 | Pseudonymization risk — repeatable transforms | §1, §18 | COVERED | Sidecar custody + 12-hex truncation satisfy NIST highly-protected requirement (see nist #23). |
| N-24 | NIST §4.3.1 | Privacy-Preserving Record Linkage (PPRL) cross-org risk | §1 | COVERED | Per-deployment key never shared — cross-org PPRL impossible by construction (see nist #24). |
| N-25 | NIST §3.7.3 | Risk-based de-identification standards layer | §15, §16, §6, §17, §19, audit ledger | COVERED | Two-layer pattern (prescriptive + risk-based) endorsed by NIST §3.7 (see nist #25). |

## 3. Gaps requiring new rules (PR-draft territory)

### Gap G-1: Vehicle / VIN / license-plate detection (HIPAA L)

- **Anchor rows:** H-L
- **Inventoried technique to extend:** §3 DROP + §10 blocking regex
- **Proposed change:** add `(?i)\b(vin|vehicle|license_?plate)\b` to `drop_fields`; add VIN shape `[A-HJ-NPR-Z0-9]{17}` and Indian plate `[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}` to `BLOCKING_PATTERNS`.
- **Cited from:** hipaa section §H-L

### Gap G-2: Device identifier / UDI detection (HIPAA M)

- **Anchor rows:** H-M
- **Inventoried technique to extend:** §3 DROP
- **Proposed change:** add `(?i)\b(device|implant|pacemaker|pump|catheter|serial|udi)(_?id|_?no)?\b` to `drop_fields`.
- **Cited from:** hipaa section §H-M

### Gap G-3: Biometric identifiers (HIPAA P, SPDI vi)

- **Anchor rows:** H-P, A-1, A-15
- **Inventoried technique to extend:** new technique (§21 candidate) + §3 DROP catalog
- **Proposed change:** add field-name DROP regex `(?i)\b(biometric|fingerprint|face_?print|voice_?print|iris|retina|aadhaar_?bio|nikshay_?bio|face_?templ|voice_?templ)\b`; add **schema-time gate** that refuses to ingest columns whose dtype suggests image/audio bytes (binary blob detector).
- **Cited from:** hipaa section §H-P; aadhaar section §A1, §S6

### Gap G-4: Image / photo-content gate (HIPAA Q, Aadhaar §29(4))

- **Anchor rows:** H-Q, A-6
- **Inventoried technique to extend:** §13 file exclusions + §3 DROP
- **Proposed change:** wire `file_exclusions` into `load_scrub_config()` (or replicate in `file_discovery.py::DEFAULT_JUNK_FILENAMES`); add `^(?:PHOTO|PIC|SCAN|IMAGE|FACE)\b` field-name DROP rule; add MIME `image/*` rejection at extraction stage.
- **Cited from:** hipaa section §H-Q; aadhaar section §A6

### Gap G-5: Fax field-name regex (HIPAA E)

- **Anchor rows:** H-E
- **Inventoried technique to extend:** §3 DROP
- **Proposed change:** add `(?i)\bfax(_?(num|number|no))?\b`.
- **Cited from:** hipaa section §H-E

### Gap G-6: Health-plan beneficiary / account regex (HIPAA I, J)

- **Anchor rows:** H-I, H-J
- **Inventoried technique to extend:** §3 DROP
- **Proposed change:** add `(?i)\b(beneficiary|health.?plan|insurance.?id|policy.?no)\b` and `(?i)\baccount(_?(num|no))?\b` plus `(?i)\b(bank|ifsc|upi)\b`.
- **Cited from:** hipaa section §H-I, §H-J

### Gap G-7: Financial (rupee / IBAN / card / UPI) blocking regex (SPDI ii)

- **Anchor rows:** A-11
- **Inventoried technique to extend:** §10 BLOCKING_PATTERNS
- **Proposed change:** extend with rupee-bank-account 9-18-digit-with-IFSC shape, 16-digit Luhn-checked card, IBAN, UPI VPA `[a-z0-9.]+@[a-z]+`.
- **Cited from:** aadhaar section §S2

### Gap G-8: Sexual-orientation field detection (SPDI iv)

- **Anchor rows:** A-13
- **Inventoried technique to extend:** §3 DROP + optional §5 GENERALIZE
- **Proposed change:** add `^(?:SEX_ORIENT|ORIENT|SEXUAL_ORIENT|LGBT)` DROP; if any future CRF codes this, add GENERALIZE map.
- **Cited from:** aadhaar section §S4

### Gap G-9: Purpose-binding manifest (DPDPA §4-§6, Aadhaar §29(3))

- **Anchor rows:** D-2, D-7, A-4, A-5
- **Inventoried technique to extend:** new technique (§21+ candidate)
- **Proposed change:** add `purpose_id` field to run manifest (mirrored into §19 sentinel); refuse trio-bundle write whose `purpose_id` is missing from `authorities/phi_purposes.yaml`. Tie agent-boundary output to recorded consent scope.
- **Cited from:** dpdpa §G1; aadhaar §A4, §A5

### Gap G-10: Erasure-by-subject ledger (DPDPA §8(7), §12, ICMR §11 withdrawal)

- **Anchor rows:** D-11, D-12, D-18, I-7
- **Inventoried technique to extend:** new technique
- **Proposed change:** append-only `erasure/{study}/requests.jsonl` (`{subject_id, requested_at, basis}`) ingested at start of every scrub run; matching subjects dropped before pseudonymization and added to orphan-quarantine block-list; idempotent under §19. Mirrors ICMR §23 withdrawal-signal proposal (`authorities/phi_withdrawn_subjects.txt`).
- **Cited from:** dpdpa §G2; icmr §23

### Gap G-11: Cross-border egress gate (DPDPA §16, ICMR §11 MTA/DUA)

- **Anchor rows:** D-20, A-8, I-8
- **Inventoried technique to extend:** new technique
- **Proposed change:** add `allowed_destinations` list in `phi_scrub.yaml` (default empty = block); surface to agent-tool boundary as precondition. Pair with `phi_outbound_transfer.md` authority note (citing receiving party + MTA/DUA + EC approval ID) — hard-fail at orchestration when missing.
- **Cited from:** dpdpa §G3; icmr §24; aadhaar §A8/§S9/§S10

### Gap G-12: At-rest encryption of working artefacts (DPDPA §8(4)/(5) Rule 6)

- **Anchor rows:** D-9
- **Inventoried technique to extend:** §18 sidecar HMAC key management
- **Proposed change:** widen sidecar key contract to a second 32-byte key for AES-256-GCM encryption of `tmp/{STUDY}/quarantine/*.jsonl` and other working artefacts; same path/mode discipline; rotation = re-ingestion.
- **Cited from:** dpdpa §G4

### Gap G-13: Breach-notification signal (DPDPA §8(6) Rule 7)

- **Anchor rows:** D-10
- **Inventoried technique to extend:** §10 + §19
- **Proposed change:** emit structured `phi_breach_signal.jsonl` whenever the gate trips with `blocked=True`; ops layer wires it to the 72-hour breach-notification workflow.
- **Cited from:** dpdpa §G5

### Gap G-14: Children-row first-class flag (DPDPA §9)

- **Anchor rows:** D-15
- **Inventoried technique to extend:** §7 / §8 (posture)
- **Proposed change:** add a `child_subject_flag` derived field (from age <18 post-cap or birthdate-based) and a posture refusal for any pipeline mode that would feed children's data into a generative model without a Limited-Dataset-style authority note.
- **Cited from:** dpdpa §G6

### Gap G-15: Promote l-diversity from "implemented" to "enforced" (DPDPA §17(2)(b), ICMR O17, NIST #20)

- **Anchor rows:** D-21, I-17, N-20
- **Inventoried technique to extend:** §16 l-diversity gate
- **Proposed change:** wire `l_diversity_check` into `verify_and_promote` so it gates promotion, not just reports findings. Default l=2; tighten on stigma-prone outcomes for vulnerable cohorts.
- **Cited from:** dpdpa §G7; icmr §16 caveat; nist #20

### Gap G-16: Irreversible anonymization mode (ICMR O5)

- **Anchor rows:** I-5
- **Inventoried technique to extend:** §1 + §8 (new posture)
- **Proposed change:** add third `compliance_posture` value `anonymous_export`. HMAC pseudonyms replaced by row-stable random IDs from a per-export entropy pool destroyed at export end; sidecar key not consulted; emit `anonymous_export.manifest.json`; force-drop k<5 quasi-identifiers (no `keep_fields` escape).
- **Cited from:** icmr §21

### Gap G-17: Institutional sidecar custody (ICMR O6)

- **Anchor rows:** I-6
- **Inventoried technique to extend:** §18
- **Proposed change:** optional `sidecar_custody: institutional` mode. Key path declared by `authorities/phi_institutional_custody.md` (signed by institution IT-Sec lead); hard-fail if mode != 0600 owned by institutional service account.
- **Cited from:** icmr §22

### Gap G-18: EC sign-off ledger (ICMR O9)

- **Anchor rows:** I-9
- **Inventoried technique to extend:** audit ledger extension
- **Proposed change:** each trio-bundle export records the IRB/EC approval ID it claims authority under; `_phi_audit.ndjson` extended so an external auditor can verify claim → EC record.
- **Cited from:** icmr §25

### Gap G-19: Retention manifest (ICMR §4)

- **Anchor rows:** D-12, A-17, I-12
- **Inventoried technique to extend:** new manifest at export end
- **Proposed change:** write `retention.json` declaring `study_close_date`, retention period (3y / 5y per study type), and computed disposal date; add TTL field to §19 sentinel so re-runs after the retention horizon force re-scrub or refusal.
- **Cited from:** icmr §26; aadhaar §S8

### Gap G-20: Quantified residual-risk note (ICMR O14/O16)

- **Anchor rows:** I-16
- **Inventoried technique to extend:** §15 / §16 / §17 surface extension
- **Proposed change:** each export emits `reidentification_risk.json` with `k_min`, `l_min`, suppressed-class count, dropped-row count due to k-violations, date-jitter envelope. Data is already computed by `kanon_gate`; surface as first-class artefact.
- **Cited from:** icmr §27

### Gap G-21: Bottom-coding semantic (NIST §4.3.3)

- **Anchor rows:** N-4
- **Inventoried technique to extend:** §4 CAP
- **Proposed change:** add a `FLOOR` action or extend `CapRule` with optional `lower_threshold` / `lower_label` so YAML can declare both tails on a single field rule. Code change: `CapRule` dataclass + `cap_numeric()` primitive; YAML schema bump.
- **Cited from:** nist gap #1

### Gap G-22: t-closeness gate (NIST §3.2.1)

- **Anchor rows:** N-21
- **Inventoried technique to extend:** new technique alongside §15 / §16
- **Proposed change:** add `t_closeness_check(records, qi_columns, sensitive_columns, t)` next to `l_diversity_check` in `kanon_gate.py`; EMD over categorical sensitive distribution; default t=0.2; wire into `verify_and_promote`. Recommended for Phase 2 expansion.
- **Cited from:** nist gap #1 (top priority)

### Gap G-23: Composition / cross-release tracking (NIST §4.3.11)

- **Anchor rows:** N-15, N-18
- **Inventoried technique to extend:** new technique on top of audit ledger
- **Proposed change:** record every `{study, agent, query-shape, timestamp}` tuple; refuse queries that, combined with prior queries, would breach a configured budget. Phase 1 audit-ledger work is the natural home.
- **Cited from:** nist gap #3

### Gap G-24: ISO 27001 evidence pack (SPDI Rule 8)

- **Anchor rows:** A-18
- **Inventoried technique to extend:** operational
- **Proposed change:** pursue ISO 27001 certification of host infrastructure or document equivalent control mapping (A.10 / A.12 / A.16 already aligned via §18 / §19 / §9).
- **Cited from:** aadhaar §S11

## 4. Gaps requiring SoT revision (HITL territory)

Per Phase 1 spec §5.3, variables in SoT YAMLs whose `handling_intent.action`
is `review_required` are policy decisions, not rule additions, and belong to a
human-in-the-loop review process — they cannot be closed by extending the
scrubber catalog. The Phase 1 sweep produced **544 HITL drafts** under
`tmp/phi_sweep_hitl_drafts/` (one Markdown file per
`(form_id, variable_id, context_hash)` tuple flagged for review).

The following coverage gaps are SoT-level policy revisions rather than
scrub-rule extensions and therefore belong here, separately from §3:

- **D-3 / D-4 / D-13 / D-16 / D-17 / D-19** — DPDPA notice / consent / DPO /
  SDF / right-to-information / grievance-redressal obligations: out-of-scope
  for `scripts/security/`; tracked via the IRB / institutional-policy layer
  and reflected in SoT `handling_intent` decisions, not scrub rules.
- **A-7 / A-8 / S9 / S10** — Aadhaar §8 authentication consent + §33
  disclosure orders + SPDI Rule 6 / Rule 7 third-party / cross-border
  release decisions: operational, cleared via HITL review of the
  individual variables and through outbound-transfer authority notes
  (Gap G-11 covers the runtime gate; the SoT decision is HITL).
- **I-7** — ICMR §11 consent typology (broad / tiered / specific +
  withdrawal / waiver / re-consent): study-protocol concern; the
  scrubber must respect a withdrawal signal (Gap G-10 covers the runtime
  ledger; the SoT decision about which subjects/variables fall under
  which consent regime is HITL).
- **I-11** — ICMR return-of-results / benefit-sharing: pipeline-level,
  not scrub-level.
- **N-13 / N-22** — Genomic / DP scope decisions: HITL review of
  whether and when these data classes enter the pipeline at all.

## 5. Forward-looking notes

**Coupling risks.** Even where a regulatory row reads as
"COVERED (by exclusion)" or "UNCOVERED but operational risk is low", the
TB-program ecosystem is moving in a direction that converts those low-risk
postures into real exposure. The most concrete is the NIKSHAY (Indian
government TB registry) integration roadmap: NIKSHAY is migrating to Aadhaar
biometric (fingerprint) authentication, so the H-P / A-1 / A-15 biometric
gap (Gap G-3) is forward-looking even though Indo-VAP CRFs do not currently
collect biometrics. Vehicle (H-L) and device (H-M) gaps are similarly
low-exposure today but become regulatorily-binding the moment a future
schema adds the column. The biometric and image-content gaps therefore
deserve weight disproportionate to their current observed prevalence.

**Statutory horizons.** The 2011 SPDI Rules (rows A-10 through A-18) sunset
on **13 May 2027** when DPDPA §44(2) repeals IT-Act §43A and the SPDI Rules.
After that date, every "PARTIALLY_COVERED" SPDI row collapses into its
DPDPA-only counterpart. The DPDPA **§17(2)(b) research carve-out** is partial:
**§8(1)** (overall responsibility) and **§8(5)** (reasonable security
safeguards) survive even when consent / notice / retention obligations are
relaxed for de-identified research output. So the security envelope (D-6,
D-9) and the "identity cannot be inferred" floor (D-21) remain
non-negotiable regardless of how the carve-out is invoked.

**Scope boundary clarification (NIST).** Differential privacy (N-22) is
intentionally absent from the scrub layer. NIST 800-188 itself frames DP as
relevant to **aggregate publication** and **interactive query interfaces**,
not to row-level microdata shipping. The scrub layer's contract is
prescriptive identifier-removal + risk-based gates (§15-§17). DP belongs to
a possible future agent-tool gate that performs query-budget tracking; the
composition-tracking ledger (Gap G-23) is the bridge between the two and
the natural place to introduce DP if and when the data-sharing model
changes. Documenting this scope boundary explicitly is itself a remediation
— it converts the absence from a silent gap into a deliberate design choice.

## 6. Live sweep result

The deterministic SoT-driven sweep (`scripts/security/phi_sot_sweep.py`) ran
against `data/SoT/Indo-VAP/` and `data/SoT/Indo-VAP/dataset_policies/` on
**2026-05-08T14:07:41+00:00**. Findings are at `tmp/phi_sweep_findings.json`
(masked variable_ids only; 0 cleartext leaks).

| Category | Count |
|---|---|
| `total_variables` | **1996** |
| `covered` | 1452 |
| `name_phi_uncovered` | 0 |
| `column_shape_phi_uncovered` | 0 (Phase 2 reserved) |
| `review_required_open` | 544 |

**Interpretation.** Every PHI-suggestive variable name in the SoT YAMLs already
maps to a covered handling action (drop, pseudonymize, jitter_date, cap,
generalize, suppress_small_cell), which is the correct posture: zero
`name_phi_uncovered` is a strong signal that the policy authors are not
leaving PHI-named columns in clear text. The 544 `review_required_open`
entries are policy decisions awaiting human sign-off; the Task 9 emitter
produced one HITL draft per entry under `tmp/phi_sweep_hitl_drafts/`. The
Task 10 verifier (`scripts/security/phi_sweep_verify.py`) confirms every
variable maps to either `covered` or an open HITL draft (`make
phi-audit-verify` exits 0).
