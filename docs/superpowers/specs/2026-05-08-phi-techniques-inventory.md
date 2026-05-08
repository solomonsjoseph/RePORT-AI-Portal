# PHI Techniques Inventory

**Generated:** 2026-05-08
**Scope:** every PHI-handling technique referenced in `scripts/security/`.
**Purpose:** ground for the regulatory coverage matrix in
`docs/superpowers/specs/2026-05-08-phi-coverage-matrix.md`.

## Index

1. [HMAC-SHA256 pseudonymization](#1-hmac-sha256-pseudonymization)
2. [Per-subject deterministic date jitter (SANT)](#2-per-subject-deterministic-date-jitter-sant)
3. [DROP](#3-drop)
4. [CAP](#4-cap)
5. [GENERALIZE](#5-generalize)
6. [SUPPRESS_SMALL_CELL](#6-suppress_small_cell)
7. [KEEP allowlist](#7-keep-allowlist)
8. [Birthdate handling](#8-birthdate-handling)
9. [Subject-ID-keyed orphan quarantine](#9-subject-id-keyed-orphan-quarantine)
10. [Blocking regex catalog](#10-blocking-regex-catalog)
11. [Warn regex tier](#11-warn-regex-tier)
12. [Subject-ID regex tier](#12-subject-id-regex-tier)
13. [File exclusions](#13-file-exclusions)
14. [Free-text whole-value drop](#14-free-text-whole-value-drop)
15. [k-anonymity gate](#15-k-anonymity-gate)
16. [l-diversity gate](#16-l-diversity-gate)
17. [Small-cell mask helper](#17-small-cell-mask-helper)
18. [Sidecar HMAC key management](#18-sidecar-hmac-key-management)
19. [Idempotency marker + sentinel](#19-idempotency-marker--sentinel)
20. [Clinical-phrase allowlist (false-positive suppression)](#20-clinical-phrase-allowlist-false-positive-suppression)

---

## 1. HMAC-SHA256 pseudonymization

**Description:** Replaces an identifier value with `<LABEL>_<hmac12hex>`, where
the HMAC input is `f"{label}:{raw_id}"` and the key is the 32-byte sidecar
secret. The label is propagated as both the visible output prefix
(`SUBJ_…`, `FAM_…`, `LAB_…`, etc.) and the cryptographic domain
separator (RFC 5869 §3.2 / HKDF info-param rationale): the same raw value
under different labels yields different pseudonyms, preventing
cross-category correlation. Same `(label, raw_id, key)` triple always
yields the same output, preserving in-category longitudinal joins. The
12-hex slice (48 bits) is calibrated for cohorts under 100 000 subjects.
The YAML `id_fields` list is structured `{pattern, label}`; first-match
wins, and unlabelled string entries are rejected (v2 catalog).

**Implementing code:**
- `scripts/security/phi_scrub.py:672-700` — `pseudo_id()` core primitive (HMAC-SHA256, domain separation, output format).
- `scripts/security/phi_scrub.py:229-250` — `IdRule` (compiled `{pattern, label}` rule).
- `scripts/security/phi_scrub.py:485-507` — `id_fields` YAML loader (rejects plain strings, requires `pattern` + `label`).
- `scripts/security/phi_scrub.py:401-416` — `id_label_for()` / `field_is_id()` first-match dispatch.
- `scripts/security/phi_scrub.py:1013-1020` — Action-priority slot 8 in `_scrub_row` (calls `pseudo_id`).
- `scripts/security/phi_scrub.yaml:189-242` — full `id_fields` catalog (`SUBJ`, `SCRN`, `FAM`, `FAC`, `CRL`, `CASE`, `IDCH`, `TBIN`, `MED`, `LAB`, `STDY`, `SPEC` labels).

**Regulatory anchor:** No explicit citation in `pseudo_id()` itself.
The surrounding YAML and module docstring cite **HIPAA §164.514(b)(2)**
(re-identification code rules: not derived from / related to the subject's
information; not otherwise capable of being translated), **DPDPA 2023
§2(t)** (personal data identifier scope), **Aadhaar Act §29** (identifier
sharing prohibitions), **SPDI Rule 3** (sensitive personal data),
**ICMR 2017 §11**, and **NIST SP 800-188** (de-identification framework).
Domain-separation property anchored to **RFC 5869 §3.2** in source.

---

## 2. Per-subject deterministic date jitter (SANT)

**Description:** Each subject is assigned a deterministic offset in
`[-max_jitter_days, +max_jitter_days]` derived from
`int.from_bytes(HMAC-SHA256(key, subject_id)[:4], 'big') % (2N+1) - N`.
Every date field on rows belonging to that subject shifts by the same
offset, preserving SANT-style interval structure (visit gaps,
person-time, incidence windows survive intact) while obscuring exact
calendar dates. The offset envelope (default 30 days) is calibrated to
preserve weekly/monthly clinical interval semantics. Format detection is
preserved on output (ISO / M-D-Y / D-M-Y / hour-minute-second / AM-PM).
Subject IDs are resolved by exact match on `subject_id_fields` first,
then suffix match (`_SUBJID` style) — exact wins.

**Implementing code:**
- `scripts/security/phi_scrub.py:703-713` — `date_offset_days()` deterministic offset derivation.
- `scripts/security/phi_scrub.py:746-769` — `shift_date()` parse/shift/re-emit preserving format.
- `scripts/security/phi_scrub.py:716-743` — `_format_date()` format-preservation helper.
- `scripts/security/phi_scrub.py:866-897` — `_resolve_subject_id()` exact-then-suffix candidate resolution.
- `scripts/security/phi_scrub.py:390-399` — `field_is_date()` (excludes birthdate to keep posture branching honest).
- `scripts/security/phi_scrub.py:999-1011` — Action-priority slot 7 in `_scrub_row` (jitter; also handles birthdate when `posture=limited_dataset`).
- `scripts/security/phi_scrub.yaml:51-64` — `subject_id_fields` (`SUBJID`, `FID`) + `max_jitter_days: 30`.
- `scripts/security/phi_scrub.yaml:179-186` — `date_fields` regex catalog (Indo-VAP CRF-tuned + generic suffix catch-alls).

**Regulatory anchor:** YAML preamble (line 35) cites **HIPAA §164.514(b)(2)**
and **ICMR §11**. Module-level docstring (`phi_scrub.py:32-36`) tags this
as the **SANT method** for "epidemiological survival / incidence /
person-time analyses." Docstring at `phi_scrub.py:9-16` ties date
treatment to **HIPAA §164.514(b)(2)(i)(C)** (dates more specific than year).

---

## 3. DROP

**Description:** Field is removed from every row entirely. Used for
HIPAA Safe Harbor identifiers that have no analytic value at the
agent boundary (names, initials, signatures, staff IDs, national IDs,
contact info, sub-state geography, free-text narratives, system
timestamps, batch/scan artefacts). Pattern matching is `re.search`
case-insensitive on the field name. The YAML catalogues 93 rules across
14 categories (per the YAML preamble at line 254). Action priority slot
3, after `keep_fields` (allowlist) and `birthdate_field` posture branch.

**Implementing code:**
- `scripts/security/phi_scrub.py:370-371` — `field_is_drop()`.
- `scripts/security/phi_scrub.py:954-958` — Action-priority slot 3 in `_scrub_row` (deletes the field).
- `scripts/security/phi_scrub.py:475-477` — `drop_fields` YAML loader (`_compile_list`).
- `scripts/security/phi_scrub.yaml:251-432` — full `drop_fields` regex catalog (subject-id duplicates, site IDs, form IDs, batch metadata, initials, signatures, staff identifiers, lab tech IDs, lab accession, SSN/MRN, person names, DOB/death dates, place-of-death, system timestamps, CRF workflow, religion/caste, education, occupation, income, labour hours, facility names, geography, phone/email/IP, India government IDs, HIPAA catch-alls, asha/anm/anganwadi worker IDs, child birth years, narrative comment/remark/note).

**Regulatory anchor:** YAML line 254 cites **HIPAA §164.514(b)(2)(i)(A-R)**
(Safe Harbor 18-identifier list). YAML lines 315, 327, 374, 397, 403
cite **HIPAA §164.514(b)(2)(i)(B)** (geography), **§164.514(b)(2)(i)(C)**
(dates), **DPDPA §2(t)** (identifier categories), **Aadhaar Act**
(India government IDs), and **HIPAA #4/#6/#7/#8/#9/#10/#11/#14/#15**
(catch-all element categories). Free-text narrative drop (lines 422-432)
documents the rationale for whole-field drop over whole-value hashing.

---

## 4. CAP

**Description:** Numeric field values strictly greater than a threshold
are replaced with a label string (default `90+`). Values at or below the
threshold pass unchanged. Each `cap_fields` entry binds a field-name
regex to an optional `threshold` and `label`; missing overrides
inherit the top-level `age_cap.threshold` (89) and `age_cap.label`
(`"90+"`). Streaming-safe (per-cell, not per-distribution). Type
preservation: int→str / float→str on cap (label is a string).

**Implementing code:**
- `scripts/security/phi_scrub.py:210-227` — `CapRule` (compiled `{pattern, threshold, label}`).
- `scripts/security/phi_scrub.py:796-813` — `cap_numeric()` core primitive.
- `scripts/security/phi_scrub.py:373-378` — `cap_rule_for()` first-match dispatch.
- `scripts/security/phi_scrub.py:516-547` — `age_cap` + `cap_fields` YAML loader (defaults + per-rule overrides).
- `scripts/security/phi_scrub.py:960-972` — Action-priority slot 4 in `_scrub_row`.
- `scripts/security/phi_scrub.yaml:73-75` — `age_cap: {threshold: 89, label: "90+"}`.
- `scripts/security/phi_scrub.yaml:439-443` — `cap_fields` patterns (`IS_AGE`, `IC_AGE`, generic `^(?:[A-Z]{1,4}[-_])?AGE$`, `^Age$`).

**Regulatory anchor:** YAML lines 72 and 438, plus
`phi_scrub.py:23-25` and `phi_scrub.py:803-806`, all cite
**HIPAA §164.514(b)(2)(i)(C)** ("ages > 89 aggregated to 90+").

---

## 5. GENERALIZE

**Description:** Value-level categorical mapping. Each `generalize_fields`
entry pairs a field-name regex with the name of a value-to-value mapping
under `generalization_maps`. At scrub time, the value is stripped,
lower-cased, looked up in the named mapping, and replaced. Unknown
values pass through unchanged (audit logs the false-count so operators
can spot coverage gaps). Maps are normalized at load time to lowercase
keys for case-insensitive lookup with no per-row allocation. Two
mappings are shipped: `marital` (Married / Single / Other) and
`facility` (Government / Private / Other).

**Implementing code:**
- `scripts/security/phi_scrub.py:253-276` — `GeneralizeRule` (compiled `{pattern, mapping_name, mapping}`).
- `scripts/security/phi_scrub.py:816-835` — `generalize_value()` core primitive.
- `scripts/security/phi_scrub.py:380-385` — `generalize_rule_for()` first-match dispatch.
- `scripts/security/phi_scrub.py:549-586` — `generalization_maps` + `generalize_fields` YAML loader (pattern + mapping reference; rejects unknown mapping names).
- `scripts/security/phi_scrub.py:974-984` — Action-priority slot 5 in `_scrub_row`.
- `scripts/security/phi_scrub.yaml:447-454` — `generalize_fields` (`MARISTAT|MARIT|MARITAL` → `marital`; `HOSP_TYPE|FACIL_TYPE|...` → `facility`; Indo-VAP coded names → `facility`).
- `scripts/security/phi_scrub.yaml:457-494` — `generalization_maps` (`marital` and `facility` value tables).

**Regulatory anchor:** No explicit citation in source for the
generalize technique itself. The YAML preamble (line 12) describes it as
"value-level mapping to broad category"; module docstring at
`phi_scrub.py:26-28` ties it to coarsening of marital status and
facility type. Implicit anchor is **HIPAA Expert Determination
§164.514(b)(1)** generalization principle and **NIST SP 800-188**
generalization control category.

---

## 6. SUPPRESS_SMALL_CELL

**Description:** Numeric values strictly greater than a threshold
(default 5) are clamped *to the threshold itself* (not to a label),
preserving numeric type stability for downstream analyses. Counts at
or below the threshold pass unchanged — small cells here are an
analytic concern, not a privacy concern (the privacy concern is the
*upper tail* of unique household / contact counts that can re-identify
a subject). Type preservation: int stays int, float stays float.

**Implementing code:**
- `scripts/security/phi_scrub.py:838-860` — `suppress_small_cell()` core primitive.
- `scripts/security/phi_scrub.py:387-388` — `field_is_suppress_small_cell()`.
- `scripts/security/phi_scrub.py:478` — `suppress_small_cell_fields` loader (`_compile_list`).
- `scripts/security/phi_scrub.py:514` — `small_cell_threshold` loader.
- `scripts/security/phi_scrub.py:986-997` — Action-priority slot 6 in `_scrub_row`.
- `scripts/security/phi_scrub.yaml:77-79` — `small_cell_threshold: 5`.
- `scripts/security/phi_scrub.yaml:502-505` — `suppress_small_cell_fields` (`IS_CONTACTS`, `IS_CONTACTS_TOTAL|6YRS`, generic `contacts_total|count|6yrs`).

**Regulatory anchor:** YAML line 77 and `phi_scrub.py:29-31`,
`phi_scrub.py:846-850` all cite **ICMR 2017 §11.7** ("k-anonymity proxy
for household-contact counts"). The technique is described as a
streaming-time approximation of k-anonymity at the cell level (the
distributional gate lives in `kanon_gate.py`).

---

## 7. KEEP allowlist

**Description:** Explicit allowlist that wins over every other rule
(slot 1 in priority order). When a field name matches any
`keep_fields` regex, it short-circuits the entire scrubber and passes
through unchanged with no audit event recorded. Used to protect
clinical lab / medication / time-of-day / categorical indicators
from being swept up by broader drop or date patterns (e.g., `_TIM` is
typically a time-of-day, not a date; `URINE` is a clinical specimen,
not part of a URL; `IS_SEX` is essential clinical data, not free-text
contact). Comprehensive Indo-VAP false-positive guards.

**Implementing code:**
- `scripts/security/phi_scrub.py:362-368` — `field_is_keep()`.
- `scripts/security/phi_scrub.py:476` — `keep_fields` loader (`_compile_list`).
- `scripts/security/phi_scrub.py:943-945` — Action-priority slot 1 in `_scrub_row` (short-circuit `continue`).
- `scripts/security/phi_scrub.yaml:88-174` — `keep_fields` regex catalog (age-adjacent indicators, death flags, marital binary, time-of-day false-positives, urine guards, lab specimen IDs, visit labels, sex, ration card category, TB card / dose-grid / PHC / clinical lab+medication+AE+EE+TC+TB block patterns).

**Regulatory anchor:** No explicit citation in source. The keep-list
preamble (YAML lines 82-87) describes it as "allowlist that wins over
every other rule" and a defense against over-redaction. No regulatory
mandate — purely an analytic-utility / clinical-fidelity preservation
mechanism. (Loosely related to **HIPAA Expert Determination** balance
between de-identification and analytic utility.)

---

## 8. Birthdate handling

**Description:** Posture-dependent rule, evaluated at slot 2 (right
after `keep_fields` allowlist). Two modes:
- `safe_harbor` (default): birthdate field is **dropped entirely**, age
  fidelity is lost, no IRB / DUA paperwork required.
- `limited_dataset`: birthdate is treated as a normal date — jittered with
  the same per-subject SANT offset as other dates so age-at-event is
  preserved. Loader **refuses to run** in limited-dataset mode unless
  `authorities/phi_limited_dataset.md` exists, documenting IRB approval
  + Data Use Agreement.

**Implementing code:**
- `scripts/security/phi_scrub.py:418-419` — `field_is_birthdate()`.
- `scripts/security/phi_scrub.py:509-510` — `birthdate_field` regex compile.
- `scripts/security/phi_scrub.py:446-454` — Limited-dataset authority-note enforcement at config-load time.
- `scripts/security/phi_scrub.py:167-174` — `_POSTURE_*` constants + `_LIMITED_DATASET_AUTHORITY` path.
- `scripts/security/phi_scrub.py:947-952` — Action-priority slot 2 in `_scrub_row` (Safe Harbor drop).
- `scripts/security/phi_scrub.py:1001-1003` — Slot 7 fall-through for Limited Dataset jitter.
- `scripts/security/phi_scrub.yaml:42-49` — `compliance_posture` documentation + default.
- `scripts/security/phi_scrub.yaml:245-249` — `birthdate_field` regex (`IS_BIRTHDAT|IC_BIRTHDAT|HHC_BRTHDAT|HC_BRTHDAT|birth|dob|bday|brthdat`).

**Regulatory anchor:** Module docstring `phi_scrub.py:9-16` cites
**HIPAA §164.514(b)(2)(i)(C)** (birthdates as Safe Harbor identifier)
+ **DPDPA**. YAML lines 43-48 contrast Safe Harbor versus Limited
Dataset postures, both anchored to HIPAA §164.514. Limited-dataset
posture aligns with **HIPAA §164.514(e)** Limited Data Set + Data Use
Agreement requirements.

---

## 9. Subject-ID-keyed orphan quarantine

**Description:** Rows with no resolvable `subject_id_fields` value
(none of the candidates exact-match or suffix-match anything non-empty
in the row) cannot be SANT-jittered (the offset is keyed off the
subject ID), so they are quarantined to
`tmp/{STUDY}/quarantine/{file}.jsonl` instead of being scrubbed in
place. If quarantine count for any single file exceeds
`orphan_quarantine_threshold` (default 10), the pipeline hard-fails —
that count typically signals a misconfigured `subject_id_fields` list.
Orphan rows preserve raw values; quarantine is a write-zone path.

**Implementing code:**
- `scripts/security/phi_scrub.py:866-897` — `_resolve_subject_id()` exact-then-suffix resolution; returns `""` if no candidate populated.
- `scripts/security/phi_scrub.py:925-928` — `_scrub_row()` returns `(None, {})` when subject ID is empty (caller quarantines).
- `scripts/security/phi_scrub.py:203-204` — `PHIQuarantineOverflowError` exception class.
- `scripts/security/phi_scrub.py:1232-1249` — orphan handling in `run_scrub()`: write to quarantine dir, raise `PHIQuarantineOverflowError` on threshold breach.
- `scripts/security/phi_scrub.py:513` — `orphan_quarantine_threshold` loader.
- `scripts/security/phi_scrub.yaml:57-59` — `subject_id_fields` (`SUBJID`, `FID`).
- `scripts/security/phi_scrub.yaml:66-70` — `orphan_quarantine_threshold: 10`.

**Regulatory anchor:** No explicit citation. The mechanism is
operational-safety (preventing un-jittered raw rows from leaking through
to the trio bundle) rather than regulatory. Implicit anchor:
**HIPAA §164.514(b)(2)(ii)** ("the covered entity does not have actual
knowledge that the information could be used … to identify an
individual") — un-keyed dates remain re-identifiable, hence quarantine.

---

## 10. Blocking regex catalog

**Description:** High-confidence PHI patterns that, on a hit anywhere in
agent-tool return text, set `blocked=True` on the gate result. These
fire on shapes that are not plausible legitimate clinical free-text:
Indian government IDs (Aadhaar, PAN, voter ID, DL, passport), contact
info (Indian phone, email, URL, Indian PIN), US identifier shapes
(SSN, MRN, IP), ISO dates, and prefixed person names
(`Mr/Mrs/Ms/Dr/Prof <Name>`). Each entry is a `(label, compiled_regex)`
tuple; labels propagate to gate findings for IRB-facing audit.

**Implementing code:**
- `scripts/security/phi_patterns.py:33-69` — `BLOCKING_PATTERNS` list (12 patterns: AADHAAR, PAN, INDIAN_VOTER_ID, INDIAN_DL, INDIAN_PASSPORT, INDIAN_PHONE, EMAIL, URL, INDIAN_PIN, SSN, MRN, IP, DATE_ISO, PERSON_NAME_PREFIX).
- `scripts/security/phi_gate.py:98-102` — `_scan_regex()` blocking-tier loop.
- `scripts/security/phi_gate.py:128-162` — `phi_gate_check()` orchestrates per-text scans, flags `blocked` if any blocking label fires.

**Regulatory anchor:** Module docstring `phi_patterns.py:18-19` cites
**HIPAA §164.514(b)(2)(i)(A-P)**, **DPDPA §2(t)**, **Aadhaar Act §29**,
**SPDI Rule 3**, **ICMR 2017 §11.4**. ISO date pattern (line 57) cites
**HIPAA §164.514(b)(2)(i)(C)**.

---

## 11. Warn regex tier

**Description:** Lower-confidence PHI heuristics that fire frequently on
legitimate clinical text and are therefore **logged-only, not blocking**.
Three patterns: `NUMERIC_ID_SHORT` (6-7 digit bare numbers),
`DATE_MDY` (slash-separated dates), `PERSON_NAME_GENERIC` (two-token
capitalized strings). The clinical-phrase allowlist (`phi_allowlist`) is
consulted on this tier only; blocking tier ignores the allowlist. The
generic person-name pattern gets per-match tuning: warn fires only when
at least one match both fails the clinical-phrase allowlist *and* looks
like a real name under the seeded first/last-name lexicon.

**Implementing code:**
- `scripts/security/phi_patterns.py:73-81` — `WARN_PATTERNS` list.
- `scripts/security/phi_gate.py:103-115` — `_scan_regex()` warn-tier loop with per-match `PERSON_NAME_GENERIC` tuning.
- `scripts/security/phi_gate.py:118-125` — `_is_clinical_allowlist_hit()` whole-text suppression.
- `scripts/security/phi_gate.py:149-153` — Warn findings recorded only when not whole-text clinical.

**Regulatory anchor:** No explicit citation. Module docstring
`phi_patterns.py:11-13` describes the rationale ("Over-aggressive in
mixed clinical text; surfaced for audit, not enforcement"). Implicit
alignment with **NIST SP 800-188** false-positive / utility-balance
guidance.

---

## 12. Subject-ID regex tier

**Description:** Indo-VAP / RePORT India-specific subject-ID literal
shapes (`SUBJ-N`, `SC<4+digits>`, `FID<digits>`). Used by the log
wrapper to perform per-subject HMAC redaction in log lines (the gate
itself does not consume this list — it is exposed for the redaction
filter / log hygiene layer).

**Implementing code:**
- `scripts/security/phi_patterns.py:84-90` — `SUBJECT_ID_PATTERNS` list.
- `scripts/security/phi_patterns.py:26-29` — `__all__` export.

**Regulatory anchor:** No explicit citation in this list itself.
The shapes are calibrated to the **RePORT India Common Protocol** and
Indo-VAP CRF set; redaction in logs flows from **HIPAA §164.514** /
**DPDPA §2(t)** as for the other tiers. Module docstring at
`phi_patterns.py:18-19` covers the consolidated regulatory anchors.

---

## 13. File exclusions

**Description:** Documentation-only catalog of file-name patterns that
the pipeline *should* skip during ingestion (Excel lock files, macOS
junk, `.bak` / `.tmp`, staff/personnel/credential files, signed
consent images). The YAML preamble explicitly notes that this section
is **not consumed at runtime** by `load_scrub_config` — actual junk
filtering is enforced in `scripts/extraction/io/file_discovery.py`'s
`DEFAULT_JUNK_FILENAMES`. This section is a documentation hook for
operator reference and for future enforcement parity.

**Implementing code:**
- `scripts/security/phi_scrub.yaml:507-525` — `file_exclusions` regex catalog (Excel lock, `.DS_Store`, `Thumbs.db`, hidden files, `__MACOSX`, `.bak`, `.tmp`, staff/personnel/employee, contact/phone lists, password/credential, signed consent images).
- `scripts/security/phi_scrub.py` — *no consumer* (deliberate; YAML preamble line 510 documents this explicitly).

**Regulatory anchor:** No explicit citation. Implicit anchor:
**HIPAA §164.514** + **NIST SP 800-188** input-hygiene guidance —
non-clinical artefact files (lock files, OS junk, password files) must
not enter the pipeline because they are not curated for PHI safety.

---

## 14. Free-text whole-value drop

**Description:** Narrative / verbatim free-text columns
(`*COMMENT`, `*REMARK`, `*NOTE`, `WITHDRAWEXPLAIN`, `*SPECIFY`,
`SP` suffix variants) are dropped wholesale rather than hashed or
NER-scrubbed. The YAML preamble at lines 422-426 explains the
rationale: hashing a free-text value containing an Aadhaar / phone
number does not remove the embedded PHI tokens, it only obscures the
containing string. Whole-value drop is the honest-safe default until
a narrative NER sweep lands. Implemented as part of the `drop_fields`
catalog; semantically a separate technique.

**Implementing code:**
- `scripts/security/phi_scrub.yaml:422-432` — narrative / free-text / specify / comment regexes (`WITHDRAWEXPLAIN|WITHDRAW_EXPLAIN`, `^FA_FUCOMPADCSP$`, `^CC_NOPREGTESTSP$`, `^CC_CNCTNDSP$`, `^ST_COMMENT$|^sc_COMMENT$`, `(?:COMMENT|REMARK|NOTE)$`).
- `scripts/security/phi_scrub.py:41-44` — module docstring rationale.
- `scripts/security/phi_scrub.py:954-958` — same `_scrub_row` slot 3 (`drop`) executes the deletion.

**Regulatory anchor:** YAML preamble (line 256-259) cites the converted
**FREE_TEXT_PHI** rule legacy (whole-hash → whole-drop). Module
docstring `phi_scrub.py:41-44` calls out narrative drop as conservative
defense; the inline rationale at YAML 422-426 acknowledges the
gap relative to a **narrative NER sweep**. No specific HIPAA / DPDPA
citation, but covered by the broader **HIPAA §164.514(b)(2)(i)(R)**
("any other unique identifying number, characteristic, or code").

---

## 15. k-anonymity gate

**Description:** Distributional-level enforcement at the trio-bundle →
agent boundary. Given a list of records and a tuple of
quasi-identifier columns, counts rows per equivalence class and
returns `blocked=True` whenever any class size falls below `k`
(default 5). Reports `smallest_class_size` and a sorted tuple of
`violating_keys` (string-form quasi-identifier tuples, safe to log).
Defends against the row-level re-identification scenario where the
scrub pseudonymizes / jitters / generalizes correctly, but a query
returns a single matched row with all sensitive attributes visible.

**Implementing code:**
- `scripts/security/kanon_gate.py:48-62` — `KAnonResult` dataclass.
- `scripts/security/kanon_gate.py:69-109` — `kanon_check()` core function.
- `scripts/security/kanon_gate.py:65-66` — `_key_to_str()` helper.
- `scripts/security/kanon_gate.py:44-45` — `_DEFAULT_K = 5` / `_SUPPRESSED_LABEL = "<5"`.

**Regulatory anchor:** Module docstring `kanon_gate.py:19-22` cites
**Pillar 1.7** (the project's IRB-grade benchmark anchor),
**ICMR 2017 §11.7**, and **NIST SP 800-188 §5**.

---

## 16. l-diversity gate

**Description:** Companion to the k-anon gate. Verifies that every
equivalence class (defined by quasi-identifier tuple) contains at least
`l_threshold` distinct values for *each* sensitive attribute. l = 2 is
the smallest meaningful threshold; higher resists homogeneity attacks
more strongly. Use *after* `kanon_check` — k-anon ensures classes are
big enough; l-diversity ensures they aren't homogeneous on the
outcomes that matter (e.g., all 5+ subjects in a class share
`outcome=DIED`). Returns `LDiversityResult` with `smallest_diversity`
and a sorted tuple of `(equivalence_class_key, sensitive_attribute_name)`
violation pairs.

**Implementing code:**
- `scripts/security/kanon_gate.py:112-131` — `LDiversityResult` dataclass.
- `scripts/security/kanon_gate.py:134-191` — `l_diversity_check()` core function.

**Regulatory anchor:** Module docstring `kanon_gate.py:20-22` flags
l-diversity ≥ 2 as a "tracked design gap (see references.rst)", anchored
to **ICMR 2017 §11.7** and **NIST SP 800-188 §5**. The "tracked design
gap" wording indicates l-diversity is implemented but not yet enforced
in the canonical agent-tool gate.

---

## 17. Small-cell mask helper

**Description:** Pair of utilities for masking aggregate counts before
they reach the LLM via an agent tool. `mask_small_cell()` returns the
count if `count >= k`, otherwise the label (default `"<5"`).
`suppress_small_cells()` lifts that to a whole `Mapping[Any, int]`,
returning a new dict with values `< k` replaced by the label and keys
untouched. Distinct from the per-cell `suppress_small_cell` scrub rule
(§6) — that one operates on a single field's numeric value at write
time; this one operates on cross-tab / frequency dictionaries at read
time.

**Implementing code:**
- `scripts/security/kanon_gate.py:194-202` — `mask_small_cell()`.
- `scripts/security/kanon_gate.py:205-216` — `suppress_small_cells()`.

**Regulatory anchor:** Module docstring `kanon_gate.py:19-22` covers
this as part of the small-cell suppression complement to k-anon
(**Pillar 1.7**, **ICMR §11.7**, **NIST SP 800-188 §5**).

---

## 18. Sidecar HMAC key management

**Description:** The HMAC key is a 32-byte secret stored at
`$XDG_CONFIG_HOME/report_ai_portal/phi_key` (default
`~/.config/report_ai_portal/phi_key`), outside the repo tree, mode
`0600`. Missing key = hard-fail (`PHIKeyMissingError`); wrong mode =
hard-fail (`PHIKeyPermissionError`); non-64-hex content = hard-fail.
`bootstrap-key` CLI generates a new key via `secrets.token_hex(32)`,
refusing to overwrite an existing key (silent overwrite would
invalidate every prior pseudonym). Rotation = delete the file → full
re-ingestion required (one-way property of the HMAC scheme).

**Implementing code:**
- `scripts/security/phi_scrub.py:610-638` — `load_key()` (existence + mode + content checks).
- `scripts/security/phi_scrub.py:641-666` — `bootstrap_key()` (no-overwrite, fsync, chmod 0600).
- `scripts/security/phi_scrub.py:171-172` — `_KEY_FILE_MODE = 0o600` / `_KEY_HEX_LEN = 64`.
- `scripts/security/phi_scrub.py:195-201` — `PHIKeyMissingError` / `PHIKeyPermissionError`.
- `scripts/security/phi_scrub.py:1281-1311` — `bootstrap-key` + `key-path` CLI subcommands.
- `scripts/security/phi_scrub.py:64-92` — module docstring "Key management" + threat-model summary.

**Regulatory anchor:** No explicit citation in source. Implicit anchor:
**NIST SP 800-188** key-management guidance for de-identification
re-identification-key control; **HIPAA §164.514(c)** (re-identification
code rules — "the covered entity does not use or disclose the code …
for any other purpose; and does not disclose the mechanism for
re-identification").

---

## 19. Idempotency marker + sentinel

**Description:** Each scrubbed row gets a `_phi_scrubbed: "v2"` marker
field; rows already carrying the current marker pass through
unchanged on a re-run (per-row idempotency). Per-study, a sentinel
file `tmp/{STUDY}/.phi_scrub_complete` short-circuits the orchestrator
on a second run (run-level idempotency). The marker version (`v2`)
forces re-scrub when the catalog changes (the v2 bump migrated from
the flat `SUBJ_` pseudonym format to the labelled `<LABEL>_<hex>`
domain-separated format).

**Implementing code:**
- `scripts/security/phi_scrub.py:147-157` — `_SCRUB_VERSION` / `_SCRUB_MARKER_FIELD` / `_SENTINEL_NAME` constants + v1→v2 rationale comment.
- `scripts/security/phi_scrub.py:1022` — `_scrub_row()` writes the marker.
- `scripts/security/phi_scrub.py:1049-1052` — `_scrub_file()` honors the marker as a per-row skip.
- `scripts/security/phi_scrub.py:1204-1210` — `run_scrub()` honors the sentinel file as run-level skip.
- `scripts/security/phi_scrub.py:1272-1275` — Sentinel write at end of successful run (fsync).

**Regulatory anchor:** No explicit citation. Operational mechanism
ensuring deterministic safe-restart semantics; not a regulatory
control per se. Aligned with **NIST SP 800-188** repeatable-process
guidance.

---

## 20. Clinical-phrase allowlist (false-positive suppression)

**Description:** Three pure functions and four seed datasets that
suppress generic name-like and date-like warn-tier hits on legitimate
clinical free-text. `is_clinical_phrase(text)` matches whole text
against `CLINICAL_PHRASES` *or* asserts every token is in
`CLINICAL_SINGLE_WORDS`. `is_clinical_free_text(text)` matches a set
of context-aware regex patterns ("patient expired", "died on …",
"DOTS … expired"). `looks_like_real_name(text)` returns True when a
2-4 token capitalized string has at least one token in
`COMMON_FIRST_NAMES` or `COMMON_LAST_NAMES`, AND is not a clinical
phrase. The lists are deliberately small seeds curated for Indo-VAP /
RePORT India free-text shapes (TB outcomes, pregnancy, study outcome,
specimen status, smoking status, generic lab indicators, English +
Indian first/last-name samples). Used exclusively by the warn tier of
the gate (not the blocking tier).

**Implementing code:**
- `scripts/security/phi_allowlist.py:34-77` — `CLINICAL_PHRASES` frozenset.
- `scripts/security/phi_allowlist.py:81-136` — `CLINICAL_SINGLE_WORDS` frozenset.
- `scripts/security/phi_allowlist.py:139-171` — `COMMON_FIRST_NAMES` frozenset.
- `scripts/security/phi_allowlist.py:175-199` — `COMMON_LAST_NAMES` frozenset.
- `scripts/security/phi_allowlist.py:206-221` — `_PATIENT_VARIANTS` / `_EXPIRED_VARIANTS` / `_CLINICAL_FREE_TEXT_PATTERNS` regex catalog.
- `scripts/security/phi_allowlist.py:229-242` — `is_clinical_phrase()`.
- `scripts/security/phi_allowlist.py:245-254` — `is_clinical_free_text()`.
- `scripts/security/phi_allowlist.py:257-271` — `looks_like_real_name()`.
- `scripts/security/phi_gate.py:103-114` — Warn-tier consumer (per-match `PERSON_NAME_GENERIC` filtering).
- `scripts/security/phi_gate.py:118-125` — Whole-text consumer.

**Regulatory anchor:** No explicit citation. The allowlist is a
precision-tuning mechanism for the warn tier rather than a regulatory
control. Implicit anchor: **NIST SP 800-188** false-positive /
analytic-utility balance and **HIPAA Expert Determination** principle
that de-identification controls must not destroy analytic value.
