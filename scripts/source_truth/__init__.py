"""Study Variable Source of Truth — internal canonical metadata layer.

This package builds and validates the project-specific canonical record
where authorized source evidence (dataset headers, PDF evidence, optional
dictionary metadata) is reconciled, normalized, classified, and reviewed
before any downstream artifact (catalog, dataset schema, audit ledgers)
is generated.

The Source of Truth is the *first* layer in the four-layer architecture
described in PRD.md. It is not the LLM-facing retrieval object; it is
deliberately too detailed for runtime use and is later compiled into
compact catalog cards plus lazy-loaded evidence packs.
"""

from scripts.source_truth.builder import (
    DERIVATION_CATALOG,
    DERIVATION_CLEANUP_LEDGER,
    DERIVATION_DATASET_SCHEMA,
    DERIVATION_PHI_LEDGER,
    SourceTruthBuildError,
    build_records,
    build_source_truth_artifact,
)
from scripts.source_truth.completeness import (
    FOOTER_EXCLUSION_BOUNDARY_NOTE,
    report_completeness,
)
from scripts.source_truth.record import (
    SourceTruthValidationError,
    validate_record,
)

__all__ = [
    "DERIVATION_CATALOG",
    "DERIVATION_CLEANUP_LEDGER",
    "DERIVATION_DATASET_SCHEMA",
    "DERIVATION_PHI_LEDGER",
    "FOOTER_EXCLUSION_BOUNDARY_NOTE",
    "SourceTruthBuildError",
    "SourceTruthValidationError",
    "build_records",
    "build_source_truth_artifact",
    "report_completeness",
    "validate_record",
]
