"""Study Variable Source of Truth — internal canonical metadata layer.

This package builds and validates the project-specific canonical record
where authorized source evidence (dataset headers, PDF evidence, optional
dictionary metadata) is reconciled, normalized, classified, and reviewed
before any downstream artifact is generated.

The Source of Truth is the *first* layer in the four-layer architecture
described in PRD.md. It is not the LLM-facing retrieval object; it is
deliberately too detailed for runtime use.

Phase 6 refactor: the 32-module pipeline has been replaced by the
deterministic ``study_intake`` CLI. See AGENTS.md §"SoT creation" and
``docs/runbook_sot_build.md``.
"""

from scripts.source_truth.pdf_evidence import (
    PDF_EVIDENCE_COMPLETE,
    PDF_EVIDENCE_NEEDS_HUMAN_REVIEW,
    PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT,
    PDF_EVIDENCE_NOT_EXTRACTED_YET,
    build_pdf_evidence_completeness_report,
    check_pdf_evidence_completeness,
    extract_pdf_evidence,
)
from scripts.source_truth.policy_loader import (
    DuplicateFormNameError,
    PolicyLoaderError,
    iter_policy_paths,
    load_policy_yaml,
    validate_unique_form_names,
)
from scripts.source_truth.record import (
    SourceTruthValidationError,
    validate_record,
)
from scripts.source_truth.study_intake import (
    main as study_intake_main,
    run_intake,
)

__all__ = [
    # pdf_evidence
    "PDF_EVIDENCE_COMPLETE",
    "PDF_EVIDENCE_NEEDS_HUMAN_REVIEW",
    "PDF_EVIDENCE_NOT_EXTRACTED_YET",
    "PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT",
    "build_pdf_evidence_completeness_report",
    "check_pdf_evidence_completeness",
    "extract_pdf_evidence",
    # policy_loader
    "DuplicateFormNameError",
    "PolicyLoaderError",
    "iter_policy_paths",
    "load_policy_yaml",
    "validate_unique_form_names",
    # record
    "SourceTruthValidationError",
    "validate_record",
    # study_intake
    "run_intake",
    "study_intake_main",
]
