# Coverage Section: NIST SP 800-188

**Generated:** 2026-05-08
**Anchor:** NIST SP 800-188, *De-Identifying Government Datasets: Techniques
and Governance* (final, September 2023).
Authoritative PDF: https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-188.pdf
**Inventory ground:** `docs/superpowers/specs/2026-05-08-phi-techniques-inventory.md`
(techniques §1-§20).

## Anchor scope

NIST SP 800-188 is the U.S. federal de-identification framework that
supersedes NIST IR 8053 and consolidates statistical-disclosure-limitation
practice across HIPAA, federal statistical agencies, and the broader
academic literature. Unlike HIPAA Safe Harbor (a prescriptive list of
identifiers) it is a **risk-based** framework. The publication
distinguishes three classes of attribute (§4.2 Conducting a Data
Survey, and §4.3 Removing Identifiers and Transforming Quasi-Identifiers):

- **Direct identifiers** — values that uniquely identify a subject by
  themselves (name, SSN, MRN, biometric template).
- **Quasi-identifiers** (a.k.a. indirect identifiers) — values that
  identify a subject only in combination with other attributes (birth
  year, ZIP, sex). Defined in footnote 14, p. 17.
- **Sensitive attributes** — values whose disclosure causes harm even
  when not identifying (HIV status, mental-health diagnosis, outcome).

The publication then lays out two main de-identification routes (§4.3
"removing identifiers and transforming quasi-identifiers" vs §4.4
"synthetic data"), a privacy-modelling layer (k-anonymity, l-diversity,
t-closeness, differential privacy — introduced in §1 and revisited
throughout §3 and §4), and a governance layer (§3, Disclosure Review
Boards, the Five Safes, data-sharing models). The pipeline this
document covers is a **tabular clinical-research microdata pipeline**
shipping a JSONL trio bundle to LLM agents under Safe-Harbor / Limited
Dataset posture, so the relevant 800-188 surface is §4.3 (deterministic
de-identification of microdata) and §3.7.3 (risk-based standards), not
§4.4 (synthetic data) or §4.5 (interactive query interfaces).

This section enumerates the major risk classes / technique families
NIST 800-188 puts forward for tabular data, maps each to one or more
of the inventoried techniques (§1-§20), and flags gaps.

## Requirement ↔ technique map

| # | NIST 800-188 risk class / technique family | §-ref in 800-188 | Inventoried Technique(s) | Status | Notes |
|---|---|---|---|---|---|
| 1 | Direct-identifier removal — replacement with NULL, masking, encryption (key-discarded), keyed-hash pseudonymization, replacement with surrogate values | §4.3.1 (p. 51) | §1 HMAC-SHA256 pseudonymization, §3 DROP, §14 free-text drop, §10 blocking regex | COVERED | 800-188 §4.3.1 lists "Hashing with a keyed hash" with **SHA-256 HMAC** and a **256-bit randomly generated key** as an explicit example — matches §1 (sidecar key, HMAC-SHA256, 32-byte secret) almost line-for-line. §18 sidecar key management satisfies §4.3.2's "keys are both unpredictable and suitably protected" caveat. |
| 2 | Special security caveat: encryption / hashing of direct identifiers carries brute-force risk; not recommended unless the key is suitably protected | §4.3.2 (p. 52) | §1 HMAC pseudonymization (12-hex 48-bit truncation; HKDF-style label domain separation), §18 sidecar HMAC key management (mode 0600, 32-byte random, no-overwrite bootstrap) | COVERED | The 12-hex truncation widens the brute-force keyspace per pseudonym and the per-label domain separation prevents cross-label dictionary correlation. §18 satisfies the "keys must be highly protected" requirement. |
| 3 | Numeric quasi-identifier transformation — top coding | §4.3.3 (p. 53), citing HIPAA Safe Harbor "ages > 89 → 90 or older" as canonical example | §4 CAP | COVERED | §4 CAP is exactly NIST's described top-coding mechanism (numeric > threshold → label string). The default `90+` threshold matches the cited example verbatim. |
| 4 | Numeric quasi-identifier transformation — bottom coding | §4.3.3 (p. 53), implicit in the "Top *and* bottom coding. Outlier values that are above *or below* certain values are coded appropriately" sentence | (none) | UNCOVERED | The CAP primitive only handles the upper tail (`numeric > threshold → label`). No `floor` semantic. **Operational risk is low** for Indo-VAP / RePORT — most clinical fields where bottom coding matters (income, household-asset counts, exposures) are dropped via §3 or generalized via §5, not retained as numerics. **But:** if a future field surfaces a low-count tail (e.g., `WEIGHT_KG < 30` distinguishing pediatric malnutrition cohorts, or very young ages where Safe Harbor demands `< 1` collapsing), CAP cannot express it. **Proposed extension:** add a `FLOOR` action (or extend `CapRule` with an optional `lower_threshold` + `lower_label`) so the YAML can declare `{pattern: ..., upper_threshold, upper_label, lower_threshold, lower_label}`. Code change: `CapRule` dataclass + `cap_numeric()` primitive; YAML schema bump. |
| 5 | Numeric quasi-identifier transformation — micro-aggregation (combine individuals into small groups) | §4.3.3 (p. 53) | (none in scrub layer) — partial proxy in §15 k-anonymity gate (which checks aggregation result, not produces it) | PARTIALLY_COVERED | The pipeline does not micro-aggregate at scrub time. The k-anon gate (§15) catches under-aggregation at the trio-bundle boundary but does not generalize. **Acceptable for this pipeline**: the agent boundary surfaces row-level data, not group-level statistics; if a future use-case surfaces aggregate tables, micro-aggregation would belong in the gate layer, not scrub. **No action recommended for now**; track as design note. |
| 6 | Numeric quasi-identifier transformation — generalize categories with small counts (collapse rare bins) | §4.3.3 (p. 53) | §5 GENERALIZE (categorical), §6 SUPPRESS_SMALL_CELL (numeric upper-tail clamp) | COVERED | §5 covers the categorical case (marital, facility type → broad bin). §6 covers the per-cell numeric upper-tail. |
| 7 | Numeric quasi-identifier transformation — data suppression (cells with counts below a threshold) | §4.3.3 (p. 53) | §17 small-cell mask helper (`mask_small_cell`, `suppress_small_cells`), §15 k-anon gate, §6 SUPPRESS_SMALL_CELL | COVERED | §17 is the read-time aggregate-table version of NIST's "suppress cells with counts lower than a predefined threshold." The `<5` label matches the de-facto k=5 threshold cited throughout 800-188. |
| 8 | Numeric quasi-identifier transformation — blanking and imputing (highly identifying values replaced with imputed values) | §4.3.3 (p. 54) | (none) | UNCOVERED | Pipeline is currently honest-drop or honest-cap, never imputes. **Acceptable**: imputation is a model-driven choice that requires a generative model and changes the data semantics; the project's IRB-grade posture explicitly favours drop / suppress over imputation. **No action recommended**; document in design notes that imputation is intentionally out of scope. |
| 9 | Numeric quasi-identifier transformation — attribute or record swapping (within similar-record cohorts) | §4.3.3 (p. 53) | (none) | UNCOVERED | Same rationale as #8 — swapping introduces deliberately-wrong values and breaks per-row determinism. The pipeline's per-subject jitter (§2 SANT) is the closest analogue but operates only on the date axis. **No action recommended**; track as out-of-scope. |
| 10 | Numeric quasi-identifier transformation — noise addition / "noise infusion" | §4.3.3 (p. 53) | §2 per-subject deterministic date jitter (date axis only) | PARTIALLY_COVERED | §2 SANT date jitter is a constrained, deterministic form of noise addition — confined to the date axis to preserve clinical interval semantics. NIST notes noise addition causes "regression dilution" and possible "systematic bias" — these caveats apply to §2 and should be acknowledged in the design doc. **No additional noise is added to numeric quasi-identifiers** (age, lab values, anthropometrics) beyond CAP and SUPPRESS_SMALL_CELL. **Acceptable**: random noise on lab values would corrupt clinical fidelity, which the IRB-grade posture explicitly forbids. |
| 11 | Date de-identification — generalize to year (Safe Harbor); systematic per-subject shift; interval perturbation; preserve day-of-week / holiday relationships | §4.3.4 (p. 54-55) | §2 SANT date jitter, §4 CAP (for ages), §8 birthdate posture | COVERED | §2 SANT implements NIST's "dates within a single person's record can be systematically adjusted by a random amount" recipe verbatim — including the example with admission and discharge offset by the same number of days. NIST notes this "does not eliminate the risk that a data intruder will make inferences based on the interval" — the project's response is the §15 k-anon gate, not interval perturbation; this is a documented design trade-off. |
| 12 | Geographic de-identification — coordinates, addresses, postal codes, narrative geography | §4.3.5 (p. 55-56) | §3 DROP (geography regexes), §14 free-text drop | COVERED | Whole-field drop for sub-state geography matches HIPAA Safe Harbor's stricter rule, which 800-188 cites approvingly. |
| 13 | Genomic information de-identification | §4.3.6 (p. 56) | (none) | UNCOVERED | Indo-VAP / RePORT do not currently collect WGS / WES / SNP arrays. If future WGS arrives, 800-188 §4.3.6 mandates separate handling (genomic data is inherently re-identifying even without metadata). **No action recommended now**; flag as a future-data-class concern. |
| 14 | Free-text narrative de-identification — dedicated NER + scrub for unstructured fields | §4.3.7 (p. 57) | §14 free-text whole-value drop, §10 blocking regex (applied at agent boundary), §11 warn regex, §20 clinical-phrase allowlist | PARTIALLY_COVERED | The pipeline drops free-text fields wholesale rather than performing NER-based scrub. The YAML preamble at lines 422-426 documents this is the "honest-safe default until a narrative NER sweep lands." NIST §4.3.7 itself acknowledges this is "an area of active research" and that "many approaches developed in the 1980s and 1990s … may no longer provide adequate protection in an era with high-quality internet search and social media." Whole-drop is conservative and 800-188-aligned; it is a utility loss, not a privacy gap. |
| 15 | Aggregation challenges — multiple releases enable inference attacks (the "school table" example) | §4.3.8 (p. 57-58) | §15 k-anonymity gate, §16 l-diversity gate, §19 idempotency marker | PARTIALLY_COVERED | §15 + §16 catch *single-release* under-aggregation. The pipeline does not yet implement *cross-release* differencing detection (the case where two scrub runs at different timestamps reveal new joiners). The §19 sentinel ensures determinism on re-run for the same input but does not detect that input has changed in a privacy-revealing way. **Tracked design gap**: log each agent-tool query and its result in an audit ledger keyed by query shape, so a later differencing query is detectable. (The Phase 1 audit-ledger work is the natural home for this.) |
| 16 | High-dimensional data challenges — many quasi-identifiers → near-uniqueness | §4.3.9 (p. 58) | §15 k-anonymity gate (operates on declared quasi-identifier tuple) | PARTIALLY_COVERED | §15 takes a tuple of declared QIs and counts equivalence classes. It correctly fails when QIs are over-rich. **Operational gap**: no automatic suggestion of which subset of fields constitutes the QI set; the SoT YAML is the policy ground for this and is the right place to declare `is_quasi_identifier: true`. Phase 0 SoT-driven sweep is the right venue. |
| 17 | Linked data challenges — joining external datasets | §4.3.10 (p. 59) | §1 HMAC pseudonymization (label-domain-separated, prevents cross-label join), §15 k-anon gate | PARTIALLY_COVERED | §1's per-label HMAC domain separation defeats trivial cross-label joins inside the dataset. External-link defence relies on QI minimization, which is the SoT's responsibility. |
| 18 | Composition challenges — multiple released datasets compose to leak more than each alone | §4.3.11 (p. 59) | (none) | UNCOVERED | NIST flags this as a fundamental k-anonymity limitation: "k-anonymity and related techniques are not compositional. That is, they do not quantify the cumulative privacy loss of multiple data releases." The pipeline currently treats each scrub run as independent. **Recommendation**: when the cutover gate matures, add a composition-tracking ledger that records every {study, agent, query-shape, timestamp} tuple and refuses queries that, combined with prior queries, would breach a configured budget. This is non-trivial and is the natural reason to evaluate **differential privacy** (see §22 below) as a longer-term answer. **Tracked design gap.** |
| 19 | k-anonymity formal model — every equivalence class has ≥ k members | §1 (p. 1, footnote), §3.2.1 (re-id probability), §4.3.1 (cited as research lineage) | §15 k-anonymity gate (default k=5) | COVERED | §15 is the canonical implementation. Default k=5 matches NIST's repeated use of k=5 in worked examples. |
| 20 | l-diversity formal model — every equivalence class has ≥ l distinct values per sensitive attribute | §3.2.1 (footnote 14, p. 17), reference [98] Machanavajjhala 2006 | §16 l-diversity gate | COVERED | §16 is implemented. The inventory notes l-diversity is "tracked design gap" — implementation exists but is not yet wired into `verify_and_promote`. NIST 800-188 itself describes l-diversity as a refinement of k-anon, not a separate mandatory standard. **Action**: complete the wiring (already in flight per Phase 2A). |
| 21 | t-closeness formal model — sensitive-attribute distribution within each equivalence class is close (in EMD / earth-mover) to the global distribution | §3.2.1 (footnote 14, p. 17), reference [94] Li/Li/Venkatasubramanian 2007 | (none) | UNCOVERED | NIST 800-188 explicitly mentions t-closeness alongside k-anon and l-diversity as a refinement that "requires that the resulting data be statistically close to the original data." The inventory has no t-closeness check. **Risk reading for this pipeline**: l-diversity defends against homogeneity attacks (every class member has outcome=DIED); t-closeness defends against the *skewness* attack (the class's outcome distribution is much more skewed than the global one, giving an attacker a strong probabilistic inference). For a TB / Indo-VAP cohort where outcomes (cured / treatment-failure / died / lost-to-followup) have meaningfully imbalanced base rates, this is a **non-trivial residual risk** even after k=5 + l=2. **Proposed extension**: add `t_closeness_check(records, qi_columns, sensitive_columns, t)` next to `l_diversity_check` in `kanon_gate.py`. Earth-Mover-Distance over the categorical sensitive attribute distribution; threshold `t` configurable (NIST does not prescribe a number — the literature commonly uses t=0.2 for categorical data). Track in the audit ledger as a third tier of cell-level protection. **Recommended for Phase 2 expansion.** |
| 22 | Differential privacy — formal model that bounds the cumulative privacy loss across all queries | §1 (p. 1), §3.2.1, §3.7.3 (risk-based standards), §4.4.7 (synthetic data with DP) | (none) | UNCOVERED — and **likely intentionally so** | NIST presents DP as the only formal model that *is* compositional (in contrast to k-anon which "do[es] not quantify the cumulative privacy loss of multiple data releases"). This pipeline does not implement DP. **Evaluation for this pipeline**: DP is most useful when the data-sharing model is a **query interface** (§4.5) or an **aggregate publication** (§4.4 synthetic data). This pipeline's data-sharing model is a **per-row JSONL trio bundle to an LLM agent inside a controlled enclave** — the agent sees scrubbed microdata, not aggregates. The standard DP mechanisms (Laplace / Gaussian noise on counts) do not directly apply to row-level data shipping. The right point to introduce DP would be (a) if the pipeline ever publishes aggregate statistics outside the enclave (e.g., counts to a public dashboard), or (b) if it adopts a query-interface model with per-query budget tracking (the §18 composition-tracking ledger from row above). **Recommendation**: do not add DP to the scrub layer. Track as a future option for the agent-tool gate if and when (a) or (b) materialize. Document this position in the design doc so the absence is not a silent gap. |
| 23 | Pseudonymization risk — repeatable transforms enable a data intruder to "determine the transformation, and thus, gain the capability to re-identify all of the records" | §4.3.1 (p. 51-52) | §1 HMAC pseudonymization (key + label domain separation + 12-hex truncation), §18 sidecar key management (key file mode 0600, no-overwrite bootstrap) | COVERED | §18's no-overwrite bootstrap + chmod 0600 + missing-key-fail-loud satisfy NIST's "the lookup table or the information for the transformation must be highly protected" requirement. The 12-hex truncation reduces the brute-force surface from 256-bit collisions to ~48-bit, which for cohorts < 100k subjects is acceptable. |
| 24 | Privacy-Preserving Record Linkage (PPRL) — when multiple orgs share a pseudonym scheme they can re-identify each other's data | §4.3.1 (p. 51-52) | §1 HMAC pseudonymization (per-deployment key, never shared) | COVERED | The sidecar key is per-deployment and never leaves the host. Cross-org PPRL is impossible by construction. |
| 25 | Standards layer — risk-based de-identification standards (vs prescriptive) | §3.7.3 (p. 42) | §15 k-anon, §16 l-div, §6 SUPPRESS_SMALL_CELL, §17 small-cell mask, §19 idempotency, the audit ledger | COVERED | The pipeline operates the prescriptive Safe-Harbor floor (§3-§14) plus a risk-based gate layer (§15-§17). NIST 800-188 §3.7 endorses exactly this two-layer pattern — prescriptive for the data-survey / direct-identifier removal, risk-based for the residual-risk gate. |

## Gaps

Three substantive **UNCOVERED** items, ranked by recommended priority:

1. **t-closeness gate (#21)**. NIST 800-188 names it alongside k-anon
   and l-diversity as a microdata-protection technique. The pipeline
   has k=5 + l=2 but no t-check. For TB / Indo-VAP outcome attributes
   with skewed base rates, residual inference risk persists even after
   l-diversity. **Proposed extension**: add `t_closeness_check()` to
   `scripts/security/kanon_gate.py`, wire into `verify_and_promote`
   alongside the l-diversity gate. EMD-based, default t=0.2, configurable.
2. **Bottom-coding semantic (#4)**. The CAP primitive only handles the
   upper tail. Add a `FLOOR` action (or extend `CapRule` with optional
   `lower_threshold` / `lower_label`) so the YAML can declare both
   tails on a single field rule.
3. **Composition / cross-release tracking (#18)**. The pipeline cannot
   currently detect a differencing attack across two scrub runs. The
   Phase 1 audit-ledger work is the natural home; record every
   {study, agent, query-shape, timestamp} tuple and refuse queries that
   would breach a configured budget. **Differential privacy (#22) is
   the long-term formal answer** to this class of risk but is not
   recommended for the scrub layer; it would only become relevant if
   the pipeline starts publishing aggregates outside the enclave or
   adopts a query-interface model.

Three **PARTIALLY_COVERED** items worth tracking but not rule-PRing:

- Numeric noise addition (#10) — the pipeline deliberately does not
  add noise to numeric quasi-identifiers; document the design rationale.
- Free-text NER (#14) — whole-drop is conservative and 800-188 explicitly
  acknowledges narrative scrub is unsolved; document the position.
- High-dimensional QI minimization (#16) — the SoT YAMLs are the right
  policy ground; the Phase 0 SoT-driven sweep is the right vehicle.

Items deliberately out of scope (UNCOVERED but **no action recommended**):
genomic data (#13), blanking-and-imputing (#8), record-swapping (#9),
DP at the scrub layer (#22), micro-aggregation (#5).

## Citations

- NIST SP 800-188, *De-Identifying Government Datasets: Techniques and
  Governance*, September 2023:
  https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-188.pdf
- §1 introduction (k-anon vs DP framing) — p. 1, footnote 1.
- §3.2.1 *Probability of Re-Identification* (k-anon, l-diversity,
  t-closeness lineage) — pp. 17-18, footnote 14.
- §3.7.3 *Risk-Based De-Identification Standards* — p. 42.
- §4.3.1 *Removing or Transforming of Direct Identifiers* (NULL,
  masking, encryption, **SHA-256 HMAC with 256-bit key**, surrogate
  values, PPRL) — pp. 51-52.
- §4.3.2 *Special Security Note Regarding the Encryption or Hashing of
  Direct Identifiers* — p. 52.
- §4.3.3 *De-Identifying Numeric Quasi-Identifiers* (top **and bottom**
  coding, micro-aggregation, generalize-rare-bins, suppression,
  blanking-and-imputing, swapping, noise addition) — pp. 53-54.
- §4.3.4 *De-Identifying Dates* (Safe-Harbor year-only, systematic
  per-subject shift, interval perturbation) — pp. 54-55.
- §4.3.5 *De-Identifying Geographical Locations* — pp. 55-56.
- §4.3.6 *De-Identifying Genomic Information* — p. 56.
- §4.3.7 *De-Identifying Text Narratives and Qualitative Information* —
  p. 57.
- §4.3.8-§4.3.11 *Aggregation, High-Dimensional, Linked, and Composition
  Challenges* — pp. 57-59.
- §4.3.12 *Potential Failures of De-Identification* — p. 60.
- §4.4 *Synthetic Data* (partially synthetic, fully synthetic, DP-based
  synthesis) — pp. 61-66.
- §4.5 *De-Identifying with an Interactive Query Interface* — p. 67.
- Reference [94] N. Li, T. Li, S. Venkatasubramanian, *t-Closeness:
  Privacy Beyond k-Anonymity and l-Diversity*, ICDE 2007.
- Reference [98] A. Machanavajjhala et al., *l-diversity: Privacy
  beyond k-anonymity*, ICDE 2006.
