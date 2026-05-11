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

from scripts.source_truth.all_form_validation import (
    FORM_STATUS_FAILED,
    FORM_STATUS_PASSED,
    FORM_STATUS_WARNING,
    discover_policy_pilot_forms,
    validate_all_forms,
)
from scripts.source_truth.build import (
    BuildCoordinatorError,
    run_build,
)
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
from scripts.source_truth.concept_derivation import (
    ConceptDerivationError,
    derive_cohorts,
    derive_concept_index,
    derive_definitions,
    derive_exposures,
    derive_outcomes,
    derive_schedules,
)
from scripts.source_truth.concepts import (
    ConceptIndexError,
    build_concept_index,
    enrich_concept_index_with_schema,
)
from scripts.source_truth.dataset_schema import (
    DatasetSchemaError,
    build_dataset_schema,
    get_dataset_schema_status,
    resolve_analysis_binding,
)
from scripts.source_truth.distribution import (
    DistributionRequestError,
    run_categorical_distribution,
)
from scripts.source_truth.evidence_pack_splitter import split_catalog_artifact
from scripts.source_truth.ledgers import (
    SourceTruthLedgerError,
    build_dataset_cleanup_ledger,
    build_phi_handling_ledger,
)
from scripts.source_truth.policy_loader import (
    PolicyLoaderError,
    load_policy_yaml,
)
from scripts.source_truth.lineage import (
    LINEAGE_VERSION,
    SourceTruthLineageError,
    artifact_ref,
    build_lineage_report,
    stamp_generated_artifact,
    stamp_source_truth,
    validate_lineage_bundle,
)
from scripts.source_truth.pdf_evidence import (
    PDF_EVIDENCE_COMPLETE,
    PDF_EVIDENCE_NEEDS_HUMAN_REVIEW,
    PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT,
    PDF_EVIDENCE_NOT_EXTRACTED_YET,
    build_pdf_evidence_completeness_report,
    check_pdf_evidence_completeness,
    extract_pdf_evidence,
)
from scripts.source_truth.record import (
    SourceTruthValidationError,
    validate_record,
)
from scripts.source_truth.retrieval import (
    CatalogAnswer,
    SourceTruthRetrievalError,
    SourceTruthRetriever,
)

__all__ = [
    "DERIVATION_CATALOG",
    "DERIVATION_CLEANUP_LEDGER",
    "DERIVATION_DATASET_SCHEMA",
    "DERIVATION_PHI_LEDGER",
    "FOOTER_EXCLUSION_BOUNDARY_NOTE",
    "FORM_STATUS_FAILED",
    "FORM_STATUS_PASSED",
    "FORM_STATUS_WARNING",
    "LINEAGE_VERSION",
    "PDF_EVIDENCE_COMPLETE",
    "PDF_EVIDENCE_NEEDS_HUMAN_REVIEW",
    "PDF_EVIDENCE_NOT_EXTRACTED_YET",
    "PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT",
    "BuildCoordinatorError",
    "CatalogAnswer",
    "ConceptDerivationError",
    "ConceptIndexError",
    "DatasetSchemaError",
    "DistributionRequestError",
    "PolicyLoaderError",
    "SourceTruthBuildError",
    "SourceTruthCatalogError",
    "SourceTruthLedgerError",
    "SourceTruthLineageError",
    "SourceTruthRetrievalError",
    "SourceTruthRetriever",
    "SourceTruthValidationError",
    "artifact_ref",
    "build_catalog_artifact",
    "build_concept_index",
    "build_dataset_cleanup_ledger",
    "build_dataset_schema",
    "build_lineage_report",
    "build_pdf_evidence_completeness_report",
    "build_phi_handling_ledger",
    "build_records",
    "build_source_truth_artifact",
    "check_pdf_evidence_completeness",
    "derive_cohorts",
    "derive_concept_index",
    "derive_definitions",
    "derive_exposures",
    "derive_outcomes",
    "derive_schedules",
    "discover_policy_pilot_forms",
    "enrich_concept_index_with_schema",
    "extract_pdf_evidence",
    "get_dataset_schema_status",
    "load_policy_yaml",
    "report_completeness",
    "resolve_analysis_binding",
    "run_build",
    "run_categorical_distribution",
    "split_catalog_artifact",
    "stamp_generated_artifact",
    "stamp_source_truth",
    "validate_all_forms",
    "validate_lineage_bundle",
    "validate_record",
]
