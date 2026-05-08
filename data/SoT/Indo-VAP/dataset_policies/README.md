# Dataset-only SoT policies

Forms that have a dataset but no source PDF. Policies inferred from
dataset COLUMN KEYS ONLY (no row values). Each YAML carries:

- `policy_source: dataset_columns_only` at the top level.
- `claude_drafted: true` on every variable entry.

Extraction conventions:
- `*DAT` / `*DATE` suffixes → `jitter_date` / `date_field`.
- `*ID` / `SUBJID` / `FID` → `pseudonymize` / identifier category.
- `*OTH` / `*OTHER` / `*EXPLAIN` / `*SP` → `review_required` / `free_text_phi`.
- `*NA` (not-applicable flag) → `keep` / `not_phi`.
- Other → `review_required` with `reason: column_name_only_action_unconfirmed_without_pdf_or_data_dictionary`.

A human reviewer must validate every entry before a form leaves
`review_required` status.

## Currently in this directory

The following dataset-only policies were drafted by Phase 0 and merged
on 2026-05-08 (per `tmp/sot_dataset_policy_drafts/`):

- `101_HHC_Recontact_policy.yaml`
- `18_1_TargConcom_policy.yaml`
- `18_2_TargConcom_policy.yaml`
- `20_CoEnroll_policy.yaml`
- `21_DSTISO_policy.yaml`
- `21_DSTIsolate_policy.yaml`
- `53_exposure_policy.yaml`
- `95_Specimen_Tracking_policy.yaml`

Pending HITL:
- `96_Specimen_Tracking_policy.yaml` — held in `excluded_from_sot.yaml`
  pending human disambiguation of an unnamed `Field1` column. Draft is
  at `tmp/sot_dataset_policy_drafts/96_Specimen_Tracking_policy.yaml.draft`.

Every variable in every YAML in this directory carries
`claude_drafted: true`. A human reviewer must validate every entry
before any variable's `review.state` advances from `review_required` to
`resolved`.
