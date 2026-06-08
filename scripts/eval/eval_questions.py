"""Gold-table fixture: EVAL_QUESTIONS.

A single shared list consumed by both the offline runner
(scripts.eval.retrieval_eval) and the CI test suite
(tests.eval.test_retrieval_eval).  Each entry is an EvalQuestion
dataclass.

All source_files are relative to config.STUDY_LLM_SOURCE_DIR
(i.e. output/{STUDY}/llm_source/).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalQuestion:
    """One entry in the evaluation gold table."""

    id: str
    question: str
    kind: str  # 'statistical' | 'definitional'
    expected_primary_tool: str
    source_files: list[str]
    backing_columns: list[str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Gold table — encode ALL 10-11 questions from the evaluation matrix
# ---------------------------------------------------------------------------

EVAL_QUESTIONS: list[EvalQuestion] = [
    # ------------------------------------------------------------------
    # STATISTICAL questions (Cohort A)
    # ------------------------------------------------------------------
    EvalQuestion(
        id="Q-A1",
        question=(
            "Cohort A univariate analysis: TB recurrence by malnutrition, diabetes, "
            "alcohol, smoking, age, and sex.  Provide p-values and violin/scatter plots."
        ),
        kind="statistical",
        expected_primary_tool="run_python_analysis",
        source_files=[
            "dataset_schema/files/1A_ICScreening.jsonl",
            "dataset_schema/files/2A_ICBaseline.jsonl",
            "dataset_schema/files/98A_FOA.jsonl",
            "study_metadata/study_variable_map.yaml",
        ],
        backing_columns=[
            "IS_AGE",
            "IS_SEX",
            "IC_DMDX",
            "IC_ALCFRQ",
            "IC_SMOKHX",
            "IC_WEIGHT",
            "IC_HEIGHT",
            "IC_KNEEHT",
            "FOA_COHAOUT",
            "SUBJID",
        ],
        notes=(
            "Malnutrition is DERIVED: BMI < 18.5 from IC_WEIGHT/IC_HEIGHT with "
            "Chumlea knee-height fallback (IC_KNEEHT).  Outcome FOA_COHAOUT "
            "aggregated as worst_per_subject."
        ),
    ),
    EvalQuestion(
        id="Q-A2",
        question=(
            "Cohort A multivariate additive logistic regression with backward "
            "selection on the same 6 predictors (malnutrition, diabetes, alcohol, "
            "smoking, age, sex)."
        ),
        kind="statistical",
        expected_primary_tool="run_python_analysis",
        source_files=[
            "dataset_schema/files/1A_ICScreening.jsonl",
            "dataset_schema/files/2A_ICBaseline.jsonl",
            "dataset_schema/files/98A_FOA.jsonl",
            "study_metadata/study_variable_map.yaml",
        ],
        backing_columns=[
            "IS_AGE",
            "IS_SEX",
            "IC_DMDX",
            "IC_ALCFRQ",
            "IC_SMOKHX",
            "IC_WEIGHT",
            "IC_HEIGHT",
            "IC_KNEEHT",
            "FOA_COHAOUT",
            "SUBJID",
        ],
        notes="Backward selection via p-value threshold or AIC on the same 6 predictors.",
    ),
    EvalQuestion(
        id="Q-A3",
        question=(
            "Cohort A interaction analysis: recurrence ~ predictor x age and "
            "recurrence ~ predictor x sex; plots colored by age/sex."
        ),
        kind="statistical",
        expected_primary_tool="run_python_analysis",
        source_files=[
            "dataset_schema/files/1A_ICScreening.jsonl",
            "dataset_schema/files/2A_ICBaseline.jsonl",
            "dataset_schema/files/98A_FOA.jsonl",
            "study_metadata/study_variable_map.yaml",
        ],
        backing_columns=[
            "IS_AGE",
            "IS_SEX",
            "IC_DMDX",
            "IC_ALCFRQ",
            "IC_SMOKHX",
            "IC_WEIGHT",
            "IC_HEIGHT",
            "IC_KNEEHT",
            "FOA_COHAOUT",
            "SUBJID",
        ],
        notes="Interaction terms: predictor x IS_AGE and predictor x IS_SEX.",
    ),
    EvalQuestion(
        id="Q-B1",
        question=(
            "Cohort B (household contacts) univariate and multivariate analysis: "
            "incident TB by malnutrition, diabetes, alcohol, smoking, age, sex."
        ),
        kind="statistical",
        expected_primary_tool="run_python_analysis",
        source_files=[
            "dataset_schema/files/1B_HCScreening.jsonl",
            "dataset_schema/files/2B_HCBaseline.jsonl",
            "dataset_schema/files/98B_FOB.jsonl",
            "dataset_schema/files/12B_FUB.jsonl",
            "study_metadata/study_variable_map.yaml",
        ],
        backing_columns=[
            "HHC_AGE",
            "HHC_SEX",
            "HC_DMDX",
            "HC_ALCFRQ",
            "HC_SMOKHX",
            "HC_WEIGHT",
            "HC_HEIGHT",
            "HC_KNEEHT",
            "FOB_COHBOUT",
            "FUB_TBDIAG",
            "SUBJID",
        ],
        notes=(
            "Cohort B outcome: FOB_COHBOUT (any_positive_per_subject) AND "
            "FUB_TBDIAG==1 from 12B_FUB."
        ),
    ),
    # ------------------------------------------------------------------
    # DEFINITIONAL questions
    # ------------------------------------------------------------------
    EvalQuestion(
        id="Q-D1",
        question="What is the overall distribution of HIV test results in this study?",
        kind="definitional",
        expected_primary_tool="query_dataset",
        source_files=[
            "dataset_schema/files/6_HIV.jsonl",
            "dictionary_mapping/jsonl/Codelists/Codelists_table_3.jsonl",
        ],
        backing_columns=["HIV_HIV", "SUBJID"],
        notes=(
            "HIV_HIV is mostly queryable/statistical; formal coded-value legend "
            "lives in Codelists_table_3.  Treated as definitional per the matrix "
            "but the distribution itself is a computable statistic."
        ),
    ),
    EvalQuestion(
        id="Q-D2",
        question=(
            "What are the inclusion and exclusion criteria for index-case Cohort A enrollment?"
        ),
        kind="definitional",
        expected_primary_tool="answer_catalog_question",
        source_files=[
            "SoT/1A_ICScreening/joined/1A_ICScreening_joined_query_view.yaml",
            "dataset_schema/files/17_EligConfirmation.jsonl",
        ],
        backing_columns=[
            "IS_ELIGIBLE",
            "IS_ENROLL",
            "EC_ELIGCONF",
            "SUBJID",
        ],
        notes=(
            "Eligibility defined by questions 3-8 YES and 9-15 NO per SoT "
            "1A_ICScreening joined view.  17_EligConfirmation provides confirmatory "
            "records."
        ),
    ),
    EvalQuestion(
        id="Q-D3",
        question=(
            "How does this study distinguish TB relapse from treatment failure in "
            "Cohort A outcomes?"
        ),
        kind="definitional",
        expected_primary_tool="answer_catalog_question",
        source_files=[
            "dataset_schema/files/98A_FOA.jsonl",
            "SoT/98A_FOA/joined/98A_FOA_joined_query_view.yaml",
        ],
        backing_columns=["FOA_COHAOUT", "FOA_RNTCDCSP", "SUBJID"],
        notes=(
            "FOA_COHAOUT categories encode relapse/failure; formal discriminating "
            "definition defers to the Common Protocol (not in bundle).  "
            "FOA_RNTCDCSP captures TB programme classification specifics."
        ),
    ),
    EvalQuestion(
        id="Q-D4",
        question=(
            "How is a household contact defined for Cohort B — dwelling sharing "
            "vs pot/meal sharing?"
        ),
        kind="definitional",
        expected_primary_tool="answer_catalog_question",
        source_files=[
            "dataset_schema/files/53_exposure.jsonl",
            "dataset_schema/files/2B_HCBaseline.jsonl",
            "dataset_schema/files/1B_HCScreening.jsonl",
        ],
        backing_columns=[
            "HC_MEAL",
            "HC_SLEEPHX",
            "HC_LIVEDY",
            "HC_LIVEDM",
            "HC_EXPDAY",
            "HC_EXPH",
            "HC_RELATN",
            "SUBJID",
        ],
        notes=(
            "53_exposure captures dwelling/meal-sharing proximity; 2B_HCBaseline "
            "has HC_MEAL, HC_SLEEPHX, HC_LIVEDY/M, HC_EXPDAY/H, HC_RELATN."
        ),
    ),
    EvalQuestion(
        id="Q-D5",
        question=(
            "What DST tests are performed on culture-positive specimens and what is "
            "their timing relative to specimen collection?"
        ),
        kind="definitional",
        expected_primary_tool="query_dataset",
        source_files=[
            "dataset_schema/files/7_Culture.jsonl",
            "dataset_schema/files/21_DSTIsolate.jsonl",
        ],
        backing_columns=[
            "CX_DSTINH",
            "CX_DSTRMP",
            "CX_DSTEMB",
            "CX_DSTSM",
            "CX_DSTPZA",
            "CX_DSTKMY",
            "CX_DSTOFX",
            "CX_DSTETHIO",
            "CX_DSTDAT",
            "DST_DSTINH",
            "DST_DSTRMP",
            "DST_DSTDAT",
            "SUBJID",
        ],
        notes=(
            "7_Culture has the primary DST panel (CX_DST*) with CX_DSTDAT; "
            "21_DSTIsolate has an extended panel (DST_*) with DST_DSTDAT."
        ),
    ),
    EvalQuestion(
        id="Q-D6",
        question=(
            "What is the follow-up schedule for household contacts and which "
            "specimens are collected at each visit?"
        ),
        kind="definitional",
        expected_primary_tool="answer_catalog_question",
        source_files=[
            "dataset_schema/files/12B_FUB.jsonl",
            "dataset_schema/files/3_Specimen_Collection.jsonl",
        ],
        backing_columns=[
            "FUB_VISIT",
            "FUB_VISDAT",
            "FUB_TBDIAG",
            "SC_ICVST",
            "SC_HHCVST",
            "SC_BLDDAT",
            "SUBJID",
        ],
        notes=(
            "12B_FUB tracks visit number (FUB_VISIT) and date (FUB_VISDAT); "
            "3_Specimen_Collection links visit flags (SC_ICVST, SC_HHCVST) to "
            "sample collection dates."
        ),
    ),
]
