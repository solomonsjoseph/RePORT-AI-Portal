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
from scripts.source_truth.catalog import (
    SourceTruthCatalogError,
    build_catalog_artifact,
)
from scripts.source_truth.completeness import (
    FOOTER_EXCLUSION_BOUNDARY_NOTE,
    report_completeness,
)
from scripts.source_truth.dataset_schema import (
    DatasetSchemaError,
    build_dataset_schema,
    get_dataset_schema_status,
    resolve_analysis_binding,
)
from scripts.source_truth.ledgers import (
    SourceTruthLedgerError,
    build_dataset_cleanup_ledger,
    build_phi_handling_ledger,
)
from scripts.source_truth.lineage import (
    LINEAGE_VERSION,
    SourceTruthLineageError,
    artifact_ref,
    build_lineage_report,
    derive_generation_id,
    stamp_generated_artifact,
    stamp_source_truth,
    validate_lineage_bundle,
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
    "LINEAGE_VERSION",
    "DatasetSchemaError",
    "SourceTruthBuildError",
    "SourceTruthCatalogError",
    "SourceTruthLedgerError",
    "SourceTruthLineageError",
    "SourceTruthValidationError",
    "artifact_ref",
    "build_catalog_artifact",
    "build_dataset_cleanup_ledger",
    "build_dataset_schema",
    "build_lineage_report",
    "build_phi_handling_ledger",
    "build_records",
    "build_source_truth_artifact",
    "derive_generation_id",
    "get_dataset_schema_status",
    "report_completeness",
    "resolve_analysis_binding",
    "stamp_generated_artifact",
    "stamp_source_truth",
    "validate_lineage_bundle",
    "validate_record",
]
