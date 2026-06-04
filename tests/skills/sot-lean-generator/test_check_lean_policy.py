"""Tests for check_lean_policy duplicate header validation helpers.

This file tests the new _duplicate_header_errors helper and
DUPLICATE_HEADER_DISCREPANCY_KINDS constant that are part of the T1.1 fix
(discrepancy-kind drift between generator and checker).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# Import the check_lean_policy module from the scripts directory.
# The module is a script, not a package, so we use importlib.
REPO = Path(__file__).resolve().parents[3]
_checker_path = (
    REPO
    / "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/check_lean_policy.py"
)
spec = importlib.util.spec_from_file_location("check_lean_policy", _checker_path)
assert spec is not None and spec.loader is not None, f"Failed to load {_checker_path}"
check_lean_policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_lean_policy)


class TestDuplicateHeaderDiscrepancyKindsConstant:
    """Test that DUPLICATE_HEADER_DISCREPANCY_KINDS constant exists and contains both kinds."""

    def test_duplicate_header_discrepancy_kinds_constant(self) -> None:
        """Assert both kind strings are members of DUPLICATE_HEADER_DISCREPANCY_KINDS."""
        assert hasattr(
            check_lean_policy, "DUPLICATE_HEADER_DISCREPANCY_KINDS"
        ), "DUPLICATE_HEADER_DISCREPANCY_KINDS constant must exist"

        kinds = check_lean_policy.DUPLICATE_HEADER_DISCREPANCY_KINDS
        assert isinstance(kinds, (list, tuple)), "DUPLICATE_HEADER_DISCREPANCY_KINDS must be iterable"

        assert (
            "dataset_duplicate_header_combined_binding" in kinds
        ), "Must accept dataset_duplicate_header_combined_binding kind"
        assert (
            "dataset_duplicate_header_binding_conflict" in kinds
        ), "Must accept dataset_duplicate_header_binding_conflict kind"


class TestDuplicateHeaderErrorsHelper:
    """Test the new _duplicate_header_errors(variables, headers, policy) helper."""

    def test_duplicate_header_with_binding_conflict_passes(self) -> None:
        """No errors when headers are deduped and policy has binding_conflict discrepancy."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B", "B"]  # B duplicated
        policy = {
            "discrepancies": [
                {"kind": "dataset_duplicate_header_binding_conflict", "note": "human reviewed"}
            ]
        }

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert errors == [], f"Expected no errors with binding_conflict, got: {errors}"

    def test_duplicate_header_with_combined_binding_passes(self) -> None:
        """No errors when headers are deduped and policy has combined_binding discrepancy."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B", "B"]  # B duplicated
        policy = {
            "discrepancies": [
                {"kind": "dataset_duplicate_header_combined_binding", "note": "human combined"}
            ]
        }

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert errors == [], f"Expected no errors with combined_binding, got: {errors}"

    def test_duplicate_header_without_any_discrepancy_fails(self) -> None:
        """Fails when policy has duplicate headers but NO accepted discrepancy kind."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B", "B"]  # B duplicated
        policy = {"discrepancies": []}

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert len(errors) > 0, "Must error when duplicate headers exist but no discrepancy"
        error_text = " ".join(errors)
        assert (
            "duplicate binding names" in error_text
        ), f"Error must mention 'duplicate binding names', got: {error_text}"

    def test_duplicate_header_without_discrepancy_key_fails(self) -> None:
        """Fails when policy is missing discrepancies key entirely."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B", "B"]  # B duplicated
        policy = {}

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert len(errors) > 0, "Must error when duplicate headers but no discrepancies key"
        error_text = " ".join(errors)
        assert "duplicate binding names" in error_text

    def test_duplicate_header_keys_not_deduped_fails(self) -> None:
        """Fails when variable keys don't match deduplicated header order."""
        variables = {"A": {}, "B": {}, "B_2": {}}
        headers = ["A", "B", "B"]  # Deduplicated order is ["A", "B"]
        policy = {
            "discrepancies": [
                {"kind": "dataset_duplicate_header_binding_conflict"}
            ]
        }

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert len(errors) > 0, "Must error when variable keys don't match deduplicated headers"
        error_text = " ".join(errors)
        assert (
            "do not match de-duplicated row-1 headers" in error_text
        ), f"Error must mention de-duplicated mismatch, got: {error_text}"

    def test_duplicate_header_with_wrong_discrepancy_kind_fails(self) -> None:
        """Fails when the discrepancy kind is neither of the accepted kinds."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B", "B"]  # B duplicated
        policy = {
            "discrepancies": [
                {"kind": "some_other_discrepancy_kind"}
            ]
        }

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert len(errors) > 0, "Must error when discrepancy kind is not accepted"
        error_text = " ".join(errors)
        assert "duplicate binding names" in error_text

    def test_no_duplicates_no_errors(self) -> None:
        """Returns empty list when headers are not duplicated."""
        variables = {"A": {}, "B": {}}
        headers = ["A", "B"]  # No duplicates
        policy = {"discrepancies": []}

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert errors == [], f"Expected no errors when no duplicates, got: {errors}"

    def test_multiple_duplicates_with_valid_discrepancy(self) -> None:
        """Handles multiple duplicated headers with valid discrepancy."""
        variables = {"A": {}, "B": {}, "C": {}}
        headers = ["A", "B", "B", "C", "C"]  # B and C duplicated
        policy = {
            "discrepancies": [
                {"kind": "dataset_duplicate_header_binding_conflict"}
            ]
        }

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert errors == [], f"Expected no errors with multiple dupes and valid discrepancy"

    def test_keys_not_deduped_but_no_duplicates_in_headers(self) -> None:
        """Fails when variable keys don't match even though headers have no dups."""
        variables = {"A": {}, "B": {}, "C": {}}
        headers = ["A", "B"]  # No duplicates, but keys don't match
        policy = {"discrepancies": []}

        errors = check_lean_policy._duplicate_header_errors(variables, headers, policy)
        assert len(errors) > 0, "Must error when keys don't match headers"
        error_text = " ".join(errors)
        assert "do not match de-duplicated row-1 headers" in error_text
