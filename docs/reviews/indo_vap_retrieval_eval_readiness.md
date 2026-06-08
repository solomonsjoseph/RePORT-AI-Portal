# Indo-VAP — Retrieval evaluation readiness (2026-06-08)

Review against Evan's 10-question evaluation set. All findings are
**metadata-grounded** (schema JSONL, SoT policy YAML, joined views,
study_variable_map.yaml). No raw dataset cell values were read.

---

## 1. Retrieval feasibility summary

### Statistical questions — 4/4 FULLY FEASIBLE

Every required column is published under `output/Indo-VAP/llm_source/` and
the concept-to-column binding is captured in
`study_metadata/study_variable_map.yaml`.

| Question | Outcome column | Predictor forms | Schema JSONL present |
|----------|---------------|-----------------|----------------------|
| Q-A1 Cohort A univariate | `FOA_COHAOUT` (98A_FOA) | 1A_ICScreening, 2A_ICBaseline | Yes — all 4 forms |
| Q-A2 Cohort A multivariate | same | same | Yes |
| Q-A3 Cohort A interactions (age/sex) | same | same | Yes |
| Q-B1 Cohort B univariate | `FOB_COHBOUT` (98B_FOB) + `FUB_TBDIAG` (12B_FUB) | 1B_HCScreening, 2B_HCBaseline | Yes — all 4 forms |

SoT policy YAMLs and joined query views are published for all four outcome
and predictor forms (`SoT/98A_FOA/`, `SoT/98B_FOB/`, `SoT/1A_ICScreening/`,
`SoT/2A_ICBaseline/`, `SoT/1B_HCScreening/`, `SoT/2B_HCBaseline/`,
`SoT/12B_FUB/`).

### Definitional questions — 6/6 FORM-TEXT GROUNDED, PARTIAL

The joined views capture printed PDF question text and coded response
options for every form referenced. What they cannot provide is the formal
narrative from the study's external **Common Protocol / Outcome Measures**
document (see Section 2).

| Question | Primary form(s) | SoT published | Limiting gap |
|----------|----------------|---------------|--------------|
| Q-D1 HIV result distribution | 6_HIV | Yes | Mostly queryable; value labels via `dictionary_mapping/jsonl/Codelists/Codelists_table_3.jsonl` |
| Q-D2 Index-case inclusion/exclusion | 1A_ICScreening, 17_EligConfirmation | Yes (both) | Exhaustive eligibility prose defers to Common Protocol |
| Q-D3 TB relapse vs treatment failure | 98A_FOA | Yes | Clinical discriminating criteria (relapse vs failure) are not in the PDF question text |
| Q-D4 Household contact definition | 53_exposure, 2B_HCBaseline, 1B_HCScreening | **53_exposure SoT absent** | Dwelling-vs-pot threshold definition defers to Common Protocol; 53_exposure has no published SoT (dataset schema JSONL is present) |
| Q-D5 DST tests + timing | 7_Culture, 21_DSTIsolate | 7_Culture Yes; **21_DSTIsolate SoT absent** | Prescribed DST panel + timing cadence defers to Common Protocol |
| Q-D6 Follow-up schedule + specimens | 12B_FUB, 3_Specimen_Collection | Yes (both) | Prescribed visit cadence defers to Common Protocol |

---

## 2. Data gap that caps definitional accuracy

The formal narrative definitions for the following concepts live in an
external **Common Protocol / Outcome Measures** document that is NOT
published under `llm_source/`:

- **TB relapse vs treatment failure** — the clinical discriminating criteria
  (time post-treatment, culture status at defined week, WHO classification).
  `98A_FOA` captures `FOA_RNTCDCSP` coded options and `FOA_COHAOUT`
  category labels only.
- **Exhaustive index-case inclusion / exclusion** — `1A_ICScreening` SoT
  (Q3–15, `IS_ELIGIBLE` rule) provides the screening-form question text and
  the skip-logic rule; the full eligibility narrative (TB definition, smear
  criteria, age floor rationale) lives in the Protocol.
- **Household contact threshold** — `53_exposure` and `2B_HCBaseline` record
  the contact-exposure variables (`HC_MEAL`, `HC_SLEEPHX`, `HC_LIVEDY`,
  `HC_LIVEDM`, `HC_EXPDAY`, `HC_EXPH`); the Protocol defines the
  dwelling-vs-shared-pot distinction.
- **DST panel and follow-up cadence** — `7_Culture` and `12B_FUB` capture
  the specimen variables; the Protocol prescribes which tests are mandatory
  at which visits.

**Consequence:** `cite_source` and `search_llm_source` can ground
definitional answers to the printed question text and coded response
options, but cannot provide the authoritative Protocol prose. The agent
will correctly answer "the form records X; the discriminating definition
is in the Common Protocol" — it cannot reproduce the Protocol definition
verbatim.

**Impact / recommendation:** Publish the relevant Outcome Measures or
Methods section of the Common Protocol as a plain-text or PDF artifact
under `llm_source/` (e.g.
`llm_source/study_metadata/common_protocol_outcome_measures.txt`). This is
a **data-publishing action by the data owner**, not a code defect. Once
published, `search_llm_source` and `cite_source` will surface the
Protocol text verbatim for definitional questions.

Two additional SoT gaps reduce grounding for Q-D4 and Q-D5:
`53_exposure` and `21_DSTIsolate` have dataset schema JSONL but no
published SoT policy YAML. Running `make sot-generate-all` for these two
forms would fill that gap independently of the Protocol gap.

---

## 3. Analysis-correctness requirements for statistical questions

These are **method dependencies** the agent code must satisfy, not
retrieval gaps. All are specified in
`study_metadata/study_variable_map.yaml`, which `agent_prompts.py` (lines
127–129) instructs the agent to read first via `read_llm_source_file`.

- **Malnutrition / BMI is DERIVED** — no stored BMI column exists. The
  agent must compute BMI from `IC_WEIGHT` / `IC_HEIGHT` (2A_ICBaseline),
  falling back to Chumlea knee-height (`IC_KNEEHT`) when height is missing,
  and classify BMI < 18.5 as malnourished. The derivation formula and
  fallback logic are in `study_variable_map.yaml`.
- **Outcome aggregation** — `FOA_COHAOUT` in 98A_FOA is a per-visit column;
  the agent must aggregate to worst_per_subject (positive labels listed in
  the map) before logistic regression. Similarly, `FOB_COHBOUT` in 98B_FOB
  requires any_positive_per_subject, and `FUB_TBDIAG == 1` in 12B_FUB is
  an additional incident-TB flag that must be OR-combined with
  `FOB_COHBOUT`.
- **HIV value labels** — `HIV_HIV` (6_HIV) uses integer codes; decode via
  `dictionary_mapping/jsonl/Codelists/Codelists_table_3.jsonl` before
  analysis or display.
- **SUBJID join** — both cohorts use `SUBJID` as the cross-form join key.
  All forms confirm `SUBJID` presence in their schema JSONL.

---

## 4. Doc-hygiene note (non-functional)

`study_metadata/study_variable_map.yaml` line 8 reads:

```
# Used by: scripts/ai_assistant/study_knowledge.py -> StudyKnowledge class
```

`scripts/ai_assistant/study_knowledge.py` does not exist and the
`StudyKnowledge` class has no implementation in the repo. The live consumer
is the system-prompt directive in `agent_prompts.py` (lines 127–129) plus
`read_llm_source_file`. This is a **comment-only doc-hygiene cleanup** — no
functional gap — but the stale reference may mislead a developer searching
for the consumer. Suggested fix: replace the comment with
`# Consumed via: agent_prompts.py system-prompt directive + read_llm_source_file`.
