# Authority note — `compliance_posture: icmr_coded_dataset`

## Status

Binding standard for RePORT AI Portal PHI de-identification. Required by
`scripts/security/phi_scrub.py:load_scrub_config` before the scrubber will
run under `compliance_posture: icmr_coded_dataset` (`phi_scrub.yaml:49`).

## Basis

**India (binding).** ICMR National Ethical Guidelines for Biomedical and
Health Research Involving Human Participants, 2017, §2.3. §2.3 sanctions
**coded (pseudonymised) data** for research use provided the linking key is
held **separately**, under strict security, and the coding plan is reviewed
by the Institutional Ethics Committee (IEC). DPDPA 2023 + DPDP Rules 2025
govern the underlying data as "digital personal data" regardless of coding
(pseudonymisation does not remove it from scope), so DPDPA §8 security
safeguards apply to the published bundle. DPDPA §17(2)(b)'s research
exemption is **not** relied upon — its "prescribed standards" have not been
issued as of this note. Aadhaar Act 2016 §29 independently prohibits
publishing Aadhaar numbers; the scrubber's `must_drop` guard enforces this
absolutely.

**USA (benchmark only, not binding).** The receiving research use is not by
a HIPAA Covered Entity or Business Associate, so HIPAA does not apply of its
own force. It is used voluntarily as a de-identification yardstick:

- HIPAA §164.514(b)(2) Safe Harbor supplies the identifier catalogue
  (`must_drop` / `drop_fields`) and the age > 89 aggregation rule
  (`age_cap`). Its date rule (strip all date elements except year) is
  deliberately **not** followed — see "Date jitter" below.
- HIPAA §164.514(b)(1) Expert Determination is the pattern the date-jitter
  design follows in spirit: retain clinical granularity, document why
  residual re-identification risk is very small. Because HIPAA is not
  binding here, that documentation is this note, and no external expert
  signature is required or blocked on.
- 45 CFR §46.102(e) (Common Rule): coded data the investigator cannot
  re-link is not identifiable private information. The LLM agent cannot
  reach the key (see below), so relative to the agent the bundle is
  unlinkable. This is recorded as an architectural control, not a claim of
  regulatory exemption.

## Key management — "held separately"

The HMAC-SHA256 key lives at a 0600-mode sidecar file
(`$XDG_CONFIG_HOME/report_ai_portal/phi_key`, default
`~/.config/report_ai_portal/phi_key`) — outside the git repository tree and
outside the LLM agent's read zone (`scripts/ai_assistant/file_access.py`).
A missing key hard-fails the scrub; the bootstrap CLI refuses to overwrite
an existing key. This satisfies ICMR §2.3's "linking key held separately
under strict security" requirement, and grounds the Common Rule
unlinkability argument above: the agent has no code path to the key.

## Date jitter (SANT method)

Clinical event dates and (conditionally) birthdate are shifted by a
per-subject deterministic offset in `[-30, +30]` days
(`date_offset_days()`, `phi_scrub.py`), derived from
`HMAC-SHA256(key, subject_id)`. This is the Scrambled Assignment of
Numeric-shift Transform (SANT) method: exact dates are never published, but
every interval between two dates for the same subject is preserved exactly,
which HIPAA Safe Harbor's "keep only the year" rule would destroy for
survival/incidence/person-time analyses. Because HIPAA is a benchmark and
not binding, this stronger-utility / still-de-identifying alternative is
adopted deliberately; the ±30-day envelope is configured in
`phi_scrub.yaml:max_jitter_days`.

## Birthdate

Dropped entirely unless (a) `compliance_posture: limited_dataset` is set
(a stricter posture requiring its own IRB/DUA authority note), or (b) the
study carries no separate age variable at all — in which case birthdate is
jittered with the same per-subject offset as other dates so that
age-at-event survives (dropping DOB with no age column would destroy age
information entirely). When jittered under case (b), the *true* age at a
fixed `age_reference_date` (declared per-study in `phi_scrub.yaml`) is
still checked against the HIPAA age-cap threshold (89) so the benchmark
continues to hold on the derived value, not just on the raw column.

## `small_cell_threshold: 5`

A **project-chosen** k-anonymity parameter applied to household/contact
count fields, not a specific ICMR or DPDPA numeric requirement — no
citable "ICMR §11.7" small-cell rule could be substantiated during audit,
and the prior citation to it has been removed from `phi_scrub.yaml`. If a
citable basis is identified later, this note and the YAML comment should be
updated together.

## IEC / IRB review

The coding plan (this note plus `phi_scrub.yaml`) is the artifact intended
for Institutional Ethics Committee review per ICMR 2017 §2.3. Reviewers
should confirm: (1) the key sidecar path and permissions above, (2) the
`must_drop` / `drop_fields` identifier catalogue, (3) the jitter envelope,
and (4) the small-cell threshold rationale.

## Deployment scope

This posture is scoped to a **single local workstation**. Raw data and the
HMAC key never leave it. Cross-border transfer controls (HMSC clearance,
MTA/MoU, DPDPA §16) are not engaged by this note and are not implemented by
the scrubber — if the published bundle is ever sent to a collaborator, that
is a separate gate: under HMSC practice, coded data whose key still exists
is not anonymised, so either the key stays in India or the bundle must be
re-derived without one.

## Provenance

- Author: RePORT AI Portal PHI pipeline hardening effort.
- Date: 2026-08-10.
- Superseded citations removed from `phi_scrub.yaml` as part of this note:
  SPDI Rules 2011 Rule 3 (superseded in practice — rests on IT Act §43A,
  which DPDPA §44(2)(a) omits effective 13 May 2027), DPDPA §2(t) (a
  definition, not an identifier catalogue), and the unsubstantiated
  "ICMR §11.7" small-cell citation.
