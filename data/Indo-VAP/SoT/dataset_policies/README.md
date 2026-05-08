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
