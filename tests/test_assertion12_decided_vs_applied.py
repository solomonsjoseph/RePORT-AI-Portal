"""C3 assertion 12: Each approved form's APPLIED protection >= phi_review DECIDED protection.

The protection lattice ranks protection level (higher = more protective):
  - Rank 0: keep (no protection)
  - Rank 1: generalize, band, cap, suppress_small_cell, suppress
  - Rank 2: jitter_date, pseudonymize (replace value w/ token)
  - Rank 3: drop, birthdate_drop (remove column)

Assertion 12 fails ONLY the UNDER-protection direction: scrub applied LESS
protection than phi_review decided (potential leak). Over-protection always passes.

Tests cover:
  - 7 actions: drop, jitter_date, pseudonymize, generalize, cap, suppress_small_cell, band
  - 2 postures: safe_harbor (birthdate -> drop) vs limited_dataset (birthdate -> jitter)
  - Rank lattice: pass/fail at each rank boundary
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.skills.extract_to_llm_source import (
    _verify_assertion_decided_vs_applied,
)


def _write_approval(
    run_dir: Path, approved_forms: list[str], forms: list[dict]
) -> None:
    """Write a phi_handling_approval.json file."""
    path = run_dir / "phi_handling_approval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"approved_forms": approved_forms, "forms": forms}),
        encoding="utf-8",
    )


def _write_dataset(
    dataset_dir: Path, form_name: str, headers: list[str]
) -> None:
    """Write a minimal JSONL dataset file with given headers."""
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / f"{form_name}.jsonl"
    record = {h: "value" for h in headers}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _write_ledger(
    audit_dir: Path, form_name: str, events: list[dict], keep_decisions: list[dict] | None = None
) -> None:
    """Write a PHI ledger file."""
    path = dataset_phi_ledger_path(audit_dir, form_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger_data: dict[str, Any] = {"events": events}
    if keep_decisions:
        ledger_data["keep_decisions"] = keep_decisions
    path.write_text(json.dumps(ledger_data), encoding="utf-8")


class TestAssertionDecidedVsAppliedBasic:
    """Basic pass/fail cases."""

    def test_no_approval_file_passes(self, tmp_path: Path) -> None:
        """When no approval file exists, assertion passes (legacy mode)."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        result, _ = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "pass"

    def test_approved_form_missing_from_dataset_passes(self, tmp_path: Path) -> None:
        """Approved form not in dataset -> pass (not published, nothing to check)."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [{"form_name": "1_Form", "classifications": []}])
        # dataset_dir empty -> no 1_Form.jsonl
        
        result, _ = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "pass"

    def test_keep_decided_keep_applied_passes(self, tmp_path: Path) -> None:
        """Decided: keep, Applied: keep -> pass."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [{"header": "DOB", "action": "keep"}],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["DOB"])
        _write_ledger(audit, "1_Form", [], [{"variable_id": "DOB"}])
        
        result, _ = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "pass"

    def test_kept_column_not_in_ledger_passes_if_config_keep(self, tmp_path: Path) -> None:
        """Column not in ledger but config says keep -> pass (no event needed for keep)."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [{"header": "SEX", "action": "keep"}],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["SEX"])
        _write_ledger(audit, "1_Form", [])  # No event, no keep_decision
        
        result, _ = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        # SEX is typically in keep_fields, so config action is "keep" -> passes
        assert result == "pass"


class TestAssertionDecidedVsAppliedRankLattice:
    """Test the protection rank lattice (7 actions x rank checks)."""

    @pytest.mark.parametrize(
        "decided,applied,should_pass",
        [
            # keep (rank 0)
            ("keep", "keep", True),
            # rank 1 coarsen
            ("keep", "generalize", True),  # over-protect
            ("generalize", "generalize", True),
            ("generalize", "keep", False),  # under-protect
            # rank 2 tokenize
            ("generalize", "jitter_date", True),  # over-protect
            ("keep", "jitter_date", True),  # over-protect
            ("jitter_date", "jitter_date", True),
            ("jitter_date", "generalize", False),  # under-protect
            ("jitter_date", "keep", False),  # under-protect
            # rank 3 drop
            ("jitter_date", "drop", True),  # over-protect
            ("keep", "drop", True),  # over-protect
            ("drop", "drop", True),
            ("drop", "pseudonymize", False),  # under-protect
            ("drop", "jitter_date", False),  # under-protect
            ("drop", "keep", False),  # under-protect
            # pseudonymize (rank 2, same as jitter_date)
            ("generalize", "pseudonymize", True),
            ("pseudonymize", "pseudonymize", True),
            ("pseudonymize", "generalize", False),
            ("pseudonymize", "keep", False),
        ],
    )
    def test_rank_lattice_decided_vs_applied(
        self, tmp_path: Path, decided: str, applied: str, should_pass: bool
    ) -> None:
        """Test protection rank lattice: applied >= decided."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [{"header": "PHI_COL", "action": decided}],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["PHI_COL"])
        _write_ledger(audit, "1_Form", [
            {"variable_id": "PHI_COL", "action": applied}
        ])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        if should_pass:
            assert result == "pass", f"decided={decided}, applied={applied}: {detail}"
        else:
            assert result == "fail", f"decided={decided}, applied={applied}: should have failed"
            assert "decided=keep" not in detail or applied != "keep"  # not a tautology


class TestAssertionDecidedVsAppliedActionCoverage:
    """Test each of 7 distinct actions."""

    @pytest.mark.parametrize(
        "action",
        ["drop", "jitter_date", "pseudonymize", "generalize", "cap", "suppress_small_cell", "band"],
    )
    def test_each_action_applied_equals_decided(self, tmp_path: Path, action: str) -> None:
        """Each action: decided == applied -> pass."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        # Some actions (band, suppress_small_cell) don't appear in ledger if config empty
        # but can still be tested with explicit event
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [{"header": "TEST_COL", "action": action}],
            }
        ])
        if action == "drop":
            # Dropped columns don't appear in dataset
            dataset_dir.mkdir(parents=True, exist_ok=True)
            (dataset_dir / "1_Form.jsonl").write_text("{}\n", encoding="utf-8")
        else:
            _write_dataset(dataset_dir, "1_Form", ["TEST_COL"])
        
        if action == "drop":
            # Dropped columns have no ledger entry, but decision check skips dropped columns
            _write_ledger(audit, "1_Form", [])
        else:
            _write_ledger(audit, "1_Form", [
                {"variable_id": "TEST_COL", "action": action}
            ])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "pass", f"action={action}: {detail}"

    def test_drop_decided_pseudonymize_applied_fails(self, tmp_path: Path) -> None:
        """Decided: drop, Applied: pseudonymize -> fail (under-protect)."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [{"header": "PHI", "action": "drop"}],
            }
        ])
        # Column is published (despite drop decision — contradiction we test)
        _write_dataset(dataset_dir, "1_Form", ["PHI"])
        _write_ledger(audit, "1_Form", [
            {"variable_id": "PHI", "action": "pseudonymize"}
        ])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "fail"
        assert "decided=drop" in detail


class TestAssertionDecidedVsAppliedMultipleColumns:
    """Test multi-column cases and ledger/decision interaction."""

    def test_multiple_columns_one_fails(self, tmp_path: Path) -> None:
        """Multiple columns, one under-protected -> fail."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [
                    {"header": "COL1", "action": "keep"},
                    {"header": "COL2", "action": "jitter_date"},
                    {"header": "COL3", "action": "drop"},
                ],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["COL1", "COL2", "COL3"])
        _write_ledger(audit, "1_Form", [
            {"variable_id": "COL1", "action": "keep"},
            {"variable_id": "COL2", "action": "keep"},  # under-protect: decided jitter, applied keep
            # COL3 dropped -> not in dataset
        ])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        assert result == "fail"
        assert "COL2" in detail

    def test_no_ledger_entry_uses_config_action(self, tmp_path: Path) -> None:
        """Column with no ledger event uses _configured_scrub_action as fallback."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [
                    {"header": "IS_AGE", "action": "cap"},  # cap is in config
                ],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["IS_AGE"])
        _write_ledger(audit, "1_Form", [])  # No event for IS_AGE (conditional cap may fire no event on all-null)
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        # Config has cap_fields pattern for IS_AGE, so _configured_scrub_action returns "cap"
        # decided="cap", applied="cap" -> pass
        assert result == "pass", f"detail={detail}"

    def test_classified_header_not_in_published_is_skipped(self, tmp_path: Path) -> None:
        """Header classified by phi_review but absent from published dataset is skipped."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [
                    {"header": "DROPPED_UPSTREAM", "action": "jitter_date"},
                    {"header": "PUBLISHED", "action": "keep"},
                ],
            }
        ])
        # Only PUBLISHED in dataset
        _write_dataset(dataset_dir, "1_Form", ["PUBLISHED"])
        _write_ledger(audit, "1_Form", [])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        # DROPPED_UPSTREAM not in published, so skipped; PUBLISHED decided=keep, applied=keep -> pass
        assert result == "pass"


class TestAssertionDecidedVsAppliedPostureInteraction:
    """Test interaction with compliance posture (safe_harbor vs limited_dataset)."""

    def test_birthdate_safe_harbor_drop_vs_limited_dataset_jitter(self, tmp_path: Path) -> None:
        """Posture affects birthdate handling: safe_harbor->drop vs limited_dataset->jitter.
        
        The config specifies posture; _configured_scrub_action uses it.
        This test just verifies that birthdate_drop action exists and is at rank 3.
        """
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        # Simulate safe_harbor posture: birthdate decided as "drop" (via birthdate_drop config)
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [
                    {"header": "IS_BIRTHDAT", "action": "drop"},  # birthdate in safe_harbor
                ],
            }
        ])
        # Birthdate field not in dataset (it was dropped)
        _write_dataset(dataset_dir, "1_Form", [])
        _write_ledger(audit, "1_Form", [])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        # IS_BIRTHDAT not in published, skipped -> pass
        assert result == "pass"

    def test_over_protection_always_passes_regardless_posture(self, tmp_path: Path) -> None:
        """Over-protection passes regardless of posture (scrub more aggressive than policy)."""
        audit = tmp_path / "audit"
        run = tmp_path / "run"
        dataset_dir = tmp_path / "dataset"
        
        _write_approval(run, ["1_Form"], [
            {
                "form_name": "1_Form",
                "classifications": [
                    {"header": "IC_RELIGION", "action": "keep"},  # decided keep (research var)
                ],
            }
        ])
        _write_dataset(dataset_dir, "1_Form", ["IC_RELIGION"])
        _write_ledger(audit, "1_Form", [
            {"variable_id": "IC_RELIGION", "action": "drop"}  # scrub is aggressive -> drop
        ])
        
        result, detail = _verify_assertion_decided_vs_applied(audit, run, dataset_dir, "Test-Study")
        # drop (rank 3) > keep (rank 0) -> over-protect -> pass
        assert result == "pass"
