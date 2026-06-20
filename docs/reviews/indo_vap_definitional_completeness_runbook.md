# Indo-VAP — Definitional completeness runbook (2026-06-08)

Three gaps currently prevent the AI assistant from answering the six most common
definitional questions researchers ask (relapse vs treatment-failure criteria,
inclusion/exclusion criteria prose, household-contact dwelling-vs-pot threshold,
DST panel composition and follow-up cadence, exposure-form variable coverage,
and DST-isolate form variable coverage). Each gap has a concrete unblocking
action. Two of the three require a data-owner input before any pipeline work
can proceed.

---

## Claim 1 — Common-Protocol narrative: drop-in mechanism verified

### Verification

`search_llm_source` (agent_tools.py:1693) and `read_llm_source_file`
(agent_tools.py:1804) both resolve every candidate path through
`_resolve_within_llm_source` (agent_tools.py:1616–1623), which roots all
lookups at `config.STUDY_LLM_SOURCE_DIR` and then calls `validate_agent_read`.
`validate_agent_read` (file_access.py:85–105) admits any path whose `realpath`
falls inside `config.STUDY_LLM_SOURCE_DIR` — the entire subtree, not a named
allowlist.

`search_llm_source` iterates `base.rglob("*")` (agent_tools.py:1735) and
checks `path.suffix.lower() not in _LLM_SOURCE_TEXT_SUFFIXES`
(agent_tools.py:1742). The suffix set (agent_tools.py:1581) is:

```
{".yaml", ".yml", ".json", ".jsonl", ".md", ".txt", ".csv"}
```

`.md` is included. Therefore any `.md` or `.txt` file placed anywhere under
`output/{STUDY}/llm_source/` is automatically discovered by `search_llm_source`
and readable via `read_llm_source_file` — **no code changes are required**.

`cite_source` (agent_tools.py:1499) delegates to `cite_variable`
(citations.py:158), which searches SoT policy YAMLs, dataset schema JSONs,
JSONL header lines, and the study-config YAML for a `(form_id, field_id)` pair.
A free-text protocol narrative does not contain field keys in that format, so
`cite_source` will not index it automatically. However, `search_llm_source` with
a topic query (e.g. `"relapse treatment failure"`) will return `path:line:
snippet` hits directly from the narrative file — which is the right citation
mechanism for prose definitions.

### Recommended path and naming

Place the Common-Protocol narrative under:

```
output/Indo-VAP/llm_source/study_metadata/protocol_narrative.md
```

`config.LLM_SOURCE_STUDY_METADATA_DIR` resolves to
`output/{STUDY}/llm_source/study_metadata/` (config.py:266). Using that subdir
groups it with the study variable map and catalog, keeping the agent's knowledge
surface cohesive.

### Publish mechanism

The pipeline does not currently copy a protocol narrative automatically. The
publish path for the study variable map (main.py:1113–1121) copies
`config/study_knowledge.yaml` → `output/.../study_metadata/study_variable_map.yaml`.
The same pattern applies: place the raw source at
`data/raw/Indo-VAP/protocol_narrative.md` (alongside `_forms_manifest.yaml` and
`_study_privacy.yaml`, which are the canonical raw study inputs for this study)
and add a publish step in `main.py` that copies it into
`config.LLM_SOURCE_STUDY_METADATA_DIR`. Until that step is added, a one-time
manual copy after `make study STUDY=Indo-VAP` is sufficient for the current bundle.

### Questions capped by this gap

All six of the recurring definitional questions rely on protocol prose that does
not appear in any dataset column or SoT YAML:

- Relapse vs treatment-failure classification criteria
- Index-case and household-contact inclusion/exclusion criteria
- Household-contact dwelling-vs-pot threshold definition
- DST panel composition and follow-up specimen cadence

None of these can be answered with confidence from dataset column names or SoT
policy alone. The protocol narrative is the only authoritative source.

### Impact / data-owner action required

**Blocked on data-owner input.** The Common-Protocol narrative (or an extract
covering the six definitional questions above) must be supplied by the principal
investigator or study coordinator. Once provided, the operator copies it to
`data/raw/Indo-VAP/protocol_narrative.md` and copies it again into
`output/Indo-VAP/llm_source/study_metadata/protocol_narrative.md` (or adds a
pipeline step to do so). No code changes are required for the agent to find it.

---

## Claim 2 — SoT gap for forms 53_exposure and 21_DSTIsolate: verified

### Verification

**Dataset schema JSONL exists for both forms** (verified by `ls`):

```
output/Indo-VAP/llm_source/dataset_schema/files/21_DSTIsolate.jsonl
output/Indo-VAP/llm_source/dataset_schema/files/53_exposure.jsonl
```

**No SoT policy directory exists for either form** (`ls output/Indo-VAP/llm_source/SoT/` returns no entry matching `21` or `53`).

**No annotated PDF exists for either form** (`ls data/raw/Indo-VAP/annotated_pdfs/` lists 28 PDFs; none match `21`, `53`, `exposure`, or `DSTIsolate`). The `study_intake.py` resolver (study_intake.py:80–92) looks for a file in `annotated_pdfs/` whose leading token matches the form code; with no file present it raises a `missing_pdf` issue (study_intake.py:466–475) and writes `status=human_review_required` — intake cannot proceed.

**Consequence for the agent.** `cite_variable` (citations.py:178–180) calls
`_resolve_form_dirs`, which looks in `config.LLM_SOURCE_SOT_DIR` for a
directory matching the form prefix. With no SoT directory, it raises
`CitationNotFoundError`. The variable map fallback (citations.py:147–155)
searches `study_metadata/study_variable_map.yaml` for the field name as a last
resort, but this covers only the curated concept-to-column mappings in
`config/study_knowledge.yaml`, not the full set of columns in these two forms.
Any field unique to `21_DSTIsolate` or `53_exposure` that is not in the curated
map will return `"error": "no citation"`.

### Command sequence once annotated PDFs are provided

SoT generation is LLM-driven (it calls the configured model at generation time,
controlled by `config.LLM_PROVIDER` / `config.LLM_MODEL`) and operates only on
human-confirmed PDF/dataset pairs. The sequence per form is:

**Step 1 — Intake (human confirmation required)**

```bash
make sot-source-pack STUDY=Indo-VAP FORM=21_DSTIsolate
```

This runs `scripts.source_truth.study_intake` (Makefile:213–215), resolves the
PDF and dataset, and writes `SoT_intake_review.md` for human review. The
operator must inspect and confirm the pair before proceeding. Do not advance
past this step without confirmation.

**Step 2 — Generation**

```bash
make sot-generate-all STUDY=Indo-VAP
```

This runs `scripts.source_truth.generate_lean_outputs` (Makefile:217–219),
which generates a lean YAML + `dataset_schema.yaml` for each confirmed form and
runs `check_lean_policy.py` inline. Repeat for form 53.

**Step 3 — Verification**

```bash
make sot-verify STUDY=Indo-VAP FORM=21_DSTIsolate CANDIDATE=<path_to_lean.yaml>
make sot-verify STUDY=Indo-VAP FORM=53_exposure   CANDIDATE=<path_to_lean.yaml>
```

For a full all-gates check (verifier + diff-against-gold):

```bash
make sot-validate STUDY=Indo-VAP FORM=21_DSTIsolate CANDIDATE=<path>
make sot-validate STUDY=Indo-VAP FORM=53_exposure   CANDIDATE=<path>
```

`sot-validate` requires a source pack from Step 1 at
`/tmp/sot_source_pack_<FORM>.json` (Makefile:240–251).

### Impact / data-owner action required

**Blocked on data-owner input.** Annotated PDFs for forms 21 (DST Isolate) and
53 (Exposure) must be supplied and placed under
`data/raw/Indo-VAP/annotated_pdfs/` before SoT intake can run. Until then:

- Agent queries about variables in these two forms fall back to the dataset
  schema JSONL (column names only, no clinical definitions).
- `cite_source` will return `"error": "no citation"` for any field in these
  forms unless it appears in `config/study_knowledge.yaml`.

This is not a pipeline bug — it is the correct fail-safe behavior. The SoT
generation skill is designed to hold forms for human review when a required
input is absent rather than fabricating definitions.

---

## Claim 3 — study_variable_map.yaml tracking: nuance clarified

### Verification

The published file at
`output/Indo-VAP/llm_source/study_metadata/study_variable_map.yaml` is
gitignored (`.gitignore` line 278: `output/` matches the entire output tree).
However, its **source** is git-tracked:

```
config/study_knowledge.yaml   (tracked: git ls-files config/)
```

`main.py:1114` hard-codes the copy source as:

```python
src = Path(__file__).resolve().parent / "config" / "study_knowledge.yaml"
```

`make rebuild-llm-source` (or `make study`) copies this source into
`output/.../study_metadata/study_variable_map.yaml` on every run. As long as
edits are made to `config/study_knowledge.yaml` and committed, the published
copy is always reproducible.

### Actual fragility

The fragility is not that the source is untracked — it is tracked. The fragility
is that **any edit made directly to the published copy**
(`output/.../study_metadata/study_variable_map.yaml`) rather than to the source
(`config/study_knowledge.yaml`) will be silently overwritten on the next
`make rebuild-llm-source` run. This is an operator-workflow risk, not a
reproducibility gap.

A secondary risk: because `output/` is fully gitignored, there is no diff-based
guard to catch the "edited the wrong file" mistake. The current pipeline emits
only an `INFO` log line on publish (main.py:1121); it does not warn if the
output differs from the source.

### Recommendation

1. Always edit `config/study_knowledge.yaml` (git-tracked), never the
   published copy.
2. To add new concept-to-column mappings, edit `config/study_knowledge.yaml`,
   commit, and re-run `make study STUDY=Indo-VAP` to republish.
3. Optionally: add a `make check-study-knowledge` target that diffs the
   published copy against the source and exits non-zero on divergence, as a
   pre-publish guard.

### Impact / data-owner action required

No data-owner input is required for the tracking recommendation. However,
`config/study_knowledge.yaml` currently maps curated concepts (smoking,
diabetes, recurrence, outcome positive-label sets) — it does not cover every
column in every form. Extending it to cover `21_DSTIsolate` and `53_exposure`
variables would improve `cite_source` coverage for those forms independently of
the SoT gap above. This extension requires a data-owner or study coordinator to
provide authoritative concept definitions for those columns.

---

## Summary table

| Gap | Blocked on | Unblocking action |
|-----|-----------|-------------------|
| Common-Protocol narrative missing | Data-owner must supply prose | Place at `data/raw/Indo-VAP/protocol_narrative.md`; copy into `llm_source/study_metadata/` |
| No SoT for `21_DSTIsolate` | Annotated PDF not supplied | Provide PDF → `data/raw/Indo-VAP/annotated_pdfs/` → run intake → generate → verify |
| No SoT for `53_exposure` | Annotated PDF not supplied | Same sequence as above |
| `study_variable_map.yaml` edit risk | Operator workflow | Always edit `config/study_knowledge.yaml`; never the published copy |

No code changes are required for items 1 or 3. Items 2 and 3 (annotated PDFs)
are pure data-owner inputs with no code dependency once provided.
