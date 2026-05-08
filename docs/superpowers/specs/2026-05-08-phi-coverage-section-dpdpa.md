# PHI Coverage Section — DPDPA 2023

**Generated:** 2026-05-08
**Anchor:** Digital Personal Data Protection Act, 2023 (Act No. 22 of 2023, Gazette of India, 11 Aug 2023).
**Scope:** every DPDPA obligation that bears on processing the personal
data of Indo-VAP / RePORT India clinical-research subjects in this
pipeline, mapped onto the §1-§20 PHI techniques inventory at
`docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`.

---

## 1. Anchor scope

DPDPA applies extraterritorially when digital personal data of data
principals located in India is processed (§3(a)-(b)). Indo-VAP is an
Indian-cohort study; subject records are personal data of Indian data
principals, so the Act applies in full. Two structural points up front:

- **No "sensitive personal data" tier.** DPDPA collapses the GDPR-style
  distinction — every byte of identifiable subject data is "personal
  data" under §2(t): "any data about an individual who is identifiable
  by or in relation to such data." The 2011 SPDI Rules continue to
  apply concurrently, but only until 13 May 2027 when §44(2) repeals
  IT-Act §43A and the SPDI Rules.
- **Research carve-out is partial, not blanket.** §17(2)(b) exempts
  Chapter II §§5-8 (excluding §8(1) and §8(5)) and Chapter III rights
  for research, archiving, or statistical processing — but only where
  no decision is taken about a specific data principal *and* the
  Central Government's de-identification standards (still being
  notified) are met. §8(1) (overall responsibility) and §8(5)
  (reasonable security safeguards) survive even under the research
  carve-out. So security and accountability are non-negotiable; consent
  / notice / retention / breach-notification can be relaxed if the
  research conditions are met.

For the de-identified, agent-boundary trio bundle this pipeline ships,
the research carve-out plausibly applies — but the pipeline must still
demonstrably satisfy §8(1) and §8(5) and meet "identity cannot be
inferred" as a binding precondition.

---

## 2. Requirement ↔ technique map

| # | DPDPA obligation | Citation | Inventoried technique | Status |
|---|---|---|---|---|
| R1 | Personal-data definition: anything by which an individual is identifiable | §2(t) | §1 (HMAC pseudonymization), §3 (DROP), §4 (CAP), §5 (GENERALIZE), §10 (blocking regex), §12 (subject-ID regex) | COVERED |
| R2 | Lawful purpose, purpose limitation; processing only for the specified consented purpose | §4, §5(i), §6 | None directly. Pipeline assumes upstream IRB-consented purpose; no in-pipeline purpose-binding control. | UNCOVERED |
| R3 | Notice content — what data, what purpose, rights, grievance route | §5(1)(i)-(v) | None — out of scope of `scripts/security/`. | UNCOVERED (out of scope) |
| R4 | Free, specific, informed, unconditional, unambiguous consent with clear affirmative action | §6(1) | None — handled by IRB-approved ICF upstream. | UNCOVERED (out of scope) |
| R5 | Legitimate-use grounds (incl. medical emergency, public-health, employment) | §7(b), §7(g) | Implicit posture under §8 birthdate handling — Limited Dataset path requires a documented authority note. | PARTIALLY_COVERED |
| R6 | §8(1) absolute, non-delegable accountability — survives the research carve-out | §8(1), §17(2)(b) proviso | §18 (sidecar key mgmt with no-overwrite, mode 0600), §19 (idempotency marker + sentinel), §13 (file exclusions doc-only, see gap), audit log surface from `phi_gate.py` | PARTIALLY_COVERED |
| R7 | §8(2) processor contract requirement | §8(2) | None at pipeline layer; contractual control. | UNCOVERED (out of scope) |
| R8 | §8(3) accuracy / completeness / consistency when data drives decisions or is disclosed | §8(3) | §7 (KEEP allowlist preserves clinically-required fidelity), §2 (date jitter preserves intervals), §6 / §17 (small-cell suppression at threshold preserves typed numerics) | COVERED |
| R9 | §8(4)+§8(5) reasonable security safeguards (encryption, access control, monitoring, backups) — survives the research carve-out | §8(4), §8(5), DPDP Rule 6 | §1 (HMAC obscures direct identifiers), §18 (key file mode 0600, no overwrite, hard-fail on missing/wrong-perm key), §10-§12 (blocking and warn regex tiers in `phi_gate.py` enforce egress hygiene), §9 (orphan quarantine prevents un-jittered leak) | PARTIALLY_COVERED |
| R10 | §8(6) personal-data-breach notification to DPB and affected principals | §8(6), DPDP Rule 7 | §10 (blocking-tier audit findings give the operational signal); no automated notification workflow. | PARTIALLY_COVERED |
| R11 | §8(7) erasure on consent withdrawal or when purpose no longer served; cause processor to erase | §8(7) | None at scrub time. Pipeline writes derived artefacts; no built-in erasure-by-subject hook. | UNCOVERED |
| R12 | §8(8) deemed cessation of purpose by inactivity (sector-specific clocks under DPDP Rule 8) | §8(8) | None. | UNCOVERED |
| R13 | §8(9) DPO / contact person for grievance | §8(9) | None — operational policy artefact. | UNCOVERED (out of scope) |
| R14 | §8(11) reasonable measures to ensure data quality | §8(11) | §7 (KEEP), §20 (clinical-phrase allowlist suppresses false-positive over-redaction) | COVERED |
| R15 | §9 children — verifiable parental consent; no behavioural tracking; no targeted advertising | §9(1)-(3) | §3 DROP catalog drops minor-marker fields (`asha/anm/anganwadi worker IDs`, `child birth years` per inventory §3 anchors); birthdate handling §8 is age-aware. No verifiable-parental-consent gate at the data layer. | PARTIALLY_COVERED |
| R16 | §10 Significant Data Fiduciary additional duties (DPIA, audits, DPO) — applies if MeitY notifies the study sponsor | §10(2) | None at the scrub layer. | UNCOVERED (out of scope) |
| R17 | §11 right to information / summary of processing | §11(1) | None. | UNCOVERED (out of scope) |
| R18 | §12 right to correction / completion / updating / erasure | §12(1)-(3) | §1 HMAC pseudonymization is one-way → satisfying erasure means deleting the source rows + rotating the key (§18 supports key rotation by file deletion); no per-subject correction hook. | PARTIALLY_COVERED |
| R19 | §13 grievance redressal with published contact | §13 | None — operational artefact. | UNCOVERED (out of scope) |
| R20 | §16 cross-border transfer subject to MeitY "negative list" (Rule 15) | §16(1)-(2) | None — pipeline does not enforce destination jurisdiction; trio bundle could egress to any LLM endpoint. | UNCOVERED |
| R21 | §17(2)(b) research / archiving / statistical exemption — only if "identity of the Data Principal cannot be inferred" | §17(2)(b) | §1 (HMAC pseudonymization), §2 (date jitter), §3-§7 (drop/cap/generalize/keep/birthdate), §15 (k-anonymity gate), §16 (l-diversity gate), §17 (small-cell mask), §6 (small-cell field clamp), §10 (egress-time blocking regex), §14 (free-text whole-value drop) | COVERED |
| R22 | Penalty exposure: up to ₹250 cr for §8(5) failure (Schedule item 2) | §33 + Schedule | Indirectly mitigated by the §1, §10, §15-§18 stack. | COVERED (mitigation) |

---

## 3. Gaps

### 3.1 UNCOVERED — propose new technique or extension

**G1. Purpose-binding manifest (R2, R4, R7).**
DPDPA §4-§6 require processing be tied to a specified, consented
purpose. The pipeline today assumes the upstream IRB ICF is binding but
records no purpose tag on the trio bundle. **Proposed extension:**
add a `purpose_id` field to the run manifest in
`scripts/security/phi_scrub.py` (mirrored into the sentinel file at
§19) and refuse to write a trio bundle whose `purpose_id` is missing
from `authorities/phi_purposes.yaml`. Cost: small; one new YAML, one
loader hook, one CLI flag.

**G2. Erasure-by-subject hook (R11, R12, R18).**
§8(7) requires erasure on withdrawal; §12 grants the data principal an
explicit erasure right. No technique today supports per-subject
deletion across the derived artefacts. **Proposed new technique
(§21 candidate):** "Per-subject erasure ledger" — keep an
append-only `erasure/{study}/requests.jsonl` whose entries
(`{subject_id, requested_at, basis}`) are ingested at the start of
every scrub run; matching subjects are dropped before pseudonymization
and added to the orphan quarantine block-list. Idempotent under §19's
re-run semantics.

**G3. Cross-border egress gate (R20).**
§16(1) lets the Central Government restrict transfers to listed
countries. The pipeline does not gate on the LLM endpoint's
jurisdiction. **Proposed extension:** add an `allowed_destinations`
list in `phi_scrub.yaml` (default empty = block) and surface it to the
agent-tool boundary as a precondition check before any trio-bundle
egress.

### 3.2 PARTIALLY_COVERED — propose technique extension

**G4. §8(5) security envelope (R9).**
The §1+§18 stack covers identifier obscuration and key custody, but
DPDP Rule 6 enumerates "encryption, obfuscation, masking, access
control, logs, monitoring, backups, breach detection." At-rest
encryption of `tmp/{STUDY}/quarantine/*.jsonl` is currently delegated
to OS file permissions (mode 0600 on the key file only).
**Proposed extension to §18:** widen the sidecar key contract to a
second 32-byte key for AES-256-GCM encryption of quarantine + working
artefacts; same path / mode discipline; rotation = re-ingestion.

**G5. §8(6) breach notification workflow (R10).**
§10 produces blocking findings but no externalised alarm. Rule 7
requires notification "as soon as possible … and in any event within
72 hours" to the DPB and affected principals. **Proposed extension to
§10/§19:** emit a structured `phi_breach_signal.jsonl` whenever the
gate trips with `blocked=True`; ops layer wires it to the breach-
notification workflow.

**G6. §9 children (R15).**
DROP catalog removes ASHA / Anganwadi / child-birth-year fields, but
the pipeline has no first-class "is the row about a child?" check that
fires the §9(2)-(3) prohibitions on detrimental processing /
behavioural tracking / targeted advertising. **Proposed extension to
§7/§8:** add a `child_subject_flag` derived field (from age < 18
post-cap or birthdate-based) and a posture refusal for any pipeline
mode that would feed children's data into a generative model without
a Limited-Dataset-style authority note.

**G7. §17(2)(b) "identity cannot be inferred" attestation (R21).**
Today the §15 k-anon gate and §16 l-diversity gate exist but
l-diversity is documented as a "tracked design gap" (per inventory
§16). Without enforced l-diversity, the §17(2)(b) safe-harbour
condition is structurally weaker than DPDPA expects.
**Proposed:** promote §16 l-diversity from documented to
gate-enforced before claiming §17(2)(b) coverage in any IRB
attestation.

---

## 4. Citations

- **Primary statute.** Digital Personal Data Protection Act, 2023
  (Act No. 22 of 2023). Indexed via dpdpa.com chapter pages
  (alternative authoritative repository while MeitY's PDF mirror
  returned HTTP 404 at the time of this fetch on 2026-05-08):
  - §2 definitions: https://www.dpdpa.com/dpdpa2023/chapter-1/section2.html
  - §4 grounds: https://www.dpdpa.com/dpdpa2023/chapter-2/section4.html
  - §5 notice: https://www.dpdpa.com/dpdpa2023/chapter-2/section5.html
  - §6 consent: https://www.dpdpa.com/dpdpa2023/chapter-2/section6.html
  - §7 legitimate uses: https://www.dpdpa.com/dpdpa2023/chapter-2/section7.html
  - §8 obligations of Data Fiduciary: https://www.dpdpa.com/dpdpa2023/chapter-2/section8.html
  - §9 children: https://www.dpdpa.com/dpdpa2023/chapter-2/section9.html
  - §10 Significant Data Fiduciary: https://www.dpdpa.com/dpdpa2023/chapter-2/section10.html
  - §16 cross-border transfer: https://www.dpdpa.com/dpdpa2023/chapter-4/section16.html
  - §17 exemptions: https://www.dpdpa.com/dpdpa2023/chapter-4/section17.html
- **Indian Kanoon mirror.** §17 in DPDPA, 2023:
  https://indiankanoon.org/doc/180784190/
- **PRS India bill tracker.** The Digital Personal Data Protection
  Bill, 2023 → Act:
  https://prsindia.org/billtrack/digital-personal-data-protection-bill-2023
- **MeitY (Ministry of Electronics and Information Technology).**
  Authority for DPDP Rules, 2025 (implementing §16 via Rule 15;
  §8(4)/(5) via Rule 6; §8(6) via Rule 7; §8(8) via Rule 8 and Third
  Schedule). The MeitY PDF at the URL given in the brief
  (`https://www.meity.gov.in/writereaddata/files/Digital%20Personal%20Data%20Protection%20Act%202023.pdf`)
  returned HTTP 404 from this sandbox on 2026-05-08; substantive
  citations were therefore drawn from dpdpa.com's chapter pages and
  the Indian Kanoon mirror, both of which reproduce the official
  statutory text. The reader should cross-check against MeitY's live
  publication when MeitY restores the URL.
- **SPDI / sensitive-data-tier status.** DPDPA §44(2) repeals IT-Act
  §43A and SPDI Rules effective 13 May 2027; current DPDPA does *not*
  re-introduce a "sensitive personal data" tier. Background:
  https://forum.nls.ac.in/ijlt-blog-post/removing-the-category-of-sensitive-personal-data-under-the-dpdpa-heightening-data-principal-vulnerability/

---

**Word count (body, excluding tables/citations):** ~1100.
