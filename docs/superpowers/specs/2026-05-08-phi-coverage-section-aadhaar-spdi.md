# PHI Coverage Section — Aadhaar Act §29 + SPDI Rule 3

**Generated:** 2026-05-08
**Anchor:** Aadhaar Act 2016 §29 (Restriction on Sharing Information) + IT
(Reasonable Security Practices and Procedures and Sensitive Personal Data
or Information) Rules 2011 — Rule 3 (definition of SPDI), with adjacent
Rules 5-8 (consent, disclosure, transfer, security) and Aadhaar §§ 2/8/33
read in for context.
**Companion document:** `docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`

## 1. Anchor scope

This section maps two distinct Indian regulatory anchors to the inventoried
PHI-handling techniques (§§1-20). The two anchors complement one another:

- **Aadhaar Act 2016 §29** restricts what may be done with the Aadhaar
  number, the underlying biometrics, and any associated identity
  information. It is an **identifier-class** rule: it tells you what you
  can and cannot do with one specific identifier (the 12-digit Aadhaar
  number) and the records pinned to it. The relevant defined terms come
  from §2: "core biometric information" (fingerprints, iris scans),
  "biometric information" (core biometric + photograph), "demographic
  information" (name, DOB, address, etc., excluding race/religion/caste/
  income/medical history), "identity information" (Aadhaar number +
  biometric + demographic), and "authentication record".
- **SPDI Rules 2011 Rule 3** defines a closed list of categories that
  count as **Sensitive Personal Data or Information (SPDI)** when
  collected by a body corporate. The eight Rule 3 categories are:
  (i) passwords; (ii) financial information (bank/card/payment
  instrument); (iii) physical, physiological and mental health
  condition; (iv) sexual orientation; (v) medical records and history;
  (vi) biometric information; (vii) any detail relating to (i)-(vi)
  provided to a body corporate for service; (viii) information received
  under (i)-(vii) by a body corporate for processing under lawful
  contract. Rule 3 expressly excludes information already in the public
  domain or furnished under the RTI Act.

Aadhaar §29 + companion sections impose the following operative
restrictions on a clinical-research processor:

- **§29(1)** — Core biometric information (fingerprints, iris)
  collected/created under the Act SHALL NOT be shared with anyone for
  any reason, and SHALL NOT be used for any purpose other than Aadhaar
  number generation and authentication.
- **§29(2)** — Identity information *other than* core biometric may be
  shared only as the Act and regulations specify.
- **§29(3)** — Identity information held by a requesting entity must
  not be used or disclosed for any purpose beyond what was disclosed
  in writing to the individual at the time of authentication.
- **§29(4)** — Aadhaar number, demographic info, or photograph SHALL
  NOT be published, displayed, or posted publicly, except as
  regulations permit.
- **§8** — Authentication requires the individual's consent.
- **§33** — Disclosure permitted only on a District Judge's (or higher)
  order with notice to the individual; §33(2) (national-security
  disclosure) was struck down by *Puttaswamy II* (2018).
- **Penalties** — §§38-41 criminalise unauthorised access to or
  disclosure of identity information / authentication records.

SPDI Rule 3 + companion rules impose:

- **Rule 3** — Defines the SPDI categories above; everything in the list
  is governed by Rules 4-8.
- **Rule 4** — Body corporates must publish a privacy policy.
- **Rule 5** — Consent (writing/fax/email) is required before
  collection; collection must be for a lawful, necessary purpose;
  data must not be retained longer than required and must be used
  only for the stated purpose.
- **Rule 6** — Disclosure to third parties needs prior consent except
  where required by law.
- **Rule 7** — Trans-border transfer requires same-or-better
  protection at the receiving end and prior consent.
- **Rule 8** — Reasonable security practices required;
  IS/ISO/IEC 27001 deemed compliant.

A clinical-research pipeline processing Indian-subject CRF data
inevitably touches both anchors: subjects' Aadhaar (or PAN/voter ID)
may appear on a face sheet (§29 territory); their medical-history,
biometric, and physiology fields are SPDI Rule 3 categories (iii),
(v), (vi).

## 2. Requirement ↔ technique map

| # | Anchor + Requirement | Inventoried technique | Status |
|---|----------------------|------------------------|--------|
| A1 | Aadhaar §29(1)(a) — core biometric info shall not be shared | §3 DROP applied to biometric-flagged columns; §10 BLOCKING regex catalog has no biometric-shape pattern, but biometric data is non-textual so cannot leak via free-text scan. Birthdate posture (§8) and DROP catalog hit fingerprint-template columns by name when `BIOMET|FINGERPRINT|IRIS` patterns are present. | PARTIALLY_COVERED |
| A2 | Aadhaar §29(1)(b) — core biometric used only for UIDAI authentication | Out-of-scope for the agent boundary: the pipeline is not an Aadhaar requesting entity. Project does not perform authentication. | COVERED (by exclusion) |
| A3 | Aadhaar §29(2) — identity-info sharing only as regulated | §10 blocking regex `AADHAAR` pattern blocks Aadhaar-shape leaks at the agent boundary; §3 DROP rules in YAML lines 397-403 drop the India-government-ID columns; §1 HMAC pseudonymization rewrites SUBJID into `SUBJ_<hex>`. | COVERED |
| A4 | Aadhaar §29(3)(a) — identity info used only for purposes disclosed in consent | No technique enforces purpose limitation at the runtime boundary. Posture file (§8 limited-dataset authority note) is the closest analogue but documents IRB approval, not Aadhaar-§29(3) consent. | UNCOVERED |
| A5 | Aadhaar §29(3)(b) — identity info disclosed only as informed | Same as A4. Project posture file is human-readable; no machine-checked predicate ties output disclosures to a recorded consent scope. | UNCOVERED |
| A6 | Aadhaar §29(4) — Aadhaar number / demographic / photo not displayed or posted publicly | §3 DROP catalog drops Aadhaar columns by name; §10 blocking regex `AADHAAR` (12-digit shape) blocks display in agent output; §3 DROP also drops `*_NAME`, `*_ADDRESS`, `*_PHONE` (demographic) by name. Photographs are documentation-only via §13 file exclusions; runtime drop of a `PHOTO` column would pattern-miss without an explicit rule. | PARTIALLY_COVERED |
| A7 | Aadhaar §8 — authentication consent | Out of scope: project is not a requesting entity. | COVERED (by exclusion) |
| A8 | Aadhaar §33 — disclosure only on District-Judge+ order with hearing | No technique. Disclosure is governed by IRB / DUA review processes that live outside the scrub layer. | UNCOVERED (operational, not technical) |
| A9 | Aadhaar §§38-41 — unauthorised access criminal penalties | §18 sidecar HMAC key management (mode 0600 + key-not-overwriting bootstrap); idempotency markers (§19); orphan quarantine (§9) prevent un-scrubbed leakage. | COVERED |
| S1 | SPDI Rule 3(i) — passwords | §3 DROP catalog hits `password|credential` patterns under file-exclusion / staff-credential rules; no runtime regex for password tokens in clinical free-text. Outside CRF scope: passwords are not a clinical-data-class encountered in study CRFs. | COVERED (by exclusion) |
| S2 | SPDI Rule 3(ii) — financial info (bank, card, payment) | §3 DROP for known PAN columns; §10 blocking regex `PAN` (`[A-Z]{5}[0-9]{4}[A-Z]`) catches PAN tokens in free-text; no card-number / IBAN regex; no rupee / bank-account regex. Indo-VAP CRFs contain very limited financial data (income column is dropped by §3). | PARTIALLY_COVERED |
| S3 | SPDI Rule 3(iii) — physical, physiological, mental health condition | These are the *clinical payload* of the study — the analytic value the system is designed to preserve. §1 HMAC pseudonymization de-links the condition from the subject; §15 k-anon and §16 l-diversity gates ensure the condition is never returned with a re-identifying quasi-id tuple. | COVERED |
| S4 | SPDI Rule 3(iv) — sexual orientation | No dedicated technique. CRFs do not currently collect sexual orientation, but no detection / drop / generalize rule exists if such a column appeared. Closest fallback: §14 free-text whole-value drop catches narrative `*_REMARK` / `*_SPECIFY` columns where this might be entered. | UNCOVERED |
| S5 | SPDI Rule 3(v) — medical records and history | Same as S3 (this is the clinical payload). §1 + §15 + §16 cover. §14 free-text drop catches narrative medical-history fields not on the structured allowlist. | COVERED |
| S6 | SPDI Rule 3(vi) — biometric information | §3 DROP applies if column is named with a biometric-suggesting label; otherwise no detection. No fingerprint-template-byte-blob detection. §13 file exclusions documents but does not enforce skipping signed-consent images that could carry biometric scans. | PARTIALLY_COVERED |
| S7 | SPDI Rule 3(vii)-(viii) — derivative SPDI from contracted processing | Pipeline as a downstream processor inherits SPDI status of inputs. §1 HMAC, §2 SANT jitter, §3 DROP, §10 blocking regex apply transitively. | COVERED |
| S8 | SPDI Rule 5 — consent, lawful purpose, retention limit, purpose limitation | §8 birthdate posture file is the closest existing artefact (it documents IRB + DUA). No machine-checked retention-limit clock. No machine-checked purpose-tag on agent output. | PARTIALLY_COVERED |
| S9 | SPDI Rule 6 — third-party disclosure with prior consent | Out of scope for the scrubber: third-party sharing is a release-time decision, not a scrub-time decision. | UNCOVERED (operational) |
| S10 | SPDI Rule 7 — trans-border transfer with same-or-better protection | Out of scope for the scrubber. The trio bundle written to `tmp/{study}/` is local; cross-border transfer happens outside this layer. | UNCOVERED (operational) |
| S11 | SPDI Rule 8 — reasonable security practices (ISO 27001 safe harbour) | §18 sidecar HMAC key management aligns with ISO 27001 A.10 (cryptographic controls); idempotency markers + sentinels (§19) align with A.12 operations security; orphan quarantine (§9) aligns with A.16 incident management. No formal ISO 27001 certification claim. | PARTIALLY_COVERED |

## 3. Gaps and proposed extensions

The following gaps map UNCOVERED / PARTIALLY_COVERED rows onto concrete
inventory extensions:

- **A4 / A5 — purpose limitation under §29(3).** Add a runtime
  `purpose_tag` predicate to §1 HMAC pseudonymization metadata: each
  agent boundary records the declared analytic purpose, and the gate
  refuses output if the tag does not match a tag listed in the IRB
  consent record. New technique candidate: **purpose-binding gate**.
- **A6 — §29(4) photograph display.** Extend §3 DROP catalog with a
  `^(?:PHOTO|PIC|SCAN|IMAGE|FACE)\\b` rule. Extend §13 file exclusions
  (currently doc-only) into a runtime enforcement path so signed-
  consent / photo-ID image files cannot enter the trio bundle.
- **A8 / S9 / S10 — §33 + Rule 6 + Rule 7 disclosure & transfer
  controls.** Operational, not technical. Document an
  `authorities/phi_disclosure.md` companion to the limited-dataset
  authority note: any cross-border or third-party share must reference
  this file and an IRB-approved DUA.
- **S2 — financial-info regex coverage.** Extend §10 blocking regex
  catalog with rupee-bank-account shape (typically 9-18 digits with
  IFSC adjacency), 16-digit credit-card shape (Luhn-checked),
  IBAN, UPI VPA (`[a-z0-9.]+@[a-z]+`). Today only PAN is covered.
- **S4 — sexual-orientation field detection.** Add a §3 DROP rule
  matching `^(?:SEX_ORIENT|ORIENT|SEXUAL_ORIENT|LGBT)` and a §5
  GENERALIZE map if any future CRF collects this as a coded field.
  Today the only safety net is §14 free-text drop on narrative
  comments.
- **S6 — biometric-data column detection.** Add a §3 DROP rule for
  `BIOMET|FINGERPRINT|IRIS|RETINA|FACE_TEMPL|VOICE_TEMPL` patterns
  and a binary-blob detector (column with high entropy + non-UTF-8
  bytes) in the gate.
- **S8 — Rule 5 consent / retention.** Add a `phi_consent_record.md`
  authority note, parallel to the limited-dataset note, that the
  loader checks at config-load time. Add a TTL field to the §19
  sentinel marker so re-runs after the retention horizon force a
  full re-scrub or refusal.
- **S11 — Rule 8 ISO 27001 deemed-compliant safe harbour.**
  Operational: pursue ISO 27001 certification of the host
  infrastructure or document the equivalent control mapping.

## 4. Citations

- **Aadhaar Act 2016, §§ 2 / 8 / 29 / 33 / 38-41** — UIDAI source PDF:
  https://uidai.gov.in/images/the_aadhaar_act_2016.pdf and the
  amended version at
  https://uidai.gov.in/images/Aadhaar_Act_2016_as_amended.pdf
- **Aadhaar Act §29 plain-language extract** — IndiaCode:
  https://www.indiacode.nic.in/show-data?actid=AC_CEN_37_85_00001_201618_1517807328460&sectionId=3607&sectionno=29&orderno=32
  and IndianKanoon: https://indiankanoon.org/doc/30018477/
- **Aadhaar (Sharing of Information) Regulation 2016** —
  Telangana High Court hosted copy:
  https://thc.nic.in/Central%20Governmental%20Regulations/Aadhaar%20(Sharing%20of%20Information)%20Regulation,%202016.pdf
- **SPDI Rules 2011 (full text, MeitY notification)** — WIPO mirror:
  https://www.wipo.int/edocs/lexdocs/laws/en/in/in098en.pdf
  and DataGuidance: https://www.dataguidance.com/sites/default/files/in098en.pdf
- **SPDI Rule 3 plain-language** — IndianKanoon:
  https://indiankanoon.org/doc/101774797/
- **SPDI Rules CIS commentary (Rule-by-Rule analysis)** —
  https://cis-india.org/internet-governance/blog/comments-on-the-it-reasonable-security-practices-and-procedures-and-sensitive-personal-data-or-information-rules-2011
- **Puttaswamy II (Aadhaar) judgment summary** —
  https://www.scobserver.in/reports/constitutionality-of-aadhaar-justice-k-s-puttaswamy-union-of-india-judgment-in-plain-english/
