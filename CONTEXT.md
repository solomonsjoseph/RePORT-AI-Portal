# Project Context

This document records terminology, source-boundary decisions, and architectural
boundaries for the RePORT AI Portal. It is the canonical project context referenced
by the engineering skills (`improve-codebase-architecture`, `diagnose`, `tdd`,
`triage`, `to-issues`, `to-prd`, `zoom-out`) per `docs/agents/domain.md`. Update
this file inline when domain terms or architectural decisions are resolved; do not
batch updates.

## Canonical Artifacts

### Study Variable Source of Truth

The Study Variable Source of Truth (SoT) is the project-specific canonical artifact where approved source evidence is combined, reconciled, and classified before downstream outputs are produced. It is built from authorized source evidence: dataset column names, associated PDF evidence, and dictionary metadata when available. It contains variable-level interpretation, source context, PHI/sensitivity classification, handling intent, dataset inclusion intent, catalog inclusion intent, and review state.

The canonical on-disk location is `data/<study>/SoT/<form>_policy.yaml` — one YAML per form, hand-authored, exhaustive, with PDF + dataset-column intent merged. The shorthand **SoT** is interchangeable with **Study Variable Source of Truth** in all later sections.

The Study Variable Source of Truth is not itself the final audit output, the LLM-facing metadata output, or the value-bearing analysis dataset. It is the governing artifact from which those outputs are derived. The earlier term **Study Source Policy** is retained only as a migration alias; the preferred term is **Study Variable Source of Truth**.


### Exhaustively Complete Study Variable Source of Truth

An Exhaustively Complete Study Variable Source of Truth has extracted and organized every useful piece of information available from the associated PDF, represented every dataset column name exactly once, and recorded any unresolved ambiguity explicitly instead of guessing. It does not require raw dataset row values.

### PHI Handling Ledger

The PHI Handling Ledger is the audit-facing derivative. It records which variables entered PHI/sensitive decisioning, what happened to each one, and how duplicates were handled. It must clearly account for dropped variables, pseudonymized variables, jittered dates, generalized fields, preserved fields with justification, review-required fields, exact duplicate drops, and duplicate candidates preserved because exact duplication was not proven. It excludes ordinary non-PHI clinical variables that were simply kept and never entered PHI/sensitive decisioning.

The ledger lives at `output/<study>/audit/phi_handling_ledger.{declared,as_written}.json` inside the [No-LLM Zone](#no-llm-zone). It is dual-half:

- **Declared ledger** — "the contract." Built at policy-load time from the SoT YAMLs. For every (form, variable_id) pair, it states what handling SHOULD happen (drop / pseudonymize / jitter_date / cap / generalize / suppress / keep) and which SoT rule said so. Exists before any row is processed. Used as the audit-facing promise.
- **As-written ledger** — "the receipt." Emitted live by `scripts/security/phi_scrub.py` and `scripts/extraction/dataset_cleanup.py` during the run. For every (form, variable_id, row) interaction, it records what handling actually fired and which rule matched. Counts only — never raw values.

**Reconciliation** is the pairwise comparison of declared vs. as-written. A passing reconciliation means the run honored the contract. Mismatches surface as gate findings — Phase 2A checks C/D/G plus the new Phase 3 cross-verifier.

#
## Concept-Level Study Questions

The user-facing system is expected to answer concept-level study and epidemiology questions, not just direct column-name questions. Common requests include study design, related variables for a clinical concept, inclusion and exclusion criteria, source definitions, household/contact definitions, specimen and test schedules, follow-up timelines, distributions, regression/modeling requests, plots, and cohort-specific research questions. The **Study Metadata Catalog** must therefore support concept discovery and variable mapping, while the current dataset remains the only source for analysis values.

## Catalog Concept Index

The **Study Metadata Catalog** should include a concept index in addition to compact variable records. A concept record groups related variables and source context under user-facing concepts such as TB relapse, TB recurrence, HIV test result, household contact, drug susceptibility testing, follow-up schedule, specimen collection, Cohort A eligibility, or comorbidity risk factors. Concept records provide concise definitions, related variable references, dataset availability, analysis queryability, and evidence-pack references. Full source wording, detailed criteria, schedules, and normalization traces remain in evidence packs and the **Study Variable Source of Truth**.

## Study Metadata Catalog

The Study Metadata Catalog is the LLM/tool-facing derivative and the replacement for the current variables reference. It exposes safe source metadata for variables that remain available after PHI handling and cleanup: variable meaning, PDF question text, options, sections, units, parent-child relationships, skip logic, source associations, and dictionary-derived metadata when available. It must not expose raw participant values or raw PHI. PDF-only or dictionary-only information can be preserved as context, but first-class catalog variables are the variables that exist in the PHI-handled dataset.

The catalog lives at `output/<study>/llm_source/study_metadata_catalog.json` and is a **lean table-of-contents (ToC)** under the [Lean-Catalog Principle](#lean-catalog-principle). It carries pointers and minimal index keys only — never full per-form payloads. The full per-form metadata payload lives in the per-form [Evidence Pack](#evidence-pack-per-form). This lean ToC replaces the legacy 1.4 MB mega-catalog and replaces "PDF extraction" as the user-facing concept.


### Lean-Catalog Principle

Every catalog file inside `llm_source/` is a lean table-of-contents only: it lists pointers, names, form, file paths, handling-status flags, and evidence-pack references — never full per-form or per-variable payloads. Per-form payloads always live in sibling per-form files. This principle applies to:

- `llm_source/study_metadata_catalog.json` — points at `evidence_packs/<form>.json`.
- `llm_source/dataset_schema/catalog.json` — points at `dataset_schema/files/<form>.jsonl`.
- `llm_source/dictionary/catalog.json` — points at `dictionary/<form>.json`.

The principle does **not** apply to `llm_source/data_dictionary.json`, which keeps its existing `main`-branch shape and content unchanged. The dictionary's per-form payloads relocate from `trio_bundle/dictionary/<form>.json` to `llm_source/dictionary/<form>.json`; only the path moves.

### Evidence Pack (per-form)

Each form has exactly one evidence pack at `output/<study>/llm_source/evidence_packs/<form>.json`. The per-form pack holds the complete PDF metadata for that form — variable descriptions, options, codings — sourced from the SoT YAML and the manual PDF + dataset-column extraction. It carries no row values.

This per-form layout replaces the legacy 949 per-variable evidence pack files. A migration script translates legacy per-variable evidence packs into the per-form shape and writes a deletion manifest for the legacy files; the deletions execute under the Phase 5 clean slate. Per spec 2026-05-07-llm-source-restructure-design; implementation tracked in PHI_handing_review branch.

### Dataset Schema

The **Dataset Schema** is the folder `output/<study>/llm_source/dataset_schema/`. It contains:

- `catalog.json` — lean ToC keyed by form, recording `{form, file, sot_yaml, handling_summary}` and a pointer into `evidence_packs/<form>.json`. Never inlines per-row or per-variable payloads.
- `files/<form>.jsonl` — PHI-cleaned rows, one JSONL per form. The pipeline writes these atomically; this is the active dataset target after the Phase 2 cutover.

The legacy write target `trio_bundle/datasets/<form>.jsonl` is retired; the new path becomes the only canonical write target.

### PHI-Handled Dataset

The PHI-Handled Dataset is the value-bearing dataset available for analysis after PHI handling, duplicate handling, cleanup, and runtime validation. It contains participant-level values only after raw PHI has been dropped, pseudonymized, jittered, generalized, or otherwise handled according to the Study Variable Source of Truth. It is separate from the Study Metadata Catalog: the catalog explains variables, while the PHI-Handled Dataset contains the analysis values. Dropped variables are not present in this dataset and are accounted for only in the PHI Handling Ledger.

### Trio Bundle Deprecation

The **trio bundle** is fully retired. Per the [design spec](docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md), the end-state per study under `output/<study>/` is exactly two folders: `llm_source/` (canonical LLM-facing) and `audit/` (no-LLM zone). All other intermediate folders — `trio_bundle/`, `staging/`, `agent/`, `human_review/` — are deleted in Phase 5.

The target user-facing outputs gathered under `llm_source/` are the **Study Metadata Catalog** (lean ToC), per-form **Evidence Packs**, the **Dataset Schema** (lean ToC + per-form JSONL files), the **Data Dictionary** (shape unchanged from `main`, payload relocated from `trio_bundle/dictionary/<form>.json` to `llm_source/dictionary/<form>.json`, with a new lean `llm_source/dictionary/catalog.json` ToC), and the concept index. Per spec 2026-05-07-llm-source-restructure-design; implementation tracked in PHI_handing_review branch.

## Derivation Model

```text
Hand-curated Study Variable Source of Truth (per form, in data/{study}/)
plus dataset column inventory (runtime input for schema only)
        ↓
PHI Handling Ledger          Study Metadata Catalog      PHI-Handled Dataset
(written at mutation site)   (LLM/tool calls)            (analysis values)
                             Evidence Packs              + PHI-Handled
                             Dataset Schema                Dataset Schema
                             Data Dictionary
                             — all under LLM Source folder
```

## Source Boundary

Raw dataset files may be accessed only for column/header names. Raw dataset row values must not be read, sampled, counted, summarized, or inspected. Associated PDFs may be used for visible form metadata and annotations. Dictionary metadata is authorized for the Study Variable Source of Truth and must be provenance-tagged separately from dataset and PDF evidence.
## Relationships

- A **Study Variable Source of Truth** is complete when it is an **Exhaustively Complete Study Variable Source of Truth** for the authorized sources.
- A **PHI Handling Ledger** is derived from a **Study Variable Source of Truth** and processing results.
- A **Study Metadata Catalog** is derived from a **Study Variable Source of Truth** and is consumed by LLM/tool calls.
- A **PHI-Handled Dataset** is derived from dataset extraction plus Study Variable Source of Truth handling rules and is consumed for analysis values.
## Flagged Ambiguities

- "100% complete" means exhaustive extraction and organization from authorized sources, not certainty about raw value shape. Raw dataset row values remain outside the evidence boundary.

## Dictionary Evidence

Dictionary metadata belongs in the **Study Variable Source of Truth** when available. It may provide variable definitions, data types, codelists, core status, and related structured metadata. Dictionary evidence must be provenance-tagged separately from dataset and PDF evidence so downstream outputs can explain whether a fact came from a dataset column, PDF text/annotation/option, or dictionary entry.

## Study Catalog Boundary

(Renamed from "PDF Extraction Boundary" per the [design spec](docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md).)

Extract all useful PDF content into the **Study Variable Source of Truth** YAMLs except footers, form version dates, PDF creation dates, print/export timestamps, and any text whose only purpose is to state when the PDF/form artifact was created. Everything else remains eligible for extraction until the human reviewer explicitly removes it.

There is no separate "PDF extraction step" downstream. PDF metadata is captured exhaustively in SoT YAMLs at authoring time, then exported to per-form **Evidence Packs** at build time. The lean **Study Metadata Catalog** plus the per-form Evidence Packs together replace the legacy PDF-extraction folder and the legacy 1.4 MB mega-catalog as the user-facing study-catalog concept. Raw PDFs continue to be preserved under `data/raw/<study>/` for audit; only the LLM-facing PDF-extraction folder is retired.
## Evidence Context Areas

- **Dataset Context** records evidence available from dataset sheet/file headers only: dataset file name, sheet/header structure when available, column order, column names, duplicate-looking column names, and column-name-only hints. Dataset row values remain outside the evidence boundary.
- **PDF Context** records evidence available from the associated PDF: form title, sections, instructions, question text, options, skip logic, annotations, units, and non-variable explanatory text, excluding footers and creation/version-date artifacts.
- A field found in both dataset headers and PDF evidence is a normal variable record with both evidence sources, not `context_only`.
- `context_only: true` is reserved for useful PDF or dataset context that does not represent a dataset variable.
- Dictionary workbooks are not dataset evidence for this pilot and remain excluded unless explicitly re-authorized.
## Source Text Preservation

The **Study Variable Source of Truth** preserves exact PDF wording in source-text fields and adds structured normalized metadata separately. Normalized labels, meanings, options, and notes must not replace the only copy of the source wording.


## Source Text And Normalization Trace

The **Study Variable Source of Truth** stores exact source wording separately from normalized catalog metadata. Exact PDF wording, dictionary wording, annotations, option text, and provenance belong in audit evidence fields. Normalized labels, meanings, sections, data types, option sets, and relationships belong in catalog metadata fields. The source of truth also records a normalization trace explaining how exact source evidence became the normalized metadata used by the **Study Metadata Catalog**. The catalog exposes the safe normalized fields by default; exact source wording remains available for audit and verification without being dumped into ordinary LLM-facing metadata.




## Observed Values Boundary

Observed dataset values, observed counts, and value distributions stay out of the **Study Metadata Catalog**. The catalog represents source metadata and source-defined possible values. Questions about observed values are answered by querying the current dataset through analysis tools. If observed-value summaries become useful later, they should be generated as a separate post-handling **Dataset Profile Summary** with privacy thresholds and freshness metadata, not embedded in the catalog.

## Source-Defined Versus Observed Values

Catalog options must explicitly distinguish source-defined possible values from observed dataset values. Source-defined options come from PDFs, annotations, or dictionary metadata and describe what the source allows. They do not prove that those values occur in the current dataset. Observed value counts or presence must come only from analysis of the current dataset through authorized runtime tools. The compact catalog may include `options.source_defined`, but should mark observed values as unavailable in the catalog.

## Compact Catalog Options

Compact catalog records include normalized source-defined option labels and codes when available. This supports common metadata and analysis-resolution questions without forcing evidence-pack retrieval. Exact PDF wording, complex annotations, and provenance for options remain in the evidence pack and **Study Variable Source of Truth**. Option labels and codes in the compact catalog describe source-defined meanings, not observed participant values.

## Source-Defined Options

The **Study Variable Source of Truth** preserves source-defined options and codelist values from PDFs and dictionary metadata even when those values are not known to occur in the current dataset. These options describe possible source meanings, not observed participant values. Observed value profiling is not part of source-of-truth generation because raw dataset row values remain outside the source extraction boundary unless an explicitly PHI-handled runtime process reports safe aggregate evidence.

## Option Value Evidence

Options preserve visible labels. Stored values/codes are recorded only when the PDF clearly shows them or source annotations provide them. If only labels are available, the option value is recorded as unknown rather than inferred. Other/specify/reason options should link to child variables when the PDF or column naming supports the relationship.
## Source Presence Model

Every variable-like item records source presence explicitly: dataset `present|absent`, PDF `present|absent`, and dictionary `not_used` unless the dictionary is explicitly re-authorized.

Record types distinguish evidence shape:

- `variable`: dataset column, with or without PDF evidence.
- `pdf_context`: useful PDF content that is not a dataset variable.
- `unmatched_pdf_field`: PDF variable-like field absent from dataset columns.
- `unmatched_dataset_column`: dataset column absent from PDF evidence.

A dataset/PDF match becomes a normal variable record with both evidence sources. Dataset-only columns stay in variables with `evidence_level: column_name_only` and conservative review status when meaning is uncertain. PDF-only variable-like fields stay in PDF context with `dataset_presence: absent`. Clean-output presence is tracked separately because a source column can exist but be dropped, pseudonymized, jittered, or excluded from the clean output.





## Ledger Access Boundary

The **PHI Handling Ledger** is produced for direct inspection in the audit folder. It is not part of normal LLM/tool retrieval for end-user study questions. User-facing chat should not query ledger contents directly. If a user asks about a dropped or unavailable variable, the assistant gives a polite non-availability response and directs the user to the maintainer. Audit/IRB users inspect the ledger artifacts outside the chat retrieval path.

## Ledger Evidence References

The **PHI Handling Ledger** does not copy full exact source wording or normalization traces. It records handling action, runtime outcome, duplicate outcome, counts where appropriate, and references back to the relevant **Study Variable Source of Truth** evidence. This keeps the ledger focused on audit handling while preventing divergence between copied source text and the canonical source-of-truth record.

## Ledger Inclusion Rule

The **PHI Handling Ledger** includes every variable that entered PHI/sensitive decisioning, not only variables that were transformed. A variable enters ledger scope when it is dropped, pseudonymized, date-jittered, generalized, marked review-required for PHI/sensitive reasons, preserved despite PHI-like naming with a justification, or considered as a duplicate candidate. Ordinary non-PHI clinical variables that were simply retained do not appear in the ledger.

## Retained PHI-Handled Variables

Variables that are PHI-sensitive but retained after handling can appear in the **Study Metadata Catalog** and the **PHI-Handled Dataset** only when they are no longer in raw form. Examples include pseudonymized identifiers, jittered dates, generalized locations, and duplicate candidates preserved because exact duplication was not proven. Their audit details remain in the **PHI Handling Ledger**. Variables that are dropped during PHI handling appear only in the ledger.







## Search Terms And Variable Matching

The **Study Metadata Catalog** should include source-grounded `search_terms` when they improve retrieval: exact or normalized PDF phrases, option labels, dictionary labels, section terms, abbreviations visible in the source, and normalized labels. It should not try to store every possible LLM-generated synonym. Runtime LLM/fuzzy expansion is allowed for natural-language matching, but generated terms are not treated as source evidence. If variable matching remains uncertain or multiple plausible variables match the user request, the assistant asks a clarification question before answering metadata or running analysis.


## Compact Catalog Relationships

Compact catalog records include a relationship summary and references, not full relationship evidence. The catalog is the key/index that identifies whether a variable has parent questions, child detail fields, other/specify links, skip logic, repeated-measure grouping, visit grouping, form section grouping, or shared option/codelist references. Full relationship details, confidence, basis, exact source wording, and provenance live in the evidence pack and **Study Variable Source of Truth**. The compact catalog opens the door to those details only when needed.

## Compact Catalog Record

Every **Study Metadata Catalog** variable uses a strict compact record shape so LLM/tool calls can retrieve metadata predictably and cheaply. The compact record includes `variable_name`, `display_label`, `normalized_meaning`, `search_terms`, `form`, `section`, `source_presence`, `catalog_tier`, `analysis_queryable`, `handling_status`, `analysis_use_guidance`, `options`, `relationships_summary`, `source_truth_ref`, and `evidence_pack_ref`. The compact record must be sufficient for common variable-understanding questions without loading full source evidence. Detailed exact wording and normalization evidence remain in the evidence pack.

## Catalog Evidence Packs

The **Study Metadata Catalog** is split into a compact default record and a heavier evidence pack. The compact record is optimized as the fast lookup key/index for LLM/tool retrieval: it identifies the variable, gives a concise normalized summary, and points to deeper records. The evidence pack contains exact source wording, PDF annotations, dictionary wording, detailed relationships, detailed provenance, and normalization trace. LLM/tool calls should fetch evidence packs lazily only when the user asks for more detail, provenance, exact source language, audit-style explanation, or when the compact catalog record is insufficient to answer accurately.

## Catalog Source References

Every **Study Metadata Catalog** variable carries lightweight references back to its **Study Variable Source of Truth** record and evidence pack. The catalog does not copy heavy evidence by default; it acts as the key that unlocks exact source wording, dictionary evidence, detailed relationships, provenance, and normalization trace only when needed.

## Catalog Variable Priority

The **Study Metadata Catalog** has two tiers of variables. Dataset-backed variables from the **PHI-Handled Dataset** are the first-priority, analysis-queryable variables. PDF-only or dictionary-only variable-like items may also be retained in the catalog as source-only or future-use variables because future datasets may use them, but they must be clearly marked as not present in the current PHI-Handled Dataset. Source-only variables provide study knowledge and form/dictionary context; they must not be presented to the LLM as currently queryable dataset values.



## PHI-Sensitive Source-Only Variables

PHI-sensitive variables found only in PDF or dictionary evidence remain in the **Study Variable Source of Truth** as source-only variables. They are marked `dataset_presence: absent`, `catalog_tier: source_only`, and `analysis_queryable: false`. Their PHI/sensitive category is still recorded for future-use awareness, but they do not create a runtime PHI Handling Ledger action for the current dataset because no current dataset value was handled. If a future dataset includes the variable, it becomes dataset-backed and enters runtime handling and ledger scope.


## Source-Only Catalog Records

Source-only variables may appear in the compact **Study Metadata Catalog** by default when they come from authorized PDF or dictionary evidence and contain no raw participant values. They must be marked `catalog_tier: source_only`, `dataset_presence: absent`, and `analysis_queryable: false`. This allows LLM/tool calls to answer study-design and form-metadata questions while preventing analysis tools from treating absent variables as current dataset columns.




## User-Facing Artifact Names

The catalog layer may define global `user_facing_names` so tools can use precise internal artifact names while chat responses use simpler wording. For example, the internal **PHI-Handled Dataset** may be rendered to users as the current dataset or dataset. This mapping belongs at catalog or system metadata level, not repeated on every variable record.

## User-Facing Dataset Wording

User-facing answers should usually refer to the **PHI-Handled Dataset** as the current dataset or dataset. Users can assume the available dataset has already been PHI-handled, so ordinary metadata and analysis responses should not repeatedly say PHI-handled. The canonical artifact name remains **PHI-Handled Dataset** in architecture and internal documentation, but chat responses should use simpler wording unless the user is asking about system design or handling boundaries.



## Notice Template Distinctions

Source-only or absent-from-current-dataset notices must be distinct from dropped/unavailable notices. A source-only notice explains that the field is available as study metadata but is not present in the current dataset, so it cannot be used for analysis. A dropped/unavailable notice states that the variable is not available in the current dataset and asks the user to contact the study/system maintainer for clarification. Dropped notices do not expose handling reasons, ledger details, or audit evidence in chat.

## Notice Rendering

User-facing notices are rendered as `Note:` messages, preferably as a separate final line after the direct answer. Do not use warning symbols for ordinary source/presence/queryability limitations. When multiple variables share the same limitation, collapse duplicate notices into one note per notice type so responses remain concise.

## Controlled Notice Types

User-facing notes use a controlled vocabulary so UI and tool responses remain consistent without sounding alarmist. Supported notice types include `source_only_not_analysis_queryable`, `absent_from_current_dataset`, `dropped_contact_maintainer`, `ambiguous_variable_requires_clarification`, and `mapping_only_not_observed_data`. Notice text should be short, user-facing, and should avoid audit details. Render these as `Note:` rather than with a warning symbol. For dropped variables, the note politely states that the variable is unavailable and tells the user to contact the study/system maintainer.

## Metadata Answer Disclosure

Metadata answers should not routinely expose `dataset_presence`, `catalog_tier`, or `analysis_queryable` when those details are not needed to answer the user. The assistant answers the direct metadata question first. Source or presence details are surfaced as a short user-facing note only when relevant to interpretation, such as when the variable is PDF-only, dictionary/mapping-only, source-only, absent from the current **PHI-Handled Dataset**, or when the user asks whether the variable can be analyzed. This keeps ordinary metadata answers concise while preventing source-only fields from being mistaken for dataset variables. In user-facing text, prefer current dataset or dataset over repeating PHI-handled dataset.

## Catalog Queryability

Every **Study Metadata Catalog** variable records whether it is currently analysis-queryable. Dataset-backed variables have `dataset_presence: present`, `catalog_tier: dataset_backed`, and `analysis_queryable: true`. Source-only variables have `dataset_presence: absent`, `catalog_tier: source_only`, and `analysis_queryable: false`. Source-only variables can answer metadata questions, but analysis tools must not treat them as available dataset values.


## Catalog Analysis Use Guidance

The compact **Study Metadata Catalog** includes analysis-use guidance so retained PHI-handled variables are interpreted safely. This guidance is not an access allowlist: the **PHI-Handled Dataset** is available to LLM-backed analysis tools. Pseudonymized identifiers may be described as suitable for linkage, grouping, or longitudinal joins but not as raw identity. Jittered dates may be described as suitable for relative timing, ordering, or trend analysis but not exact calendar-date claims. Generalized locations may be described as suitable for broad geographic/category analysis but not precise location inference. The catalog exposes this safe use guidance without including ledger counts, raw values, before/after values, or row-level audit evidence.

## Catalog Handling Status

The **Study Metadata Catalog** includes a concise safe handling status for retained PHI-handled variables so LLM/tool calls interpret them correctly. For example, pseudonymized identifiers, jittered dates, and generalized locations remain catalog-visible with handling status such as `pseudonymized`, `jittered_date`, or `generalized`. The catalog must not include audit details such as affected counts, row references, duplicate counts, raw values, or before/after values; those belong only in the **PHI Handling Ledger**.

## Catalog Exclusion Rule

Variables dropped during PHI handling or cleanup are absent from the **Study Metadata Catalog**. The catalog contains metadata for retained variables only. Dropped variables are accounted for exclusively in the **PHI Handling Ledger**, where the audit record explains why they were dropped and how they were handled.

## Handling Intent

Every dataset variable in the **Study Variable Source of Truth** has an explicit handling intent. The controlled action vocabulary is `keep`, `drop`, `pseudonymize`, `jitter_date`, `generalize`, and `review_required`. `keep` means the variable is intentionally expected to remain available after cleaning, not that it was overlooked. PHI-specific status and category are recorded separately from the handling action.

## Review And Exposure State

`review_required` separates review status from exposure safety. Each reviewed or unresolved variable records `review_state` and `exposure_state`. `review_state` indicates whether human interpretation is unresolved or resolved. `exposure_state` indicates whether the current representation is safe to expose. Unresolved unsafe variables are blocked from the **Study Metadata Catalog** and the **PHI-Handled Dataset** until reviewed. Unresolved variables that have already been safely handled can remain present, but they must be marked unresolved. Source-only unresolved variables may be metadata-searchable only when they contain no raw PHI and are clearly not analysis-queryable.

## Structured Review Required

`review_required` is a structured handling intent, not a vague fallback. It records a controlled reason, the exact uncertainty, and the reviewer question needed to resolve it. Common reasons include `possible_free_text`, `pdf_only_review`, `column_name_only`, `ambiguous_identifier`, `ambiguous_location`, `ambiguous_date`, `ambiguous_signature_or_initials`, `ambiguous_parent_child_relationship`, `ambiguous_option_values`, `unmatched_dataset_column`, and `unmatched_pdf_field`.
## Evidence Confidence

Every variable decision records an evidence level and basis. Evidence levels are `high`, `medium`, and `low`. `high` means dataset and PDF evidence directly agree or the PDF annotation directly names the variable. `medium` means the dataset column exists and nearby PDF context strongly supports the meaning without a direct annotation. `low` means column-name-only or ambiguous PDF-only evidence. Evidence basis records the source types used, such as dataset column, PDF annotation, PDF visible text, PDF option text, or derived column pattern.
## Derivation Targets

Each variable records whether it derives to the **PHI Handling Ledger**, the **Study Metadata Catalog**, and/or the **PHI-Handled Dataset**. PHI-risk handling, drops, pseudonymization, jittering, generalization, and duplicate decisions derive to the ledger. Safe metadata for retained variables derives to the catalog; dropped variables do not derive to the catalog. Retained value-bearing variables derive to the PHI-Handled Dataset after the configured PHI handling has been applied. Review-required items derive conservatively according to their risk and should not be exposed in the catalog as resolved metadata until reviewed.
## Duplicate Lifecycle

The **Study Variable Source of Truth** identifies duplicate candidates from source structure and naming, such as repeated subject identifier columns. It does not prove exact duplication from headers or PDF evidence alone. Duplicate candidates are verified during authorized extraction/cleaning runtime using actual values after PHI handling. The **PHI Handling Ledger** records the proven outcome: exact duplicate confirmed and dropped, or candidate preserved because exact duplication was not proven. Duplicate counts are reported under the canonical variable.

## Canonical Duplicate Accounting

When duplicate variables are proven to be exact duplicates after PHI handling, the **PHI Handling Ledger** records them under the canonical original variable rather than as unrelated top-level entries. The canonical entry records duplicate candidates, exact duplicates dropped with count and variable names, and duplicate candidates preserved because exact duplication was not proven. This keeps `SUBJID2`-style duplicate accounting attached to `SUBJID`.

## Dataset-Only Columns

Dataset-only columns remain variable records in the **Study Variable Source of Truth** with dataset `present`, PDF `absent`, and lower evidence confidence. PDF absence does not exempt a column from PHI handling. Strong PHI/date/signature/identifier patterns still receive the appropriate handling intent and are verified or applied by the authorized extraction/cleaning runtime. Ambiguous dataset-only columns remain structured `review_required`.
## Runtime Binding

The **Study Variable Source of Truth** records the expected runtime enforcement points for handling decisions. PHI handling is enforced by the extraction PHI scrub process, duplicate verification is enforced by deduplication after PHI handling, date jittering is enforced by the extraction date-jitter logic, and clean-output projection is enforced by dataset cleanup. Variable-level records may name the runtime components expected to enforce or verify the decision.
## PHI And Sensitive Categories

The **Study Variable Source of Truth** uses controlled PHI/sensitive categories: `subject_identifier`, `family_or_household_identifier`, `facility_or_site_identifier`, `operational_identifier`, `date`, `age_or_birthdate`, `signature_or_initials`, `location`, `free_text_or_specify`, `system_timestamp`, `not_phi`, and `unknown`. Default intents are pseudonymize for subject/family/facility/site/operational identifiers, jitter dates, drop signatures/initials and system timestamps unless explicitly safe, generalize or review locations, review possible free text, keep non-PHI, and review unknowns.


## Metadata Versus Analysis Boundary

Variable and metadata questions use the **Study Metadata Catalog**. Analysis uses the **PHI-Handled Dataset** as the value source. The catalog may help identify what a variable means, but it is not the analysis data source. Analysis tools must not answer value questions from catalog metadata or source evidence. They answer value questions only from the PHI-handled dataset and its technical schema.




## Analysis Answer Caveats

Analysis answers should not routinely mention PHI-handling caveats when the user requested answer can be provided directly from the **PHI-Handled Dataset**. The assistant should avoid exposing implementation or handling detail in ordinary analysis responses. If a handling constraint materially prevents or limits an answer, the assistant gives a brief non-availability or limitation response and directs the user to contact the study/system maintainer rather than exposing audit details.

## Ambiguous Analysis Requests

When an analysis request resolves to multiple plausible variables, the assistant asks a clarification question instead of guessing, unless one match is clearly dominant from the user wording and study context. Ambiguity is especially important for generic terms such as positive, result, date, visit, location, treatment, or status. Clarification happens before querying the **PHI-Handled Dataset** so analysis is not performed on the wrong variable.

## Analysis Variable Resolution

When a user asks an analysis question with natural language or an ambiguous variable reference, the system first uses the **Study Metadata Catalog** to resolve the requested concept to one or more candidate variables. The **PHI-Handled Dataset Schema** then confirms which matched variables are present and analysis-queryable in the current dataset. The actual calculation is performed only against the **PHI-Handled Dataset**. The catalog resolves meaning; the dataset supplies values.

## PHI-Handled Dataset Schema

The **PHI-Handled Dataset** should stay lean and value-bearing. Column-level interpretability belongs in a compact **PHI-Handled Dataset Schema** sidecar rather than being repeated inside each data row. The schema maps each retained dataset column to its **Study Metadata Catalog** reference, **Study Variable Source of Truth** reference, handling status, clean-output presence, and analysis queryability. This lets analysis tools bind values to the correct metadata without loading the full source-of-truth evidence.


## Query Routing

User questions route to the smallest sufficient capability. Metadata questions use the compact **Study Metadata Catalog** first. Analysis questions that require counts, distributions, filtering, comparisons, or statistics use the **PHI-Handled Dataset** through the dataset schema. Provenance, exact wording, source verification, or low-confidence answers fetch the relevant evidence pack or **Study Variable Source of Truth** record. PHI handling questions use the **PHI Handling Ledger**. General non-study conversation, greetings, simple arithmetic, date/time, and ordinary assistant questions can be answered directly without study tools, while gently encouraging the user to continue with study-related questions.



## Unavailable Analysis Variables

When an analysis request targets a source-only variable, the assistant explains that the variable exists in study metadata but is not present in the current **PHI-Handled Dataset**, so analysis cannot be performed. When an analysis or metadata request targets a dropped variable, the assistant politely denies availability, does not expose dropped-variable metadata through ordinary LLM paths, and tells the user to contact the study/system maintainer for access or clarification. Dropped-variable handling details remain available only in the generated **PHI Handling Ledger** files for direct authorized audit/IRB/maintainer review. The assistant must not silently substitute a similar variable unless the user confirms the substitution.

## Query Intent Vocabulary

Every user request is classified into a controlled query intent before study tools are selected. The supported intents are `general_conversation`, `study_metadata`, `study_analysis`, `source_provenance`, `phi_audit`, and `review_or_quality_check`. `general_conversation` answers without study retrieval. `study_metadata` uses the compact **Study Metadata Catalog**. `study_analysis` uses the **PHI-Handled Dataset** as the value source and its technical schema for column binding. `source_provenance` uses evidence packs or **Study Variable Source of Truth** records. `phi_audit` identifies that the request belongs to audit workflow; user-facing chat should direct the user to the generated audit folder or maintainer rather than querying the ledger directly. `review_or_quality_check` uses the source of truth, evidence packs, and validation reports.


## PHI Audit Intent

`phi_audit` is a detection-and-redirect intent, not a retrieval intent. When a user-facing chat request asks about PHI handling details, dropped-variable accounting, or audit evidence, the assistant recognizes the request as audit-related and directs the user to the generated audit folder or study/system maintainer. It does not fetch or summarize **PHI Handling Ledger** contents in ordinary chat retrieval.

## LLM Retrieval Order

LLM/tool calls should retrieve from the smallest accurate artifact first. The default path is: use the **Study Metadata Catalog** for variable meaning and source context, use the **PHI-Handled Dataset** as the value source with its technical schema for column binding, and fetch the full **Study Variable Source of Truth** only when the user asks for provenance, audit evidence, exact source wording, normalization trace, or when confidence is low. The **PHI Handling Ledger** is not an ordinary LLM retrieval artifact. It is generated for direct human/IRB/maintainer inspection from the audit folder, not queried by the user-facing chat flow.

## Policy Runtime Contract

The **Study Variable Source of Truth** states intended classification and handling. Runtime execution enforces, verifies, and reports what actually happened. Runtime must not silently override the Study Variable Source of Truth. Any mismatch between policy intent and runtime result is recorded as a policy-runtime mismatch audit finding.
## Study Variable Source of Truth Schema Direction

The pilot should move toward a stricter **Study Variable Source of Truth** schema with top-level form identity, dataset context, PDF context, variables, derivation targets, and validation. Each variable should consistently record name, source presence, record type, exact source text, normalized metadata, options, relationships, PHI/sensitive classification, handling intent, runtime binding, derivation targets, evidence level and basis, review state, and clean-output presence.

## Source Truth v2 Record Rules

The v2 **Study Variable Source of Truth** records explicit downstream derivation decisions for each source variable. A variable record should state whether it is included in the source truth, included or excluded from the **Study Metadata Catalog**, included, transformed, or excluded from the current dataset, included in the **PHI Handling Ledger**, and included in the **Dataset Cleanup Ledger**. These flags prevent downstream tools from inferring availability from a single action label.

Normalized labels, meanings, sections, and descriptions may be generated from source evidence, but must record a normalization basis such as column name, PDF section, PDF annotation, source-defined option set, or reviewer-approved interpretation. Normalized metadata must carry confidence and must not be presented as exact source wording. If exact PDF wording is not available in the extracted evidence, the source truth records an evidence gap instead of inventing wording.

When field-level section metadata conflicts with a more precise PDF section map, the PDF section map is the normalized section source. The older field-level section may be preserved only as draft evidence or provenance.

All form version labels, form version dates, footers, PDF creation dates, print/export timestamps, and artifact-version metadata are excluded from source-truth content. Stable form identity such as form number and form title may be retained when useful.

## Dataset Cleanup Ledger

The **Dataset Cleanup Ledger** is the audit-facing derivative for non-PHI dataset cleanup decisions. It accounts for duplicate handling, exact duplicate drops, duplicate candidates preserved because exact duplication was not proven, non-study system metadata drops, and other cleanup exclusions that are not PHI/sensitive handling decisions. It is separate from the **PHI Handling Ledger**.

The **PHI Handling Ledger** remains limited to PHI/sensitive decisioning: dropped PHI/sensitive variables, pseudonymized identifiers, jittered dates, generalized fields, preserved PHI-like variables with justification, and review-required PHI/sensitive variables. Duplicate handling belongs in the **Dataset Cleanup Ledger**, not the **PHI Handling Ledger**.

## System And Capture Metadata

Obvious system, capture, routing, image, batch, remote, original-file, verification, and timestamp columns are represented exactly once in the **Study Variable Source of Truth** for completeness, but are classified separately from study variables. Conservative name patterns such as `Batch*`, `Remote_*`, `Orig_*`, `Image_*`, `Route_*`, `Time_Stamp`, verification/workstation fields, and similar capture metadata default to non-study cleanup drops unless a reviewer marks them clinically or scientifically useful.

Sensitive-looking system metadata such as remote user, fax, phone, original file, or routing identifiers remains in the **Dataset Cleanup Ledger** with a sensitivity flag such as `possible_phi_or_operational_identifier`. It is not duplicated into the **PHI Handling Ledger** unless it is a real study variable that entered PHI/sensitive decisioning.

PDF-backed study fields default to clinically or scientifically useful unless PHI/sensitive handling, explicit system metadata classification, or human review says otherwise. Ambiguous PDF-backed study fields remain in the source truth with `review_required` and are blocked from catalog and analysis use until resolved.

## No-LLM Zone

`output/<study>/audit/` is a **no-LLM zone**: a filesystem path enforced unreadable to LLM agents and any retrieval module. The fix agent (see [Cross-Verifier](#cross-verifier)) and any other LLM-driven module is forbidden from reading anything inside this folder. Defense in depth:

- **Path-based deny** — `scripts/ai_assistant/` retrieval builders and `scripts/source_truth/retrieval.py` reject any path resolving inside `output/*/audit/`. A unit test asserts `os.path.realpath` cannot escape into the audit subtree.
- **Runtime guard** — `scripts/audit/ledger.py` and any audit emitter check the `REPORTAL_PROCESS_ROLE` environment marker. When set to `llm-agent`, audit-write paths refuse with `PermissionError`.
- **Custom `.gitattributes`** — `output/*/audit/**` is marked `report-ai-portal-no-llm=true`; retrieval modules read this attribute as one of the deny signals.
- **Sentinel file** — `output/<study>/audit/.NO_LLM_ZONE` is asserted on every audit-zone read attempt. Removal of the sentinel is itself an audited event.
- **Directory perm 0700.**

The PHI Handling Ledger, Dataset Cleanup Ledger, lineage manifest, and `phi_id_mapping.json` (variable_id → hashed token) all live inside this zone. Per spec 2026-05-07-llm-source-restructure-design; implementation tracked in PHI_handing_review branch.

## Intermediate Scratch Convention

Pipeline intermediates write to `tmp/<study>/<stage>/...`, never to `output/<study>/staging/`. The legacy `staging/` folder is a deprecated artifact retained only for the cutover window and is deleted in Phase 5; new code never recreates it. All `tmp/` writes are atomic so retries do not leave partial files. Cross-verify scratch, draft outputs, checksum staging, and SoT-gap drafts (`tmp/sot_gap_drafts/`) all live under `tmp/`.

## Cross-Verifier

The cross-verifier is a mid-pipeline check between the cleaned dataset and the SoT, running **after** `phi_scrub` and **before** `dataset_schema/files/` is finalized for a build. It is additive, accumulates findings rather than blocking, and has two components:

- **Component A — deterministic scanner.** Pure Python, no LLM. Walks `dataset_schema/files/<form>.jsonl` and emits a SAFE schema-only report containing counts and booleans only — never raw values, sample values, hashes of values, or value-derived statistics.
- **Component B — isolated fix agent.** LLM-driven, runs in a separate subprocess with OS-level read-deny on the row JSONL files (`output/<study>/trio_bundle/datasets/` and `output/<study>/llm_source/dataset_schema/files/`). Allowed reads are limited to the scanner's SAFE report, the SoT YAML, `scripts/security/phi_scrub.yaml`, and the per-form evidence pack (`evidence_packs/<form>.json`).

**Auto-fix policy.** Auto-fix opens a **PR** (never a direct commit). PR descriptions follow the same masking rules as HITL issues. Eligible auto-fixes: (a) add a `phi_scrub.yaml` rule only when the column persists in the scanner report AND the SoT declares `drop` or `pseudonymize`; (b) add a single-variable stub to the SoT YAML only when the variable record exists inside the form's evidence pack. **No-invent guards** prevent rule additions for variables absent from both SoT and the form's evidence pack, and SoT stubs cannot be added when the form's policy YAML is missing entirely (that is a Phase 0 gap, not a Phase 3 fix). Ambiguous discrepancies open a [HITL issue](#hitl-issue-convention). A **repeat finding 2× after fix** stops the fix agent and escalates to HITL with `auto_fix_exhausted: true`.

## HITL Issue Convention

A **HITL issue** is a GitHub issue with the `HITL` label. It replaces every prior `human_review/` artifact. Whenever a pipeline stage requires human disambiguation, a HITL issue is filed via `gh issue create` against the same repo as the active branch.

**PHI variable_id masking.** PHI variable_id never appears in clear text in any LLM-visible artifact (HITL issue body, PR title/body, retrieval-time report excerpt, log line at INFO+). A redactor under `scripts/security/` takes any (form, variable_id) pair and returns:

- For non-PHI variables: `variable_id` in clear text.
- For PHI-classified variables (per SoT or per `phi_patterns.py`): a hashed token (`<phi:hashed-token>`) plus the description from the form's evidence pack. Description-only label.
- When classification is uncertain: **default-to-mask**.

The clear-text (variable_id → hashed-token) mapping is persisted only to `output/<study>/audit/phi_id_mapping.json`, which lives inside the [No-LLM Zone](#no-llm-zone). Audit-zone files are already protected by the no-LLM zone, so the redactor is not required for ledger writes inside `output/<study>/audit/`.

## Architectural Decisions — May 2026

The sections below were resolved during the architecture review of `2026-05-05`
and refine or replace earlier statements above. Where they conflict with earlier
prose, these sections are authoritative.

### LLM Source Folder

The user-facing output folder under `output/{STUDY}/` is named `llm_source/`,
replacing the prior `trio_bundle/` name. The folder holds the artifacts the
LLM reads at runtime: the lean **Study Metadata Catalog**
(`study_metadata_catalog.json`), per-form **Evidence Packs**
(`evidence_packs/<form>.json`), the **Dataset Schema** folder
(`dataset_schema/catalog.json` + `dataset_schema/files/<form>.jsonl`),
the relocated per-form **Dictionary** payloads
(`dictionary/<form>.json` with lean `dictionary/catalog.json` ToC), the
**Data Dictionary** (`data_dictionary.json`, shape unchanged from
`main`), and the concept index (`concept/concept_index.json`). Internal
storage paths under `trio_bundle/` may persist during the migration, but
the canonical name and all chat/agent retrieval paths are `llm_source/`,
and `trio_bundle/` is deleted in Phase 5.

### Manual Study Variable Source of Truth Files

The **Study Variable Source of Truth (SoT)** is hand-curated as one YAML file per
form per study, stored permanently in the repo at
`data/SoT/{study}/{form_id}_policy.yaml` (e.g.
`data/SoT/Indo-VAP/19_Smear_policy.yaml`). The file is the authoritative
reference: the **Study Metadata Catalog**, per-form **Evidence Packs**, and
the **Dataset Schema** are derived from it, and the verifier compares
pipeline outputs back against it. The previous draft files at
`tmp/results/policy_pilot_*/` are working artifacts that are promoted into
this permanent location once reviewed. Draft state, when used, is recorded
as a `policy_status` field inside the YAML rather than as a filename suffix.

### Pipeline Scope

The build pipeline (`make pipeline`) covers only **dictionary extraction**
and **dataset extraction**. PDF extraction as a standalone step is
**retired**; PDF content lives directly in the **Study Variable Source
of Truth** YAMLs and is exported to per-form **Evidence Packs** at build
time (see [Study Catalog Boundary](#study-catalog-boundary)). The lean
**Study Metadata Catalog** + per-form Evidence Packs + the
**Dataset Schema** folder build runs as a separate stage downstream of
dataset extraction and reads the manual Source-of-Truth files.

### Policy Fallback Semantics

The manual **Study Variable Source of Truth** is the primary structure for
every form. The legacy auto-derivation path (PDF extraction → derived
metadata via `scripts/source_truth/builder.py`) is retained as a fallback
**only** when no policy file exists for a form (i.e.
`data/{study}/{form_id}_policy.yaml` is missing). When a policy file
exists, the build reads only from it and does not consult the legacy path.
As all 30 forms are migrated to manual policies and verified, the fallback
is retired.

### PHI Handling Ledger as Factual Source

The **PHI Handling Ledger** and the **Dataset Cleanup Ledger** are written
at the time of mutation by the code performing the action, not derived
after the fact from Source-of-Truth records. A centralized append-only
ledger writer service (`scripts/audit/ledger.py`) is the only allowed write
path; mutation sites in dataset extraction and PHI scrub call into it. Each
ledger entry carries a run id, ISO timestamp, action type, variable/column
reference, and evidence reference. The verifier confirms that the
as-written ledger from a real pipeline run agrees with the
**Study Variable Source of Truth**'s declared expectations
(`ledger_expectations:` block in the policy YAML). The ledger is therefore
the factual record of what the pipeline did, not a derivation of what was
supposed to happen.

### Verifier (Refactored Cutover Gate)

The cutover gate (`scripts/source_truth/cutover_gate.py`) is reframed as a
**verifier**: given a manual **Study Variable Source of Truth** and a real
pipeline run, it confirms three agreements — manual source ↔ built
artifacts (catalog, evidence packs, dataset schema), manual source ↔
as-written ledger entries, and built artifacts ↔ runtime LLM retrieval
paths. Disagreement on any axis is a real signal: pipeline bug, missing
ledger event, or stale Source-of-Truth.

### LLM Source Restructure (2026-05-07, locked design)

The locked [design spec](docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md) reshapes the canonical LLM-facing bundle. Where it conflicts with earlier prose in this section, the spec is authoritative.

- **End-state per study is `output/<study>/{llm_source/, audit/}` only.** `trio_bundle/`, `staging/`, `agent/`, `human_review/` are deleted in Phase 5; pipeline intermediates relocate to `tmp/<study>/<stage>/...`.
- **Lean-catalog principle.** Every catalog file in `llm_source/` is a lean ToC of pointers — `study_metadata_catalog.json`, `dataset_schema/catalog.json`, `dictionary/catalog.json`. Per-form payloads live in sibling files. The principle does not apply to `data_dictionary.json`, which keeps its `main`-branch shape.
- **Per-form evidence packs replace the legacy 949 per-variable packs.** One JSON per FORM at `evidence_packs/<form>.json`, sourced from SoT YAML + manual PDF/dataset-column extraction, no row values.
- **PHI ledger is dual-half (declared + as-written) with reconciliation.** The declared ledger is the contract built at policy-load time; the as-written ledger is the receipt emitted live by `phi_scrub.py` and `dataset_cleanup.py`. The audit folder is the [No-LLM Zone](#no-llm-zone) with path deny + runtime guard + `.gitattributes` + sentinel + 0700.
- **Phase 1 PHI-rule audit posture.** First enumerate every PHI-handling technique already in `scripts/security/` (HMAC-SHA256 pseudonymization, SANT per-subject date jitter, drop, cap, generalize, suppress_small_cell, keep allowlist, birthdate handling under safe_harbor / limited_dataset, subject-id orphan quarantine, blocking/warn/subject-id regex tiers, file exclusions, free-text whole-value drop). Then research-extend in parallel against HIPAA §164.514(b)(2)(i)(A-R), DPDPA 2023, Aadhaar Act §29 + SPDI Rule 3, ICMR 2017 §11, and NIST SP 800-188; synthesis includes a SoT-driven sweep that flags any variable whose name suggests PHI but whose SoT handling does not match a covered technique.
- **Cross-verifier (mid-pipeline, additive).** Deterministic Python scanner emits a SAFE counts-only report; the isolated LLM fix agent runs in a separate subprocess with OS read-deny on row JSONL files, opens PRs (never direct commits), opens HITL issues on ambiguity, and stops with `auto_fix_exhausted: true` after 2× repeat findings.
- **HITL replaces `human_review/`.** A HITL issue is a GitHub issue with the `HITL` label; the PHI variable_id is masked via hashed token + description from the form's evidence pack. Default-to-mask on uncertainty.
- **Logging + centralized config.** Every new module imports `scripts.utils.logging_system`; no `print` calls in pipeline code. All paths/thresholds/flags resolve through `config.py`. A linter rule fails the build if `trio_bundle`, `staging`, `agent`, or `human_review` strings appear under `scripts/`.

Implementation tracked in PHI_handing_review branch.

## Migration Status — 2026-05-05

### v2 Policy Migration Complete (28/28 forms)

All 28 Indo-VAP forms are migrated to v2 manual **Study Variable Source of
Truth** YAMLs at `data/Indo-VAP/{form_id}_policy.yaml` (one canonical YAML
per form, after the 2026-05-05 duplicate-policy resolution below). Every
file is at `schema_version: 2`, `policy_status: draft_for_human_review`,
with `forbidden_keys_absent: true`, `columns_unique: true`, and
`evidence_packs.by_variable` / `catalog_refs.by_variable` each carrying
exactly one entry per variable (no gaps, no extras).

Aggregate totals across all 28 forms:

- 1,609 variables (every dataset column represented exactly once)
- 507 `review.state: review_required` items pending human disambiguation
- 544 `ledger_expectations.phi_handling` entries
- 78 `ledger_expectations.dataset_cleanup` entries
- 302 option_sets, 262 sections, ~70+ verbatim PDF-question records per form

Action distribution: `keep` 755 / `review_required` 460 /
`pseudonymize` 135 / `jitter_date` 135 / `drop` 124. The 47-variable gap
between action `review_required` (460) and review.state `review_required`
(507) is the HIV_CD4DAT pattern — a resolved handling action (e.g.
`jitter_date`) attached to a variable that still has a PDF evidence gap
worth reviewing.

### Resolved Reviewer Disambiguations — 2026-05-05

Both previously-flagged duplicate-policy pairs were YAML-merged in place
(option iii: union semantics; canonical scalar wins; secondary value goes to
audit trail). Upstream `.xlsx` workbooks are out of scope and untouched. The
canonical name in each pair was kept; the variant filename was deleted after
sign-off.

- `14_CaseControl` ⊕ `14_Case_Control` → canonical
  `data/Indo-VAP/14_CaseControl_policy.yaml` (128 vars). The four
  dataset-only columns (`CC_HIVLOCSP`, `CC_MEDHXYN`, `CC_RBS`, `CC_RBSND`)
  are now in the merged file. 223 conflicts flipped to secondary on review
  (CHILDY1..8 PHI-safety: birth-year vars re-classified `date` /
  `jitter_date`; SUBJID2..8 `analysis_queryable: false` per the canonical
  duplicate-accounting rule; section reorganization to the granular split;
  three skip-logic completions). 611 stylistic prose conflicts
  (`display_label`, `meaning`, `notes`) kept primary.
- `2A_ICBaseline` ⊕ `2A_ICBaseline_1` → canonical
  `data/Indo-VAP/2A_ICBaseline_policy.yaml` (197 vars). 358 conflicts
  flipped to secondary (section reorganization + `IC_RESPNDT` →
  `jitter_date`, `IC_ESTWEIGHT` → `review_required`). 1515 conflicts kept
  primary, including 53 `phi_sensitive_category` cases where secondary
  proposed the non-controlled term `unmapped_or_supplemental` (rejected;
  see `PHI And Sensitive Categories`).

Per-merge audit artefacts: `tmp/merge_report_form{14,2A}.md`,
`tmp/decisions_form{14,2A}.md`, `tmp/integrity_form{14,2A}.md`,
`tmp/schema_fixes_log.md`. Original files preserved under
`tmp/policy_merge_backup_2026-05-05/Indo-VAP/`. Strict integrity verifier
passed on both forms with zero missing leaf values: every leaf from each
pre-merge backup is either in the merged YAML or in the recorded audit
trail.

Post-merge readability cleanup (2026-05-06, no information loss):

- `14_CaseControl`: dropped 17 unreferenced identical-orphan option_sets
  (e.g. `hand_wash_when_pdf`, `water_at_handwash_pdf`, `audit_freq_pdf`,
  …) whose surviving peers carry the identical option text and are the
  ones referenced by `options_ref`. Final option_sets count: 39.
- `2A_ICBaseline`: three `display_label` values containing `?` were
  double-quoted (`"Can TB Be Cured?"`, `"HIV Tested?"`, `"Pregnant?"`)
  to fix PyYAML `safe_load` parsing; the top-level `validation` block
  was reordered to follow `variables` so the F14/F2A top-level shape
  matches.

Per-form review highlights worth surfacing to the maintainer review queue:

- `3_Specimen_Collection`: 121 PHI-handling ledger entries (highest density);
  Index Case vs Household Contact split into distinct sub-sections.
- `98B_FOB`: 32 dataset-cleanup ledger entries (mostly capture metadata
  upgraded from draft `review_required` → `drop`).
- `2B_HCBaseline`, `14_CaseControl`: form-version drift / annotation typos
  flagged (`CC_VISTDAT`, `CC_WEEGHT`, `CC_RASH`, `CANCER` case mismatch,
  `HC_CHEW*`/`HC_SNUFF*` v1.0 PDF gap).
- `2A_ICBaseline`: PDF annotation `TBKNOWLINK` has no matching dataset
  column.
- `4_Smear`: entire `ZN_SOL*` / `ZN_LIQ*` culture block (16 cols + 4 dates)
  flagged because the Microscopy PDF has no culture section.
- `9_EEval`: 38 dataset-only fields parked in synthesized
  `agriculture_school_wage` section.

### Next Build Phases

With the manual Source-of-Truth layer now exhaustive for the Indo-VAP
study, the remaining migration work targets the build/audit/verify path:

1. **Build coordinator** (`scripts/source_truth/build.py`) — reads policy
   YAMLs and emits Catalog + Evidence Packs + PHI-Handled Dataset Schema
   under `output/{STUDY}/llm_source/`.
2. **Audit ledger writer** (`scripts/audit/ledger.py`) — single
   append-only write path called from dataset-extraction and PHI-scrub
   mutation sites.
3. **Verifier** — confirms manual source ↔ built artifacts ↔ as-written
   ledger ↔ runtime retrieval paths agree.
4. **Consumer migration** — point existing tools at the new artifact paths.
5. **Legacy contraction** — delete the `tmp/results/policy_pilot_*/` and
   `trio_bundle/` paths once parity is proven on every form.

## Build Pipeline — May 2026

The sections below extend the May 2026 architectural decisions to lock
the build/audit/verify pipeline that consumes the manual Source-of-Truth
layer. Where they conflict with earlier prose, these sections are
authoritative. Detailed design lives in
`tmp/design_2026-05-06_llm_source_build_pipeline.md` (local working
artifact); decisions distilled below.

### Hard Invariants

The build pipeline must hold these invariants:

1. **The 28 hand-curated `data/{study}/SoT/{form_id}_policy.yaml`
   files are frozen.** No new fields, no schema migration, no
   auto-rewrite. The build reads them as-is. The cross-form concept
   index is now structurally DERIVED from these per-form SoT files
   (see Concept Layer Source below); there is no separate
   hand-authored `study_concepts.yaml`.
2. **`output/{STUDY}/llm_source/` contents are exactly these
   artifacts**, no more, no less (per the locked
   [design spec](docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md)):
   - `study_metadata_catalog.json` (lean ToC; pointers only)
   - `evidence_packs/{form}.json` (one JSON per FORM; replaces the
     legacy 949 per-variable evidence packs)
   - `dataset_schema/catalog.json` + `dataset_schema/files/{form}.jsonl`
     (lean ToC + PHI-cleaned rows, one JSONL per form)
   - `concept/concept_index.json` (study concepts, derived from the
     per-form SoT policy files)
   - `dictionary/catalog.json` + `dictionary/{form}.json` (lean ToC +
     per-form dictionary payloads relocated from
     `trio_bundle/dictionary/{form}.json`; per-form payload shape
     unchanged)
   - `data_dictionary.json` (existing dictionary extraction output,
     shape unchanged from `main`; lean-catalog principle does NOT apply
     to this file)
3. **The `output/{STUDY}/audit/` primary deliverable is the two
   ledgers**, each in declared and as-written halves. They replace the
   existing `phi_scrub_report.json` and `dataset_cleanup_report.json`:
   - `phi_handling_ledger.declared.json` + `.as_written.json`
     (replacing `phi_scrub_report.json`)
   - `dataset_cleanup_ledger.declared.json` + `.as_written.json`
     (replacing `dataset_cleanup_report.json`)
   Maintainer-internal artifacts (`verifier_report.json`,
   `policy_runtime_mismatch.json`, `preflight_mismatch.json`,
   `lineage_manifest.json`) may sit alongside in `audit/` but are not
   the IRB-facing audit deliverable.
4. **Dictionary extraction, dataset extraction (pre-PHI), and PHI scrub
   logic are preserved untouched.** The PHI scrubber gains ledger-write
   calls at its mutation sites only; its scrubbing behavior is
   unchanged. `make dictionary` and `make extract-datasets` are
   untouched.
5. **Chat read surface is `llm_source/` only.** PHI-cleaned dataset rows
   live under `llm_source/dataset_schema/files/<form>.jsonl`; the
   legacy top-level `datasets/` folder is retired in Phase 5. The audit
   folder is unreachable from chat by zone-guard construction
   (`assert_llm_source_zone` rejects any path under `audit/` or any
   intermediate scratch path) and by the [No-LLM Zone](#no-llm-zone)
   defense-in-depth (path deny + runtime guard + `.gitattributes`
   marker + sentinel file).

### Pipeline Stages

```
Stage 1 — PARALLEL
  Branch X (preserved):
    Dictionary extraction → existing producer
    Dataset extraction (pre-PHI) → raw values + column inventory
  Branch Y (new — replaces PDF extraction AND variables.json):
    Build coordinator (scripts/source_truth/build.py) reads
      data/{study}/SoT/{form_id}_policy.yaml × N
    Derives the cross-form concept index structurally from those
      per-form SoT files (no hand-authored study_concepts.yaml)
    Emits to llm_source/: catalog + evidence packs + concept_index
    Emits to audit/: phi_handling_ledger.declared.json,
                     dataset_cleanup_ledger.declared.json

Stage 2 — MERGE
  Dataset schema = extraction column inventory ⊕ SoT facets,
    deduplicated to one record per column
  Concept index enriched with analysis_queryable from dataset schema
  Both written to tmp/{study}/merge/llm_source/

Stage 3 — PHI scrub + dedup (mutation; preserved logic)
  Scrubber writes rows to tmp/{study}/scrub/llm_source/dataset_schema/files/{form}.jsonl
  Scrubber + dedup call scripts/audit/ledger.py:LedgerWriter
    Emits as-written ledgers to tmp/{study}/scrub/audit/
  Honest-broker shape scanner profiles raw dataset → bucketed
    metadata only → tmp/{study}/scrub/audit/column_shape_profile.json (no values)

Stage 3.5 — Pre-publication verification gate
  Reads SoT YAMLs, declared ledgers, as-written ledgers,
    staged dataset schema, column shape profile (no values)
  Eight checks (false negatives, false positives, ledger drift,
    action mismatch, pseudonym integrity, cross-form consistency,
    drop confirmation, shape-vs-declaration)
  PASS → atomic promote tmp/{study}/scrub/* → llm_source/ + audit/
  FAIL → write audit/preflight_mismatch.json with structured fix-list;
         tmp scratch held; previously published artifacts remain
         authoritative

Stage 3.6 — Cross-verifier (additive; accumulate-don't-block)
  Deterministic scanner (no LLM) → SAFE counts/booleans report
  Isolated fix agent (LLM, separate subprocess, OS read-deny on row
    JSONL files; reads scanner report + SoT YAML + phi_scrub.yaml +
    per-form evidence pack only) → opens PR (no direct commit) for
    eligible auto-fixes; ambiguity → HITL issue;
    repeat finding 2× → escalates to HITL with auto_fix_exhausted: true

Stage 4 — Post-publication verifier (maintainer CLI)
  Refactor of cutover_gate.py
  Four-axis agreement: SoT ↔ built artifacts ↔ as-written ledger ↔
    runtime retrieval ↔ lineage manifest fingerprints
  Writes audit/verifier_report.json (always),
         audit/policy_runtime_mismatch.json (only divergences)
```

### Output Layout

```
output/{STUDY}/
  llm_source/                   GREEN; chat read surface
    study_metadata_catalog.json     (lean ToC → evidence_packs/<form>.json)
    evidence_packs/
      {form}.json                   (one JSON per FORM; PDF metadata; no values)
    dataset_schema/
      catalog.json                  (lean ToC → files/<form>.jsonl + handling flags)
      files/
        {form}.jsonl                (PHI-cleaned rows, one JSONL per form)
    dictionary/
      catalog.json                  (lean ToC → dictionary/<form>.json)
      {form}.json                   (per-form dictionary payload, shape unchanged)
    concept/
      concept_index.json            (derived from per-form SoT YAMLs)
    data_dictionary.json            (shape unchanged from `main`)

  audit/                        RED; no-LLM zone (maintainer/IRB only)
    .NO_LLM_ZONE                    (sentinel; read-asserted by runtime guard)
    Primary deliverable (ledger replacement of legacy reports):
      phi_handling_ledger.declared.json
      phi_handling_ledger.as_written.json
      dataset_cleanup_ledger.declared.json
      dataset_cleanup_ledger.as_written.json
    PHI-id mapping (variable_id → hashed token):
      phi_id_mapping.json
    Maintainer-internal support:
      lineage_manifest.json
      verifier_report.json
      policy_runtime_mismatch.json
      preflight_mismatch.json       (only when last gate failed)
      telemetry/

tmp/{study}/<stage>/             scratch; atomic; cleaned on success
```

Per the [design spec](docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md), the end-state per study is exactly `llm_source/` + `audit/`. The `output/{STUDY}/trio_bundle/`, `output/{STUDY}/staging/`, `output/{STUDY}/agent/`, and `output/{STUDY}/human_review/` directories are retired in Phase 5; pipeline intermediates write to `tmp/<study>/<stage>/...` per the [Intermediate Scratch Convention](#intermediate-scratch-convention). The legacy `output/{STUDY}/audit/phi_scrub_report.json` and `output/{STUDY}/audit/dataset_cleanup_report.json` are retired by the ledger replacement, after a dual-emit cutover window proves parity. The legacy 949 per-variable evidence pack files are deleted in Phase 5 in favor of the per-form `evidence_packs/<form>.json` shape.

### Concept Layer Source

The cross-form concept index is **structurally derived** from the
per-form SoT policy YAMLs by
`scripts/source_truth/concept_derivation.py`. Five derivation passes
(cohorts, outcomes, exposures, schedules, definitions) read only the
already-loaded SoT artifacts and emit a deterministic concept index
with `policy_status: "derived_from_sot"` and no hand-authored
definition wording. The derived index is written to
`llm_source/concept/concept_index.json`.

Cohorts are assigned by form-id A/B suffix (e.g., `1A_*` → cohort_a,
`1B_*` → cohort_b). Outcomes are pulled from SAE / follow-up /
final-outcome / final-status forms via section-name pattern
matching. Exposures are baseline-form risk-factor sections (alcohol,
smoking, medical_history, hiv, diet, diabetes, etc.). Schedules are
classified by form_title regex with form-id fallback. Definitions
surface only structural section_labels — exact PDF wording lives in
the per-form evidence packs (`evidence_packs/<form>.json`), not in the
concept index.

### Retrieval Tier Order

Chat-path retrieval consults artifacts in this order, falling through
when an earlier tier is insufficient:

1. **Compact catalog** (lean ToC `llm_source/study_metadata_catalog.json`) —
   variable meaning, form, section, options, source presence; pointers
   into per-form evidence packs.
2. **Dataset schema + values**
   (`llm_source/dataset_schema/catalog.json` lean ToC plus
   `llm_source/dataset_schema/files/{form}.jsonl`) — analysis queries.
3. **Concept index + per-form evidence packs**
   (`llm_source/concept/concept_index.json`,
   `llm_source/evidence_packs/{form}.json`) — concept questions, exact
   PDF wording, full provenance. Note the per-form (not per-variable)
   evidence-pack shape per the locked design.
4. **Dictionary** (`llm_source/data_dictionary.json`, with per-form
   payloads at `llm_source/dictionary/{form}.json` indexed by
   `llm_source/dictionary/catalog.json`) — fallback when tiers 1–3
   yield no answer or signal an evidence gap. The form + dataset are
   the primary axis for study questions; the dictionary is consulted
   only when those cannot answer.
5. **Controlled notice** — `absent_from_current_dataset`,
   `dropped_contact_maintainer`, `ambiguous_variable_requires_clarification`,
   per `Notice Template Distinctions`.

### Pre-publication Gate Checks

The gate operates on SoT declarations + ledger entries + staged dataset
schema + bucketed column-shape metadata. It never reads raw values.
Each check is fail-closed (block on violation), except where noted as
flag-only:

- **A. False negatives.** SoT action ∈ {drop, pseudonymize,
  jitter_date, generalize} but the column is present un-transformed in
  the staged dataset schema → block.
- **B. False positives.** SoT action = keep with `phi_category =
  not_phi` but the column is missing or transformed → block.
- **C. Ledger ↔ SoT alignment.** Declared event without an as-written
  match, or as-written without declared → block.
- **D. Action mismatch.** SoT action differs from as-written action →
  block.
- **E. Pseudonym integrity.** distinct(pseudonym) ≠ distinct(subject_id)
  per namespace → block.
- **F. Cross-form consistency.** The same logical identifier (e.g.
  `SUBJID`) classified under different `phi_sensitive_category` values
  across forms → block. Operates on existing fields only; the gate does
  not auto-modify YAMLs.
- **G. Dropped vars absent.** SoT action = drop ⇒ variable not in
  staged schema → block.
- **H. Shape-vs-declaration.** Honest-broker shape scanner emits
  bucketed metadata (`format_signature`, `cardinality_class`,
  `uniqueness_ratio_band`, `length_band`, `null_share_band`). Hard
  mismatch with existing `phi_sensitive_category` (e.g. declared
  `not_phi` but `format_signature: ssn_like`/`phone_like`/`email_like`)
  → block. Soft mismatches (e.g. high uniqueness in many-row form for
  a declared `not_phi` column) → flag in `preflight_mismatch.json`
  without blocking. `unknown` `phi_sensitive_category` entries are
  tolerated (see invariant #1) and surfaced as flags, not blocks.

On block, `preflight_mismatch.json` records each finding with check id,
form, variable, issue, and a fix-path list pointing the maintainer at
either the SoT YAML or the scrub configuration. Two roots of a finding:
SoT misclassification (update YAML) or scrub misbehavior (fix
`phi_scrub.yaml` or `phi_scrub.py`). The gate never auto-fixes either.

### Honest-broker Aggregate Scanner

`scripts/audit/column_shape_scanner.py` runs in the `tmp/<study>/scrub/`
scratch zone where it has access to raw values. It emits
`column_shape_profile.json` to `tmp/<study>/scrub/audit/` containing
only bucketed metadata: format signature classes (regex-based, not
values), cardinality class, uniqueness ratio band, length band, null
share band, plus the variable's existing declared category and action.
The output crosses no values into the canonical `llm_source/` or
`audit/` zones. The profile is consumed by the gate (Check H) and is
**not** promoted to `output/{STUDY}/audit/`. On gate PASS the profile
is discarded with the rest of the `tmp/` scratch; on FAIL it is
retained in `tmp/<study>/scrub/audit/` next to `preflight_mismatch.json`
for fix triage. CI tests assert the scanner schema is closed and that
no field carries value-bearing data.

### Audit Ledger Writer

`scripts/audit/ledger.py` is the single allowed write path for as-written
ledger files. It is append-only and called from the existing PHI-scrub
mutation sites (`scripts/security/phi_scrub.py`) and the dedup mutation
sites. Each event records run id, ISO timestamp, action, variable id,
form, evidence reference (variable id + form + policy YAML path), and
optional aggregate counts. No event records raw values, before/after
values, or row identifiers. On scrubber close, the writer finalizes the
JSON list with the run id, ISO timestamp, scrub config hash, and input
dataset hash; partial runs leave an incomplete list rather than a
mid-record corruption. The writer is the only module that opens the
as-written ledger files for write.

### Consumer Migration

The cutover affects:

- `scripts/ai_assistant/agent_tools.py` — `_load_variables_json()` reads
  `llm_source/study_metadata_catalog.json` (lean ToC). Heavy-field
  reads (exact PDF wording, full relationships) lazy-fetch the
  per-form pack `llm_source/evidence_packs/{form}.json` and locate the
  variable record inside that file. Catalog resolution unifies on
  `output/{STUDY}/llm_source/study_metadata_catalog.json`.
- `scripts/utils/snapshots.py` and `scripts/utils/restore_drill.py` —
  root-marker check uses `study_metadata_catalog.json` instead of
  `variables.json`.
- `scripts/security/secure_env.py` — `assert_trio_bundle_zone` renamed
  to `assert_llm_source_zone`. Allowed root: `llm_source/` only
  (PHI-cleaned rows live under `llm_source/dataset_schema/files/`; the
  legacy `datasets/`, `agent/`, `staging/`, and `human_review/` roots
  are rejected). The `audit/` folder is explicitly rejected, enforcing
  invariant #5 plus the no-LLM zone defense-in-depth. All call sites
  renamed.
- `artifact_type: study_variable_catalog` (multiple modules in
  `scripts/source_truth/`) — renamed to `study_metadata_catalog`.
- `scripts/extraction/build_variables_reference.py` and the
  `make build-variables` target — demoted to fallback during cutover,
  retired after parity proven against the new compact catalog.

The legacy `variables.json` and `study_variable_catalog.json` continue
to be emitted for the cutover window so consumers can dual-read; once
parity passes they are retired together with the legacy
`phi_scrub_report.json` and `dataset_cleanup_report.json`.

### Accuracy Levers

The build pipeline includes accuracy improvements that operate on
existing policy YAML fields and runtime hooks. **No additions to policy
YAMLs.** The levers are:

- Preflight checks on coverage, action completeness, and review state.
- Mutation discipline (determinism, idempotency, pseudonym collision
  detection) wrapped around the unchanged scrubber via the ledger
  writer and lineage manifest hashing.
- Postflight gate (Stage 3.5) covering the eight checks above.
- Review-gated publish (`policy_status != ready` blocks, existing
  `review_state`/`exposure_state` enforced).
- Scrub config versioning recorded in the lineage manifest.
- Tier 3 honest-broker shape scanner (Gate Check H) catches the SoT
  blind spot — column-headers + PDF + dictionary cannot reveal value
  shape; bucketed shape metadata can flag declared `not_phi` columns
  whose values look like identifiers, dates, or contact info.

Out of scope for this milestone (would require modifying frozen policy
YAMLs): adding new policy fields such as `pseudonym_namespace`,
`jitter_basis`, `column_name_regex_hint`, `generalization_granularity`,
or `free_text_scrub_profile`; mandating non-`unknown`
`phi_sensitive_category` (`unknown` entries are tolerated; the gate
flags via shape scanner instead); synthetic-data accuracy testing;
jitter monotonicity sampling.

