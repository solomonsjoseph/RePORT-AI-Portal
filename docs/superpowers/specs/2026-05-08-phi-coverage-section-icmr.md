# PHI Coverage Section — ICMR 2017 National Ethical Guidelines (§11 + Adjacent)

**Generated:** 2026-05-08
**Anchor:** ICMR National Ethical Guidelines for Biomedical and Health Research Involving Human Participants (2017), §11 *Biological Materials, Biobanking and Datasets* — supplemented by the §1 general principle of privacy and confidentiality, §5 informed consent (data-confidentiality element), §6 Vulnerability, and §4 Ethical Review Procedures (record retention).
**Inventory:** `docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md` (techniques §1–§20).
**Scope of this document:** map every ICMR-2017 data-protection obligation that bears on processing PHI in clinical research (Indo-VAP is a clinical TB study run inside India) onto an inventoried scrubber/gate technique, mark COVERED / PARTIALLY_COVERED / UNCOVERED, and for UNCOVERED rows propose a concrete extension.

> **Section-numbering note.** The ICMR-2017 master PDF is heavy and the published "highlights"
> commentary (Bavdekar 2018, PMC6644181) re-numbers sections; "Section 11" in that commentary
> is the canonical *Biological Materials, Biobanking and Datasets* chapter. Adjacent
> obligations (privacy/confidentiality general principle, EC retention, vulnerability,
> consent confidentiality element, multicentric anonymized-data review) live in §1, §4, §5,
> §6, §7. This section treats the bundle as the data-protection anchor for clinical research
> in India, since ICMR-2017 disperses data-protection obligations across these sections rather
> than housing them under a single GDPR-style §11. References cited by URL in §Citations.

---

## 1. Anchor scope (what ICMR-2017 actually requires for PHI)

The substantive obligations relevant to a clinical TB study processing CRF data through an
LLM-bound trio bundle are:

| # | ICMR-2017 obligation (paraphrased from official text + commentaries) |
|---|----------------------------------------------------------------------|
| O1 | **General principle of ensuring privacy and confidentiality** — one of the 12 named §1 general principles; binds every research activity, every record, every output. |
| O2 | **Confidentiality of records is a mandatory informed-consent element (§5).** The consent form must commit to maintenance of confidentiality of records; the researcher carries personal responsibility for it. |
| O3 | **Sample/dataset classification regime (§11, Table 4).** Every sample/dataset is one of: (a) Anonymous/unidentified — no identifiers present; (b) Anonymized — systematically de-identified, either reversibly (coded) or irreversibly; (c) Identifiable — identity directly linked. The technique deployed must match the declared class. |
| O4 | **Coded / reversibly-anonymized samples** require a coding key. Access to the key, to the samples, and to the underlying records must be limited (commentary). |
| O5 | **Irreversibly anonymized samples** must have the link to the participant truly removed — re-link cannot be done. |
| O6 | **Custodianship / ownership rule (§11).** The participant is the *owner* of biological samples and associated data; the institution (via the EC) is *custodian/trustee*; the individual researcher has no ownership/custodianship claim. PHI handling must be auditable up to the institution, not the researcher. |
| O7 | **Consent typology for stored/secondary-use samples & datasets.** Future-research re-use requires one of: broad/blanket, tiered with opt-in, specific, delayed, dynamic; with explicit provisions for **withdrawal**, **waiver**, and **re-consent**. |
| O8 | **Material Transfer Agreement (MTA).** Any transfer of biological material — within India or outside — must be governed by a signed MTA. By extension, dataset transfer to external collaborators must have equivalent governance (DUA / data-sharing agreement). |
| O9 | **EC review of dataset/biorepository proposals.** Every transfer of biological materials or available datasets, and every secondary-use proposal, must be reviewed by the EC of the institution or biorepository — explicitly because of "enormous potential for research as well as commercialization." |
| O10 | **Limited / least-privilege access.** Access to identifiable samples and to corresponding records must be limited (§11 commentary). |
| O11 | **Return of results / benefit sharing.** Researchers must make efforts to share research findings and any derived benefits with the providers of research material; commercial value of samples/data must be disclosed in consent. |
| O12 | **Record retention (§4).** EC documentation must be filed/preserved/archived after study completion — minimum 3 years for biomedical/health research (longer for regulatory clinical trials, minimum 5 years per the 2017 update). |
| O13 | **Vulnerability protections (§6).** When the study includes vulnerable participants (economically/politically/socially deprived, compromised autonomy, voluntariness compromised, fear of revenge), additional protections — including audio-visual consent, community representative oversight, and tighter EC review — apply, *and confidentiality controls must be commensurately strengthened* because re-identification harms fall disproportionately on these groups. |
| O14 | **Anonymized-data review pathway (§4 / §7).** Research using anonymized samples or data falls under the multicentric expedited / minimal-risk review pathway — i.e., the de-identification posture itself is part of how the study is reviewed. The de-identification claim must therefore be defensible. |
| O15 | **Expedited-review eligibility for "non-identifiable clinical data" (§4 Table 2).** Research involving *non-identifiable* clinical data, documents and records is expressly listed as a candidate for expedited review. The "non-identifiable" claim is a material representation to the EC. |
| O16 | **Honest-effort caveat in consent (commentary).** Participants must be told that "every effort will be made to protect privacy and ensure confidentiality, [but] it may not be possible to do so under certain circumstances." Re-identification residual risk must be bounded and acknowledged, not denied. |
| O17 | **Special care for genetic data and stigma-prone categories (§10 — adjacent).** Genetic-counselling-style safeguards, stigma/discrimination prevention. Variables that can re-stigmatize (TB, HIV, mental health, caste, religion) inherit this concern. |
| O18 | **Public accountability / CTRI registration.** Trials must be registered in CTRI; published outputs must not contain re-identifiable subject data. The publication / agent-tool boundary must therefore be a hard de-identification gate. |

---

## 2. Requirement ↔ Technique map

For each obligation above, the table lists the inventoried technique(s) that satisfy it (numbers
refer to `2026-05-08-phi-techniques-inventory.md`), the verdict, and a one-line rationale.

| # | ICMR Obligation | Covering technique(s) | Verdict | Rationale |
|---|-----------------|-----------------------|---------|-----------|
| O1 | §1 general privacy/confidentiality principle | §1 HMAC pseudonymization, §3 DROP, §10 Blocking regex, §13 File exclusions, §15 k-anon gate, §17 small-cell mask | COVERED | Every PHI-class identifier has at least one technique; gate enforces at boundary. |
| O2 | §5 ICF "maintenance of confidentiality" element | §1 HMAC pseudonym, §3 DROP, §18 Sidecar HMAC key | PARTIALLY_COVERED | Code enforces the technical commitment, but ICF text lives in study docs; no automated check that the ICF actually states the commitment. |
| O3 | §11 Table 4 sample/dataset classification | §8 Birthdate posture (`safe_harbor` vs `limited_dataset`) + §1 pseudonym + §2 SANT jitter + §15 k-anon gate | PARTIALLY_COVERED | The `compliance_posture` switch matches *coded/reversibly-anonymized* (key sidecar exists) and "limited dataset" matches §11 Table 4's *coded* class. There is no first-class flag for *anonymous/unidentified* output (where even the HMAC pseudonym would be omitted) nor for *irreversibly anonymized* (where the sidecar key is destroyed). |
| O4 | Limited access to coding key + samples + records | §18 Sidecar HMAC key (mode 0600, outside repo, hard-fail on missing/wrong-mode) | COVERED | XDG-config sidecar at 0600 is the canonical coding-key custody control. |
| O5 | Irreversibly anonymized — link removed | (none) | UNCOVERED | The system supports HMAC-pseudonymization (reversible by anyone with the sidecar key) but has no first-class *irreversible* mode that destroys/never-issues the key, drops all quasi-identifiers, and emits a manifest stating "no re-identification path exists in this artefact". Proposed: §21 below. |
| O6 | Custodian = institution, not researcher | §18 sidecar key + §19 idempotency sentinel | PARTIALLY_COVERED | Sidecar lives in user XDG config, not under institutional control. Operationally fine for solo-dev work; weak for a multi-investigator institutional posture. Proposed: §22 below. |
| O7 | Consent typology — broad/tiered/specific + withdrawal/re-consent | (none in scrubber; lives in protocol) | UNCOVERED-by-design | This is a study-protocol concern, not a scrubber concern; flag is for SoT-pipeline alignment. The scrubber must respect a per-subject *withdrawal* signal — see §23 below. |
| O8 | MTA / DUA on transfer | §8 birthdate `limited_dataset` posture (refuses to run unless `authorities/phi_limited_dataset.md` documents IRB + DUA) | PARTIALLY_COVERED | Authority-note enforcement is present but only for the birthdate posture; there is no equivalent gate for *outbound* trio-bundle transfer. Proposed: §24 below. |
| O9 | EC review of dataset/repo proposals | (none) | UNCOVERED | No automated check that the receiving collaborator/biorepository has EC sign-off recorded against the trio-bundle artefact. Proposed: §25 below. |
| O10 | Limited (least-privilege) access | §13 File exclusions + §18 sidecar 0600 + §9 quarantine path | PARTIALLY_COVERED | Filesystem-level controls are in place; no per-artefact ACL or read-audit. |
| O11 | Return of results / benefit sharing | (out of scope for scrubber) | OUT_OF_SCOPE | Pipeline concern, not PHI-scrubber concern. |
| O12 | §4 record retention — 3 yr / 5 yr | §19 idempotency marker + sentinel (file-level) | PARTIALLY_COVERED | The sentinel proves a scrub completed; it does not enforce *retention period* on the scrubbed bundle. Implicit (filesystem retention by ops) but not first-class. Proposed: §26 below. |
| O13 | §6 vulnerability — strengthened controls for vulnerable groups | §3 DROP (caste, religion, occupation, income, education), §5 GENERALIZE (marital, facility), §6 SUPPRESS_SMALL_CELL (household contact counts), §15/§16 k-anon + l-diversity | COVERED | DROP catalog explicitly drops caste/religion/occupation/income; small-cell suppression resists tip-of-distribution attacks on vulnerable subgroups; l-diversity (§16) is the homogeneity-attack defence on stigma-prone outcomes. Note: §16 is implemented but flagged "tracked design gap" — see §16's own caveat. |
| O14 | §4 expedited pathway depends on anonymization quality | §15 k-anon gate (k≥5 default), §1 HMAC, §2 SANT jitter, §3 DROP, §6 SUPPRESS | COVERED | The k≥5 distributional gate is the empirical defence for the "anonymized samples or data" claim made to the EC. |
| O15 | §4 Table 2 "non-identifiable clinical data" expedited claim | §10 Blocking regex catalog at the agent-tool boundary | COVERED | Blocking regex tier (§10) is the contract-test that the LLM-facing surface contains no plain-text PHI. |
| O16 | Bounded residual re-identification risk in consent | §15 k-anon (5+), §16 l-diversity, §6 SUPPRESS | PARTIALLY_COVERED | Risk is bounded but not *quantified* and surfaced for IRB packs. Proposed: §27 below. |
| O17 | §10 genetic / stigma-prone safeguards | §3 DROP (caste, religion), §11 Warn regex (PERSON_NAME_GENERIC), §20 clinical allowlist (TB-vocabulary aware) | PARTIALLY_COVERED | TB stigma is partly mitigated by the clinical allowlist not letting "patient expired" ladder into a name-warn finding; explicit *outcome-homogeneity* defence is l-diversity (§16) which is "tracked gap". |
| O18 | Publication / agent-tool de-identification gate | §10 Blocking regex + §15 k-anon + §17 small-cell mask | COVERED | Three-layer enforcement at the trio-bundle → agent boundary. |

**Counts:** 18 obligations. **COVERED 7 · PARTIALLY_COVERED 8 · UNCOVERED 2 · UNCOVERED-by-design 1 (out-of-scope for scrubber).** OUT_OF_SCOPE: 1 (O11).

---

## 3. Gaps — proposed extensions

The seven proposals below name new technique slots in the inventory (numbering continues after
§20). Each is scoped to be implementable inside `scripts/security/` without changing the
trio-bundle protocol.

### §21 — Irreversible anonymization mode (covers O5)
Add a third `compliance_posture` value: `anonymous_export`. In this mode:
- HMAC pseudonyms are replaced with non-keyed surrogates (e.g., row-stable random IDs from a per-export entropy pool that is destroyed at export end).
- Sidecar key is *not consulted* for export-time fields; pseudonyms become irreversible by construction.
- A manifest sidecar `anonymous_export.manifest.json` is emitted documenting "no re-identification path persisted in this artefact"; manifest hash is committed.
- Birthdate, partial geography, low-frequency labs, and any quasi-identifier with k<5 distribution are *forced-dropped* (no escape via `keep_fields`).

### §22 — Institutional sidecar custody (covers O6)
Optional `sidecar_custody: institutional` mode. Key file lives at a path declared by an
`authorities/phi_institutional_custody.md` note (signed by the institution's IT-Sec lead),
not under user XDG. Loader hard-fails if posture is `institutional` and the authority note
is absent or the path mode is not 0600 owned by an institutional service account.

### §23 — Per-subject withdrawal signal (covers part of O7)
Add an `authorities/phi_withdrawn_subjects.txt` file (newline-delimited subject IDs). At
scrub time, any row whose resolved `subject_id` matches a withdrawn ID is dropped (not
quarantined, not pseudonymized) and counted into the audit ledger as `withdrawn=N`. The
file is the operational hook for the §11 "withdrawal" provision.

### §24 — Outbound transfer (DUA / MTA) gate (covers O8)
A `phi_outbound_transfer.md` authority note must exist, citing the receiving party + MTA/DUA
identifier + EC approval ID, before the trio-bundle export step runs. Mirrors the existing
`phi_limited_dataset.md` pattern. Hard-fail at orchestration time if missing.

### §25 — EC sign-off ledger (covers O9)
Each trio-bundle export records the IRB/EC approval ID it claims authority under; the audit
ledger persists this alongside the run hash so an external auditor can verify claim → EC
record. Requires a small extension to `_phi_audit.ndjson`.

### §26 — Retention manifest (covers O12)
At export end, write `retention.json` declaring `study_close_date`, retention period (3y or
5y per study type), and computed disposal date. Operational disposal is out of scope; the
manifest makes the obligation auditable and links it to the institutional retention process.

### §27 — Quantified residual-risk note (covers O16 / O14)
Each export emits `reidentification_risk.json` with: `k_min`, `l_min`, count of suppressed
quasi-identifier classes, count of dropped rows due to k-violations, and the date-jitter
envelope used. Provides a single numeric IRB-facing snapshot of the residual-risk claim made
in the consent form. The data is already computed by `kanon_gate`; this proposal surfaces it
as a first-class artefact.

---

## 4. Concerns / open questions

1. **Section-numbering provenance.** The PMC highlights commentary (Bavdekar 2018) numbers Section 11 as *Biological Materials, Biobanking and Datasets*. Some pre-existing inline citations in `phi_scrub.yaml` ("ICMR §11.7", "ICMR §11.4") use what looks like a sub-section numbering scheme that is not visible in the highlights commentary; the scheme may match the master PDF (which we could not text-extract — the official PDF is image-laden and FlateDecode-encoded). A follow-up investigation should confirm the §11.7 / §11.4 numbering against the master document so that the YAML citations are demonstrably accurate.
2. **§16 l-diversity is "tracked design gap"** in the scrubber's own docs — and ICMR §11 stigma-prone groups (TB outcome) is exactly where l-diversity matters. Promoting l-diversity from "implemented but not enforced" to "enforced at the gate" closes the largest single ICMR-aligned gap.
3. **§13 file exclusions are not consumed at runtime** (per the YAML preamble); enforcement lives in `file_discovery.py`. ICMR §11 limited-access principle is satisfied operationally, but parity between the documented YAML list and the runtime list is fragile and worth a design follow-up.
4. **Vulnerability cascade.** ICMR §6 says vulnerable-group studies need *strengthened* controls. The scrubber is uniform across studies; there is no `compliance_posture: vulnerable` mode that automatically lowers k from 5 → 10 or forces l-diversity. Indo-VAP includes pregnant women and economically-deprived TB patients — both ICMR-§6 vulnerability axes — so this cascade is concretely relevant.
5. **No automated check that the ICF text actually claims confidentiality** (O2). The technical control is in place but the documentary commitment is not cross-checked.

---

## 5. Citations (URLs)

- ICMR National Ethical Guidelines (2017) — official PDF (NCDIR ethics portal): https://ethics.ncdirindia.org/asset/pdf/ICMR_National_Ethical_Guidelines.pdf
- ICMR Ethical Guidelines page (NCDIR ethics portal landing): https://ethics.ncdirindia.org/icmr_ethical_guidelines.aspx
- ICMR portal mirror (resource-guidelines): https://www.icmr.gov.in/icmrobject/custom_data/pdf/resource-guidelines/ICMR_Ethical_Guidelines_2017.pdf
- ICMR (DST mirror): https://www.indiascienceandtechnology.gov.in/sites/default/files/file-uploads/guidelineregulations/1527507675_ICMR_Ethical_Guidelines_2017.pdf
- ICMR Handbook on Ethical Guidelines (NAITIK / DHR): https://naitik.gov.in/DHR/resources/app_srv/DHR/global/pdf/downloads/Handbook_on_ICMR_Ethical_Guidelines.pdf
- Bavdekar SB (2018), "Highlights of ICMR National Ethical Guidelines for Biomedical and Health Research Involving Human Participants," PMC6644181 — primary source for the §11 (biomaterials/biobanking/datasets) summary, Table 4 sample-class taxonomy, and the §1/§4/§5/§6 obligations cited above: https://pmc.ncbi.nlm.nih.gov/articles/PMC6644181/
- Mathur R, Swaminathan S (2018), "ICMR National Ethical Guidelines: A commentary," PMC6251259 — primary source for the custodianship rule and §11 left-over-samples / transfer / long-term-storage / return-of-results / benefit-sharing language: https://pmc.ncbi.nlm.nih.gov/articles/PMC6251259/
- Sil A et al. (2019), "ICMR National Ethical Guidelines: The way forward from 2006 to 2017," PMC6647898 — primary source for record-retention durations (3y/5y), broad-consent typology, and CTRI/public-accountability language: https://pmc.ncbi.nlm.nih.gov/articles/PMC6647898/
- Indian Journal of Medical Ethics commentary (long form): http://ijme.in/articles/national-ethical-guidelines-for-biomedical-and-health-research-involving-human-participants-2017-a-commentary/?galley=html
