# SoT Intake Review — Indo-VAP
Generated: 2026-05-15 (seed entries migrated from `excluded_from_sot.yaml`)
Tick `- [x]` after triage; re-run the intake CLI to refresh.

## Unpaired PDFs (no matching dataset)

## Unpaired datasets (no matching PDF)

## Empty / unreadable header row

## Multi-sheet workbooks

## Formula headers

## PHI-shaped headers

## Low-confidence fuzzy matches

## Duplicate filename, mismatched content

## Deprecated stubs
- [ ] **30_Air_Quality** — `data/raw/Indo-VAP/datasets/30_Air_Quality.xlsx`
      Reason: `deprecated_stub`
      Notes: Source XLSX has only 4 admin columns (Time_Stamp, AP_COMPDAT, AP_SIGN, AP_INIT) and 3 data rows. No measurement variables. The canonical environmental-evaluation form is 9_EEval. Recommended disposition (per Phase 0 HITL flag at tmp/sot_pdf_sweep/30_Air_Quality.diff.md): mark deprecated.
      Disposition: __________  (add_with_override / delete / keep_in_review / rename)

## Requires HITL disambiguation
- [ ] **96_Specimen_Tracking** — `data/raw/Indo-VAP/datasets/96_Specimen_Tracking.xlsx`
      Reason: `requires_hitl_disambiguation`
      Notes: Dataset has an unnamed `Field1` column (likely placeholder or system marker) that needs human disambiguation. Draft policy exists at tmp/sot_dataset_policy_drafts/96_Specimen_Tracking_policy.yaml.draft with `Field1` flagged as review_required. After the human resolves Field1's intent, the draft can be hand-merged into data/Indo-VAP/SoT/dataset_policies/.
      Disposition: __________  (add_with_override / delete / keep_in_review / rename)
