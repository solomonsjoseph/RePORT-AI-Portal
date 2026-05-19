# Runbook: Build a SoT Lean YAML for One Form

End-to-end operator guide for running the sot-lean-generator pipeline on a
single annotated PDF / dataset pair. Uses **Indo-VAP / 6_HIV** as the worked
example throughout.

---

## Prerequisites

| Item | Check |
|------|-------|
| `data/raw/Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf` present | `ls "data/raw/Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf"` |
| `data/raw/Indo-VAP/datasets/6_HIV.xlsx` present | `ls data/raw/Indo-VAP/datasets/6_HIV.xlsx` |
| Ghostscript (`gs`) installed | `gs --version` |
| `uv` installed | `uv --version` |
| Output directory exists | `mkdir -p output/Indo-VAP/llm_source/source_truth` |

---

## Step 1 — Stage 0: Source Pack

Run the deterministic source-pack extractor. This reads **only row 1** of the
dataset file (row 2+ values are never read or used) and renders every PDF page
at 600 DPI.

```bash
make sot-source-pack STUDY=Indo-VAP FORM=6_HIV
```

Equivalent direct CLI (cross-LLM entry point, works without Make):

```bash
python -m scripts.source_truth.study_intake --study Indo-VAP --form 6_HIV
```

**Expected outputs:**

- `/tmp/sot_source_pack_6_HIV.json` — JSON object with `headers` (dataset row-1
  array), `pdf_sha256`, `renders`, and `screenshot` as the first-render
  compatibility alias.
- `/tmp/sot_render_6_HIV/6 HIV v1.0.pdf.page-001.png`, `.page-002.png`, ... —
  600 DPI PNG renders of the annotated PDF (visual ground truth for Stages 1–3).

**Stop here if either output is missing.** Check that `gs` is on `$PATH` and
that the PDF is not password-protected or truncated.

### Batch Runtime Build

For the normal runtime rebuild, do not run each form by hand. Use:

```bash
make build-llm-source STUDY=Indo-VAP
```

That command:

1. runs source-pack extraction for each PDF-backed form,
2. generates the lean YAML into `/tmp`,
3. verifies the YAML against the source pack,
4. promotes only passing YAMLs to
   `output/Indo-VAP/llm_source/source_truth/`, and
5. runs `main.py --pipeline` to publish PHI-scrubbed datasets,
   dictionary mappings, audit ledgers, lineage, and the output signpost.

To force a clean generated-output rebuild while preserving
`output/Indo-VAP/agent/`, use:

```bash
make rebuild-llm-source STUDY=Indo-VAP
```

---

## Step 2 — Stages 1–3: LLM YAML Authoring

High-assurance authoring requires LLM/manual reasoning over the source pack and
page renders. Runtime rebuilds may use the conservative script-backed candidate
generator, but those candidates still require the same verifier and
diff-against-gold gates before promotion.

### Skill-aware or Manual Authoring

Open `skills/sot-lean-generator/SKILL.md` and follow the stage instructions.
Write the result to `/tmp/6_HIV_lean.yaml`.

### Direct Rules-File Path

1. Read `skills/sot-lean-generator/references/exhaustive_yaml_rules.md`.
   Write the exhaustive YAML draft for the form using the source pack and the
   600 DPI page renders as your evidence sources.

2. Run 5 visual sweep iterations: compare every render listed in the source
   pack's `renders` array against your draft. Correct any widget
   type, field label, or value-set mismatches. Do not invent details that are not
   visible in the render; if a widget is ambiguous at 600 DPI, **pause and ask
   the human** — do not guess.

3. Read `skills/sot-lean-generator/references/lean_yaml_rules.md`. Trim the
   exhaustive draft to the lean schema. Write the final result to
   `/tmp/6_HIV_lean.yaml`.

**All LLM tools share the same rules files and the same verifier.**

---

## Step 3 — Stage 4: Verify

Run the deterministic verifier. It checks forbidden text tokens, forbidden
keys, instruction-block whitelist, and header-equality against the source pack.

```bash
make sot-verify STUDY=Indo-VAP FORM=6_HIV
# validates /tmp/6_HIV_lean.yaml by default
# use CANDIDATE=/path/to/file to override
```

### Exit codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | All checks pass — ready for the next gate | Continue to Step 4 |
| 1 | Validation/content failure | Fix the candidate and re-run |
| 2 | SHA mismatch — source pack does not match the PDF on disk | Re-run Step 1 (Stage 0), then redo Stages 1–3 |
| 3 | Script gap — verifier could not evaluate a check | **Stop. Do not promote. Ask the human.** |
| other | Unexpected error | Inspect stderr; do not promote |

---

## Step 4 — Stage 5: Promote

Copy the verified lean YAML to the canonical output path.

```bash
cp /tmp/6_HIV_lean.yaml output/Indo-VAP/llm_source/source_truth/6_HIV_policy.lean.yaml
```

The canonical output path is:

```
output/<study>/llm_source/source_truth/<form>_policy.lean.yaml
```

This file is the runtime input consumed by the LLM source builder
(`make build-llm-source`).

---

## Escalation Rules

Stop and ask the human (do not proceed, do not invent) in these situations:

- A widget shape or field label is ambiguous in the 600 DPI render and the
  annotated PDF provides no additional clarity.
- The verifier exits with code 3 (script gap).
- The verifier exits with code 2 more than once after re-running Stage 0
  (possible PDF corruption or path mismatch).
- A field's value set or inclusion rule contradicts both the printed form and
  the annotation — record the discrepancy in the YAML's `discrepancies:` block
  and flag it for human review before promoting.

---

## Reference: Canonical Gold and Diff-Against-Gold

`data/SoT/<study>/` holds **anchored lean gold** for forms that have completed a
gold/attestation workflow. It is the comparison source for
diff-against-gold checks when a gold file exists. Under Operating Rule 4 of
`docs/plan_sot_afk_pipeline.md`: "No gold may be modified silently. A gold
YAML changes only via the Anchor or Re-anchor workflow with electronic
signature."

The `_attestations/` subdirectory holds per-form attestation JSON. Phase 1
wrote placeholder files with `status: "pre-plan"` and `attested: false`. Phase
6's anchor ceremony will replace them with real e-signed attestations.

The promoted runtime artifact at
`output/<study>/llm_source/source_truth/<form>_policy.lean.yaml` is generated
from the printed PDF plus row-1 dataset headers and must pass the lean checker
before promotion. It is not copied silently over an anchored gold file.

To run all three gates (verifier + property validator + diff-against-gold) in
one command:

```bash
make sot-validate STUDY=Indo-VAP FORM=6_HIV
# Requires /tmp/sot_source_pack_6_HIV.json — run `make sot-source-pack` first.
# Validates /tmp/6_HIV_lean.yaml by default; pass CANDIDATE=/path/to/file to override.
# Exits non-zero if any gate fails.
```

To inspect diffs directly:

```bash
uv run --all-groups python scripts/source_truth/diff_against_gold.py \
  --study Indo-VAP --form 6_HIV \
  --candidate /tmp/6_HIV_lean.yaml
# Writes /tmp/diff_6_HIV.json with cosmetic / within_rule / novel lists.
# Exit 0 = all diffs cosmetic or empty. Exit 1 = novel diffs. Exit 2 = I/O error.
```

---

## Related Files

| File | Role |
|------|------|
| `skills/sot-lean-generator/SKILL.md` | SoT authoring guide for Stages 1–3 |
| `skills/sot-lean-generator/references/exhaustive_yaml_rules.md` | Stage 1 rules (any LLM) |
| `skills/sot-lean-generator/references/lean_yaml_rules.md` | Stage 3 trim rules (any LLM) |
| `skills/sot-lean-generator/scripts/extract_sources.py` | Stage 0 implementation |
| `skills/sot-lean-generator/scripts/check_lean_policy.py` | Stage 4 implementation |
| `scripts/source_truth/study_intake.py` | CLI wrapper — delegates to Stage 0 script |
| `AGENTS.md` § SoT creation | Machine-readable flow summary for agentic tools |
| `docs/sphinx/developer_guide/architecture.rst` § Source-Truth | Layered architecture description |
