# tests/source_truth/test_build_declared_ledgers.py
"""Tests for the new-schema declared-ledger emitters in build.py.

These tests exercise the two private functions
``_build_new_phi_declared_entries`` and
``_build_new_cleanup_declared_entries``, and verify end-to-end that
``run_build`` writes the new schema to the audit ledger files.

No real policy YAMLs are read — synthetic policy artifacts are constructed
in memory and wired through ``run_build`` via a patched ``load_policy_yaml``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scripts.source_truth.build import (
    _build_new_cleanup_declared_entries,
    _build_new_phi_declared_entries,
    run_build,
)
from scripts.source_truth.builder import (
    DERIVATION_CLEANUP_LEDGER,
    DERIVATION_PHI_LEDGER,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _phi_record(
    variable_id: str,
    *,
    action: str = "drop",
    handling_reason: str | None = "direct_identifier",
    sensitivity_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a synthetic record that targets the PHI ledger."""
    normalized: dict[str, Any] = {
        "handling_action": action,
        "analysis_queryable": False,
    }
    if handling_reason is not None:
        normalized["handling_reason"] = handling_reason
    if sensitivity_flags:
        normalized["sensitivity_flags"] = sensitivity_flags
    return {
        "variable_id": variable_id,
        "source_kind": "form_field",
        "review_state": "approved",
        "derivation_targets": [DERIVATION_PHI_LEDGER],
        "presence": {
            "dataset": {"present": True, "column": variable_id},
            "pdf": {"present": False},
            "dictionary": {"present": False},
        },
        "normalized": normalized,
        "exact_source_wording": {},
        "source_references": {},
    }


def _cleanup_record(
    variable_id: str,
    *,
    handling_reason: str | None = "system_timestamp",
) -> dict[str, Any]:
    """Build a synthetic record that targets the cleanup ledger."""
    normalized: dict[str, Any] = {
        "handling_action": "drop",
        "analysis_queryable": False,
    }
    if handling_reason is not None:
        normalized["handling_reason"] = handling_reason
    return {
        "variable_id": variable_id,
        "source_kind": "non_pdf_dataset_only",
        "review_state": "approved",
        "derivation_targets": [DERIVATION_CLEANUP_LEDGER],
        "presence": {
            "dataset": {"present": True, "column": variable_id},
            "pdf": {"present": False},
            "dictionary": {"present": False},
        },
        "normalized": normalized,
        "exact_source_wording": {},
        "source_references": {},
    }


def _catalog_record(variable_id: str) -> dict[str, Any]:
    """Build a minimal catalog-only record (not PHI, not cleanup)."""
    return {
        "variable_id": variable_id,
        "source_kind": "form_field",
        "review_state": "approved",
        "derivation_targets": ["catalog"],
        "presence": {
            "dataset": {"present": True, "column": variable_id},
            "pdf": {"present": False},
            "dictionary": {"present": False},
        },
        "normalized": {
            "label": variable_id,
            "handling_action": "keep",
            "analysis_queryable": True,
        },
        "exact_source_wording": {},
        "source_references": {},
    }


def _make_policy_artifact(
    form: str,
    records: list[dict[str, Any]],
    *,
    dataset_file: str | None = None,
    pdf_file: str | None = None,
) -> dict[str, Any]:
    """Return a minimal policy artifact in the shape load_policy_yaml produces."""
    source: dict[str, Any] = {}
    if dataset_file is not None:
        source["dataset_file"] = dataset_file
    if pdf_file is not None:
        source["pdf_file"] = pdf_file
    return {
        "artifact_type": "source_truth_policy",
        "study": "Synth",
        "schema_version": 2,
        "form": form,
        "source": source,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Unit tests for _build_new_phi_declared_entries
# ---------------------------------------------------------------------------

class TestBuildNewPhiDeclaredEntries:
    def test_phi_declared_ledger_new_schema(self) -> None:
        """Each PHI entry has all required keys of the new schema."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_phi_record("PAT_NAME", action="drop", sensitivity_flags=["name_address"])],
            dataset_file="FORM_A.xlsx",
            pdf_file="data/raw/FORM_A.pdf",
        )
        entries = _build_new_phi_declared_entries([artifact])
        assert len(entries) == 1
        entry = entries[0]
        assert set(entry.keys()) == {"form", "variable_id", "action", "rule", "rationale", "where", "count"}

    def test_phi_declared_entry_field_mapping(self) -> None:
        """Field values map correctly from record and source keys."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _phi_record(
                    "PAT_NAME",
                    action="drop",
                    handling_reason="Direct identifier; dropped per SoT policy",
                    sensitivity_flags=["name_address"],
                )
            ],
            dataset_file="FORM_A.xlsx",
            pdf_file="data/raw/FORM_A.pdf",
        )
        entries = _build_new_phi_declared_entries([artifact])
        e = entries[0]
        assert e["form"] == "FORM_A"
        assert e["variable_id"] == "PAT_NAME"
        assert e["action"] == "drop"
        assert e["rule"]["project_category"] == "name_address"
        assert e["rule"]["taxonomy"] is None
        assert e["rationale"] == "Direct identifier; dropped per SoT policy"
        assert e["where"]["dataset_file"] == "FORM_A.xlsx"
        assert e["where"]["pdf_source"] == "data/raw/FORM_A.pdf"

    def test_phi_declared_count_is_null(self) -> None:
        """count is always None in declared entries (runtime data, not declaration)."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_phi_record("SUBJID", action="pseudonymize", sensitivity_flags=["subject_identifier"])],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_phi_declared_entries([artifact])
        assert all(e["count"] is None for e in entries)

    def test_phi_declared_empty_sensitivity_flags_yields_null_category(self) -> None:
        """When no sensitivity_flags, rule.project_category is None."""
        record = _phi_record("SOME_VAR", action="review_required")
        # Remove sensitivity_flags key entirely (policy_loader omits it when empty)
        record["normalized"].pop("sensitivity_flags", None)
        artifact = _make_policy_artifact("FORM_A", [record], dataset_file="FORM_A.xlsx")
        entries = _build_new_phi_declared_entries([artifact])
        assert entries[0]["rule"]["project_category"] is None

    def test_phi_declared_filters_non_phi_records(self) -> None:
        """Records without DERIVATION_PHI_LEDGER in targets are excluded."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _phi_record("PHI_VAR", action="drop", sensitivity_flags=["name_address"]),
                _catalog_record("SAFE_VAR"),
            ],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_phi_declared_entries([artifact])
        assert len(entries) == 1
        assert entries[0]["variable_id"] == "PHI_VAR"

    def test_phi_declared_multi_form_aggregation(self) -> None:
        """Entries from multiple forms are concatenated in order."""
        artifacts = [
            _make_policy_artifact(
                "FORM_A",
                [_phi_record("VAR_A", action="drop", sensitivity_flags=["name_address"])],
                dataset_file="FORM_A.xlsx",
            ),
            _make_policy_artifact(
                "FORM_B",
                [_phi_record("VAR_B", action="pseudonymize", sensitivity_flags=["subject_identifier"])],
                dataset_file="FORM_B.xlsx",
            ),
        ]
        entries = _build_new_phi_declared_entries(artifacts)
        assert len(entries) == 2
        assert entries[0]["form"] == "FORM_A"
        assert entries[1]["form"] == "FORM_B"

    def test_phi_declared_null_rationale_when_reason_absent(self) -> None:
        """rationale is None when no handling_reason in normalized."""
        record = _phi_record("SOME_VAR", action="drop", handling_reason=None)
        artifact = _make_policy_artifact("FORM_A", [record], dataset_file="FORM_A.xlsx")
        entries = _build_new_phi_declared_entries([artifact])
        assert entries[0]["rationale"] is None


# ---------------------------------------------------------------------------
# Unit tests for _build_new_cleanup_declared_entries
# ---------------------------------------------------------------------------

class TestBuildNewCleanupDeclaredEntries:
    def test_cleanup_declared_ledger_new_schema(self) -> None:
        """Each cleanup entry has all required keys of the new schema."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("TIME_STAMP")],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert len(entries) == 1
        entry = entries[0]
        assert set(entry.keys()) == {"form", "variable_id", "action", "rule", "rationale", "where", "count"}

    def test_cleanup_action_always_dataset_column_drop(self) -> None:
        """action is always 'dataset_column_drop' regardless of normalized action."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("DUP_COL")],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert entries[0]["action"] == "dataset_column_drop"

    def test_cleanup_rule_project_category_always_cleanup(self) -> None:
        """rule.project_category is always 'cleanup'."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("TIME_STAMP")],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert entries[0]["rule"]["project_category"] == "cleanup"
        assert entries[0]["rule"]["taxonomy"] is None

    def test_cleanup_where_pdf_source_always_null(self) -> None:
        """where.pdf_source is always None for cleanup entries."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("TIME_STAMP")],
            dataset_file="FORM_A.xlsx",
            pdf_file="data/raw/FORM_A.pdf",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert entries[0]["where"]["pdf_source"] is None

    def test_cleanup_declared_count_is_null(self) -> None:
        """count is always None."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("TIME_STAMP")],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert entries[0]["count"] is None

    def test_cleanup_filters_non_cleanup_records(self) -> None:
        """Records without DERIVATION_CLEANUP_LEDGER in targets are excluded."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _cleanup_record("TIME_STAMP"),
                _catalog_record("SAFE_VAR"),
                _phi_record("PHI_VAR", action="drop"),
            ],
            dataset_file="FORM_A.xlsx",
        )
        entries = _build_new_cleanup_declared_entries([artifact])
        assert len(entries) == 1
        assert entries[0]["variable_id"] == "TIME_STAMP"


# ---------------------------------------------------------------------------
# Integration tests: run_build writes new schema to audit ledger files
# ---------------------------------------------------------------------------

def _write_policy_yaml_for_artifact(artifact: dict[str, Any], path: Path) -> None:
    """Serialize a policy artifact back to a minimal YAML that load_policy_yaml accepts."""
    import yaml

    # We side-step load_policy_yaml entirely by patching it, so the YAML
    # content just needs to pass the glob pattern "*_policy.yaml".
    path.write_text(yaml.safe_dump({"_stub": True}), encoding="utf-8")


class TestRunBuildEmitsNewSchemaLedgers:
    """Integration tests using run_build with patched load_policy_yaml."""

    def _run(
        self,
        tmp_path: Path,
        policy_artifacts: list[dict[str, Any]],
    ) -> tuple[dict, dict]:
        """Run run_build with synthetic artifacts; return (phi_ledger, cleanup_ledger)."""
        policies_dir = tmp_path / "sot"
        policies_dir.mkdir()
        output_root = tmp_path / "output"

        # Create stub YAML files so glob finds them (one per artifact).
        for art in policy_artifacts:
            stub = policies_dir / f"{art['form']}_policy.yaml"
            stub.write_text("_stub: true\n", encoding="utf-8")

        # Patch load_policy_yaml to return our synthetic artifacts in order.
        artifacts_iter = iter(policy_artifacts)

        def _fake_load(_path: Path) -> dict[str, Any]:
            return next(artifacts_iter)

        with patch("scripts.source_truth.build.load_policy_yaml", side_effect=_fake_load):
            run_build(
                study="Synth",
                policies_dir=policies_dir,
                output_root=output_root,
                column_inventory=None,
            )

        phi_path = output_root / "audit" / "phi_handling_ledger.declared.json"
        cleanup_path = output_root / "audit" / "dataset_cleanup_ledger.declared.json"
        phi = json.loads(phi_path.read_text(encoding="utf-8"))
        cleanup = json.loads(cleanup_path.read_text(encoding="utf-8"))
        return phi, cleanup

    def test_phi_declared_ledger_new_schema(self, tmp_path: Path) -> None:
        """run_build emits phi_handling_ledger.declared.json with new schema entries."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _phi_record("PAT_NAME", action="drop", sensitivity_flags=["name_address"]),
                _catalog_record("SAFE_VAR"),
            ],
            dataset_file="FORM_A.xlsx",
            pdf_file="data/raw/FORM_A.pdf",
        )
        phi, _cleanup = self._run(tmp_path, [artifact])
        assert phi["artifact_type"] == "phi_handling_ledger"
        assert phi["kind"] == "declared"
        assert isinstance(phi["entries"], list)
        assert len(phi["entries"]) == 1
        entry = phi["entries"][0]
        for key in ("form", "variable_id", "action", "rule", "rationale", "where", "count"):
            assert key in entry, f"missing key: {key}"

    def test_phi_declared_entry_field_mapping(self, tmp_path: Path) -> None:
        """Fields map correctly from synthetic artifact through run_build."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_phi_record("PAT_NAME", action="drop", sensitivity_flags=["name_address"])],
            dataset_file="FORM_A.xlsx",
            pdf_file="data/raw/FORM_A.pdf",
        )
        phi, _cleanup = self._run(tmp_path, [artifact])
        e = phi["entries"][0]
        assert e["action"] == "drop"
        assert e["rule"]["project_category"] == "name_address"
        assert e["where"]["dataset_file"] == "FORM_A.xlsx"

    def test_cleanup_declared_ledger_new_schema(self, tmp_path: Path) -> None:
        """run_build emits dataset_cleanup_ledger.declared.json with new schema entries."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _cleanup_record("TIME_STAMP"),
                _catalog_record("SAFE_VAR"),
            ],
            dataset_file="FORM_A.xlsx",
        )
        _phi, cleanup = self._run(tmp_path, [artifact])
        assert cleanup["artifact_type"] == "dataset_cleanup_ledger"
        assert cleanup["kind"] == "declared"
        assert isinstance(cleanup["entries"], list)
        assert len(cleanup["entries"]) == 1
        e = cleanup["entries"][0]
        assert e["action"] == "dataset_column_drop"
        assert e["rule"]["project_category"] == "cleanup"

    def test_phi_declared_count_is_null(self, tmp_path: Path) -> None:
        """count is null in all phi declared entries."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [
                _phi_record("VAR_1", action="pseudonymize", sensitivity_flags=["subject_identifier"]),
                _phi_record("VAR_2", action="drop", sensitivity_flags=["name_address"]),
            ],
            dataset_file="FORM_A.xlsx",
        )
        phi, _cleanup = self._run(tmp_path, [artifact])
        for entry in phi["entries"]:
            assert entry["count"] is None, f"{entry['variable_id']}: count should be null"

    def test_cleanup_declared_count_is_null(self, tmp_path: Path) -> None:
        """count is null in all cleanup declared entries."""
        artifact = _make_policy_artifact(
            "FORM_A",
            [_cleanup_record("TIME_STAMP")],
            dataset_file="FORM_A.xlsx",
        )
        _phi, cleanup = self._run(tmp_path, [artifact])
        for entry in cleanup["entries"]:
            assert entry["count"] is None
