"""Tests for evidence pack pruner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_sot(sot_dir: Path, forms: list[str]) -> None:
    for form in forms:
        (sot_dir / f"{form}_policy.yaml").write_text(
            f"form: {form}\npolicy_status: active\nvariables: {{}}\n"
        )


def test_pruner_keeps_per_form_deletes_per_variable(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    _make_sot(sot_dir, ["10_TST", "19_Smear"])

    packs_dir = tmp_path / "evidence_packs"
    packs_dir.mkdir()

    for name in ["10_TST", "19_Smear"]:
        (packs_dir / f"{name}.json").write_text(json.dumps({"form": name}))

    for name in ["ZN_VISIT", "SUBJID", "FID_VAR"]:
        (packs_dir / f"{name}.json").write_text(json.dumps({"variable": name}))

    from scripts.utils.evidence_pack_pruner import prune_per_variable_packs

    deleted = prune_per_variable_packs(packs_dir=packs_dir, sot_dir=sot_dir)

    assert deleted == 3
    remaining = {p.stem for p in packs_dir.glob("*.json")}
    assert remaining == {"10_TST", "19_Smear"}


def test_pruner_handles_dataset_policies_subdir(tmp_path: Path) -> None:
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    _make_sot(sot_dir, ["10_TST"])
    dp_dir = sot_dir / "dataset_policies"
    dp_dir.mkdir()
    _make_sot(dp_dir, ["101_HHC_Recontact"])

    packs_dir = tmp_path / "evidence_packs"
    packs_dir.mkdir()
    for name in ["10_TST", "101_HHC_Recontact"]:
        (packs_dir / f"{name}.json").write_text("{}")
    (packs_dir / "LEGACY_VAR.json").write_text("{}")

    from scripts.utils.evidence_pack_pruner import prune_per_variable_packs

    deleted = prune_per_variable_packs(packs_dir=packs_dir, sot_dir=sot_dir)
    assert deleted == 1
    assert (packs_dir / "10_TST.json").is_file()
    assert (packs_dir / "101_HHC_Recontact.json").is_file()
    assert not (packs_dir / "LEGACY_VAR.json").exists()
