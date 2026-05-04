"""Schema tests for Study Variable Source of Truth records.

These tests pin the contract that every downstream artifact (catalog,
evidence packs, dataset schema, audit ledgers) inherits from. The PRD
calls these out as the first listed schema tests; they exercise external
behaviour (record shape) and not implementation internals of any
extractor or builder.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.source_truth.record import (
    PRESENCE_SOURCES,
    REVIEW_STATE_VALUES,
    SOURCE_KIND_VALUES,
    SourceTruthValidationError,
    validate_record,
)


def _minimal_valid_record() -> dict[str, Any]:
    """Return a fresh, minimally valid SoT record fixture for each test."""
    return {
        "variable_id": "HIVTEST",
        "source_kind": "matched",
        "review_state": "auto_normalized",
        "presence": {
            "dataset": {"present": True, "column": "HIVTEST"},
            "pdf": {"present": True, "form_id": "6_HIV", "question": "HIV test result"},
            "dictionary": {"present": False},
        },
        "exact_source_wording": {
            "dataset_column": "HIVTEST",
            "pdf_question": "HIV test result",
            "pdf_options": ["Positive", "Negative", "Indeterminate"],
            "dictionary_label": None,
        },
        "normalized": {
            "label": "hiv_test_result",
            "concept": "hiv_serology_result",
            "source_defined_options": {
                "Positive": "positive",
                "Negative": "negative",
                "Indeterminate": "indeterminate",
            },
        },
    }


class TestMinimalValidRecord:
    def test_minimal_record_passes(self) -> None:
        validate_record(_minimal_valid_record())

    def test_non_mapping_rejected(self) -> None:
        with pytest.raises(SourceTruthValidationError):
            validate_record("not-a-record")  # type: ignore[arg-type]


class TestRequiredTopLevelKeys:
    @pytest.mark.parametrize(
        "missing_key",
        [
            "variable_id",
            "source_kind",
            "review_state",
            "presence",
            "exact_source_wording",
            "normalized",
        ],
    )
    def test_missing_required_key_raises(self, missing_key: str) -> None:
        record = _minimal_valid_record()
        record.pop(missing_key)
        with pytest.raises(SourceTruthValidationError, match="missing required"):
            validate_record(record)

    def test_blank_variable_id_rejected(self) -> None:
        record = _minimal_valid_record()
        record["variable_id"] = "   "
        with pytest.raises(SourceTruthValidationError, match="variable_id"):
            validate_record(record)


class TestSourceKindEnum:
    @pytest.mark.parametrize("kind", sorted(SOURCE_KIND_VALUES))
    def test_known_source_kinds_accepted(self, kind: str) -> None:
        record = _minimal_valid_record()
        record["source_kind"] = kind
        validate_record(record)

    def test_unknown_source_kind_rejected(self) -> None:
        record = _minimal_valid_record()
        record["source_kind"] = "guessed"
        with pytest.raises(SourceTruthValidationError, match="source_kind"):
            validate_record(record)


class TestReviewStateEnum:
    @pytest.mark.parametrize("state", sorted(REVIEW_STATE_VALUES))
    def test_known_review_states_accepted(self, state: str) -> None:
        record = _minimal_valid_record()
        record["review_state"] = state
        validate_record(record)

    def test_unknown_review_state_rejected(self) -> None:
        record = _minimal_valid_record()
        record["review_state"] = "approved-by-vibes"
        with pytest.raises(SourceTruthValidationError, match="review_state"):
            validate_record(record)


class TestPresenceFlagsSeparate:
    """PRD: dataset, PDF, and dictionary presence must be recorded separately."""

    @pytest.mark.parametrize("source", sorted(PRESENCE_SOURCES))
    def test_missing_source_in_presence_rejected(self, source: str) -> None:
        record = _minimal_valid_record()
        record["presence"].pop(source)
        with pytest.raises(SourceTruthValidationError, match=source):
            validate_record(record)

    def test_present_must_be_boolean(self) -> None:
        record = _minimal_valid_record()
        record["presence"]["dataset"]["present"] = "yes"
        with pytest.raises(SourceTruthValidationError, match="boolean"):
            validate_record(record)

    def test_three_independent_sources_can_disagree(self) -> None:
        # PDF-only with no dataset column and no dictionary entry is valid.
        record = _minimal_valid_record()
        record["source_kind"] = "source_only"
        record["presence"] = {
            "dataset": {"present": False},
            "pdf": {"present": True, "form_id": "6_HIV"},
            "dictionary": {"present": False},
        }
        record["exact_source_wording"]["dataset_column"] = None
        validate_record(record)


class TestExactWordingSeparate:
    """PRD: exact source wording is preserved separately from normalized labels."""

    def test_exact_wording_can_differ_from_normalized_label(self) -> None:
        record = _minimal_valid_record()
        record["exact_source_wording"]["pdf_question"] = "HIV TEST RESULT (initial)"
        record["normalized"]["label"] = "hiv_test_result"
        validate_record(record)
        assert (
            record["exact_source_wording"]["pdf_question"]
            != record["normalized"]["label"]
        )

    def test_exact_source_wording_must_be_mapping(self) -> None:
        record = _minimal_valid_record()
        record["exact_source_wording"] = "HIV test result"
        with pytest.raises(SourceTruthValidationError, match="exact_source_wording"):
            validate_record(record)

    def test_normalized_label_required(self) -> None:
        record = _minimal_valid_record()
        record["normalized"].pop("label")
        with pytest.raises(SourceTruthValidationError, match=r"normalized\.label"):
            validate_record(record)


class TestNoRawRowValues:
    """PRD: raw dataset row values must not be read or emitted into source truth."""

    def test_top_level_observed_values_rejected(self) -> None:
        record = _minimal_valid_record()
        record["observed_values"] = ["Positive", "Negative"]
        with pytest.raises(SourceTruthValidationError, match="raw dataset row values"):
            validate_record(record)

    def test_nested_row_data_rejected(self) -> None:
        record = _minimal_valid_record()
        record["normalized"]["row_data"] = {"row_1": "Positive"}
        with pytest.raises(SourceTruthValidationError, match="raw dataset row values"):
            validate_record(record)

    def test_observed_value_counts_rejected_anywhere(self) -> None:
        record = _minimal_valid_record()
        record["presence"]["dataset"]["observed_value_counts"] = {"Positive": 42}
        with pytest.raises(SourceTruthValidationError, match="raw dataset row values"):
            validate_record(record)

    def test_sample_values_in_list_rejected(self) -> None:
        record = _minimal_valid_record()
        record["normalized"]["children"] = [{"sample_values": ["x", "y"]}]
        with pytest.raises(SourceTruthValidationError, match="raw dataset row values"):
            validate_record(record)


class TestFooterAndVersionExclusion:
    """PRD: footers, version dates, creation/print/export timestamps excluded."""

    @pytest.mark.parametrize(
        "forbidden_key",
        [
            "footer_text",
            "version_date",
            "print_timestamp",
            "creation_date",
            "export_timestamp",
            "form_version_date",
            "pdf_creation_date",
        ],
    )
    def test_forbidden_artifact_version_key_rejected(self, forbidden_key: str) -> None:
        record = _minimal_valid_record()
        record["normalized"][forbidden_key] = "2024-01-15"
        with pytest.raises(
            SourceTruthValidationError, match="footer / artifact-version metadata"
        ):
            validate_record(record)

    def test_form_version_date_at_top_level_rejected(self) -> None:
        record = _minimal_valid_record()
        record["form_version_date"] = "2024-01-15"
        with pytest.raises(
            SourceTruthValidationError, match="footer / artifact-version metadata"
        ):
            validate_record(record)

    def test_form_version_string_field_is_allowed(self) -> None:
        # A semantic ``form_version`` (not a *date*) should still be allowed —
        # only the artifact-creation timestamps are forbidden.
        record = _minimal_valid_record()
        record["normalized"]["form_version"] = "2.1"
        validate_record(record)


class TestSourceDefinedVsObserved:
    """PRD: source-defined options must be kept separate from observed values."""

    def test_source_defined_options_alone_pass(self) -> None:
        record = _minimal_valid_record()
        record["normalized"]["source_defined_options"] = {"Y": "yes", "N": "no"}
        validate_record(record)

    def test_source_defined_options_paired_with_observed_rejected(self) -> None:
        record = _minimal_valid_record()
        record["normalized"]["source_defined_options"] = {"Y": "yes", "N": "no"}
        record["normalized"]["observed_values"] = ["Y", "N", "Y"]
        with pytest.raises(SourceTruthValidationError, match="source_defined_options"):
            validate_record(record)


class TestValidatorIsPure:
    """The validator must not mutate the record it inspects."""

    def test_record_unchanged_after_validation(self) -> None:
        record = _minimal_valid_record()
        snapshot = copy.deepcopy(record)
        validate_record(record)
        assert record == snapshot
