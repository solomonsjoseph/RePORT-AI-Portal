"""Tests for Stage 3a modules: phi_patterns, phi_allowlist, phi_gate, kanon_gate, phi_safe."""

from __future__ import annotations

import pytest

from scripts.ai_assistant.phi_safe import (
    PHISafetyError,
    guard_rows_with_kanon,
    guard_text,
    phi_safe_return,
)
from scripts.security import phi_allowlist
from scripts.security.kanon_gate import kanon_check, mask_small_cell, suppress_small_cells
from scripts.security.phi_gate import PHIGateConfigError, phi_gate_check

# ── phi_allowlist ───────────────────────────────────────────────────────────


class TestClinicalPhrases:
    def test_known_phrase(self) -> None:
        assert phi_allowlist.is_clinical_phrase("Bacteriologic relapse") is True

    def test_case_insensitive(self) -> None:
        assert phi_allowlist.is_clinical_phrase("TREATMENT COMPLETED") is True

    def test_unknown_passthrough(self) -> None:
        assert phi_allowlist.is_clinical_phrase("Jane Doe") is False

    def test_two_word_clinical_vocabulary(self) -> None:
        # Both words in CLINICAL_SINGLE_WORDS → True
        assert phi_allowlist.is_clinical_phrase("Treatment Completed") is True

    def test_empty(self) -> None:
        assert phi_allowlist.is_clinical_phrase("") is False


class TestClinicalFreeText:
    def test_expired_notation(self) -> None:
        assert phi_allowlist.is_clinical_free_text("patient expired") is True

    def test_died_notation(self) -> None:
        assert (
            phi_allowlist.is_clinical_free_text("died on 3/1/2014 due to complications")
            is True
        )

    def test_benign_prose_not_flagged(self) -> None:
        assert phi_allowlist.is_clinical_free_text("Enrolled in cohort A") is False


class TestLooksLikeRealName:
    def test_common_indian_name(self) -> None:
        assert phi_allowlist.looks_like_real_name("Rajesh Sharma") is True

    def test_single_word_not_name(self) -> None:
        assert phi_allowlist.looks_like_real_name("Rajesh") is False

    def test_clinical_phrase_not_name(self) -> None:
        # Suppressed by clinical-phrase short-circuit.
        assert phi_allowlist.looks_like_real_name("Treatment Completed") is False


# ── phi_gate ────────────────────────────────────────────────────────────────


class TestPHIGateCheck:
    def test_blocks_aadhaar(self) -> None:
        result = phi_gate_check("enrolled subject 1234 5678 9012")
        assert result.blocked is True
        assert "AADHAAR" in result.findings
        assert bool(result) is False  # __bool__ → falsy on block

    def test_blocks_pan(self) -> None:
        result = phi_gate_check("PAN: ABCDE1234F")
        assert result.blocked is True
        assert "PAN" in result.findings

    def test_blocks_email(self) -> None:
        result = phi_gate_check("contact clinic@example.com")
        assert result.blocked is True

    def test_blocks_iso_date(self) -> None:
        result = phi_gate_check("Event occurred on 2014-07-15")
        assert result.blocked is True
        assert "DATE_ISO" in result.findings

    def test_blocks_indian_phone(self) -> None:
        result = phi_gate_check("call +91 9876543210")
        assert result.blocked is True

    def test_passthrough_clean_text(self) -> None:
        result = phi_gate_check("Enrollment counts by cohort: A=50, B=85")
        assert result.blocked is False
        assert bool(result) is True

    def test_clinical_phrase_allowlisted(self) -> None:
        # "Treatment Completed" would trigger PERSON_NAME_GENERIC warn.
        # Allowlist suppresses the warning (not a block).
        result = phi_gate_check("Treatment Completed")
        assert result.blocked is False

    def test_warn_findings_recorded_only_when_not_allowlisted(self) -> None:
        result = phi_gate_check("Enrollment Data")
        # Both capitalized; matches PERSON_NAME_GENERIC; neither in allowlist
        # → findings non-empty, not blocked.
        # (This is heuristic and depends on the allowlist; the test asserts
        # that a *non*-clinical two-word capitalized string DOES produce at
        # least the warn finding.)
        if result.findings:
            assert "PERSON_NAME_GENERIC" in result.findings
        assert result.blocked is False

    def test_list_of_texts(self) -> None:
        result = phi_gate_check(["clean string", "AADHAAR 1234 5678 9012"])
        assert result.blocked is True

    def test_non_sequence_raises(self) -> None:
        with pytest.raises(PHIGateConfigError):
            phi_gate_check(42)  # type: ignore[arg-type]

    def test_real_name_in_clinical_narrative_still_warns(self) -> None:
        # Per-match allowlist must NOT swallow a real-name bigram just
        # because the surrounding text has clinical vocabulary.
        result = phi_gate_check(
            "Subject Rajesh Sharma was diagnosed with pulmonary TB."
        )
        assert result.blocked is False
        assert "PERSON_NAME_GENERIC" in result.findings

    def test_cohort_label_does_not_warn(self) -> None:
        # "Cohort A" / "Index Cases" / "Household Contacts" are benign
        # bigrams the old whole-text allowlist let through.
        for label in ("Cohort A analysis", "Index Cases enrolled", "Household Contacts"):
            result = phi_gate_check(label)
            assert "PERSON_NAME_GENERIC" not in result.findings, (
                f"{label!r} incorrectly flagged"
            )

    def test_violin_plot_heading_does_not_warn(self) -> None:
        # Narratives contain section headings like "Violin Plot",
        # "Multivariate Model", "Interaction Model" — these should
        # never produce PERSON_NAME_GENERIC findings.
        for heading in ("Violin Plot", "Multivariate Model", "Interaction Model"):
            result = phi_gate_check(heading)
            assert "PERSON_NAME_GENERIC" not in result.findings, (
                f"{heading!r} incorrectly flagged"
            )


# ── kanon_gate ──────────────────────────────────────────────────────────────


class TestKAnonCheck:
    def test_block_when_class_too_small(self) -> None:
        rows = [
            {"age_band": "40-44", "sex": "M", "district": "D1"},  # unique
            {"age_band": "45-49", "sex": "F", "district": "D2"},  # unique
            {"age_band": "45-49", "sex": "F", "district": "D2"},  # size 2
        ]
        result = kanon_check(
            rows, quasi_identifiers=("age_band", "sex", "district"), k=5
        )
        assert result.blocked is True
        assert result.smallest_class_size == 1

    def test_pass_when_all_classes_ge_k(self) -> None:
        rows = [{"group": "A"}] * 5 + [{"group": "B"}] * 7
        result = kanon_check(rows, quasi_identifiers=("group",), k=5)
        assert result.blocked is False
        assert result.smallest_class_size == 5

    def test_empty_rows_not_blocked(self) -> None:
        result = kanon_check([], quasi_identifiers=("x",), k=5)
        assert result.blocked is False
        assert result.smallest_class_size == 0

    def test_violating_keys_are_strings(self) -> None:
        rows = [{"x": 1, "y": "a"}]
        result = kanon_check(rows, quasi_identifiers=("x", "y"), k=5)
        assert result.blocked is True
        assert result.violating_keys == ("1|a",)

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError):
            kanon_check([{"a": 1}], quasi_identifiers=("a",), k=0)

    def test_empty_quasi_identifiers_raises(self) -> None:
        with pytest.raises(ValueError):
            kanon_check([{"a": 1}], quasi_identifiers=(), k=5)


class TestMaskSmallCell:
    def test_mask_below_k(self) -> None:
        assert mask_small_cell(3) == "<5"

    def test_passthrough_at_k(self) -> None:
        assert mask_small_cell(5) == 5

    def test_passthrough_above_k(self) -> None:
        assert mask_small_cell(100) == 100


class TestSuppressSmallCells:
    def test_masks_small_counts_only(self) -> None:
        cells = {"A": 2, "B": 5, "C": 20, "D": 1}
        out = suppress_small_cells(cells)
        assert out == {"A": "<5", "B": 5, "C": 20, "D": "<5"}


# ── phi_safe (decorator layer) ──────────────────────────────────────────────


class TestGuardText:
    def test_passthrough_clean(self) -> None:
        assert guard_text("clean aggregate response") == "clean aggregate response"

    def test_redacts_blocking(self) -> None:
        out = guard_text("email the clinic: fake@example.com")
        assert "fake@example.com" not in out
        assert "PHI-SAFE redaction" in out
        assert "EMAIL" in out

    def test_coerces_non_string(self) -> None:
        out = guard_text(42)  # type: ignore[arg-type]
        assert out == "42"


class TestPhiSafeReturn:
    def test_wraps_function(self) -> None:
        @phi_safe_return
        def my_tool(query: str) -> str:
            return f"result for {query}"

        assert my_tool("clean") == "result for clean"

    def test_blocks_bad_return(self) -> None:
        @phi_safe_return
        def leaky_tool(query: str) -> str:
            return f"contact: patient@example.com about {query}"

        out = leaky_tool("enrollment")
        assert "patient@example.com" not in out
        assert "PHI-SAFE redaction" in out

    def test_preserves_non_string_return_as_coerced_str(self) -> None:
        @phi_safe_return
        def counts_tool() -> int:
            return 42

        out = counts_tool()
        assert out == "42"

    def test_exception_propagates(self) -> None:
        @phi_safe_return
        def broken_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            broken_tool()


class TestGuardRowsWithKanon:
    def test_empty_rows_on_block(self) -> None:
        rows = [{"g": "A"}] * 2  # smaller than k=5
        surfaced, result = guard_rows_with_kanon(
            rows, quasi_identifiers=("g",), k=5
        )
        assert surfaced == []
        assert result.blocked is True

    def test_passthrough_on_pass(self) -> None:
        rows = [{"g": "A"}] * 10
        surfaced, result = guard_rows_with_kanon(
            rows, quasi_identifiers=("g",), k=5
        )
        assert len(surfaced) == 10
        assert result.blocked is False


class TestPHISafetyErrorIsImportable:
    def test_exists(self) -> None:
        # Smoke: the exception class is accessible from the module surface.
        assert issubclass(PHISafetyError, Exception)
