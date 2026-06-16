"""Tests for the scrub-affecting input fingerprint (Wave 4 C5.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils.input_fingerprint import (
    compute_input_fingerprint,
    fingerprint_record_path,
    is_redundant_run,
    read_recorded_fingerprint,
    write_fingerprint_record,
)


def _seed_inputs(tmp_path: Path) -> tuple[Path, Path]:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    (datasets / "form2A.xlsx").write_bytes(b"raw-bytes-A")
    (datasets / "ignored.txt").write_text("not a dataset", encoding="utf-8")
    sot = tmp_path / "sot"
    sot.mkdir()
    (sot / "form2A_policy.yaml").write_text("policy: v1\n", encoding="utf-8")
    return datasets, sot


def test_fingerprint_is_deterministic(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    a = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    b = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 64  # sha256 hex


def test_fingerprint_changes_when_data_changes(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    before = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    (datasets / "form2A.xlsx").write_bytes(b"raw-bytes-A-CHANGED")
    after = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert before.fingerprint != after.fingerprint


def test_fingerprint_changes_when_sot_changes(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    before = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    (sot / "form2A_policy.yaml").write_text("policy: v2\n", encoding="utf-8")
    after = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert before.fingerprint != after.fingerprint


def test_non_dataset_file_change_is_ignored(tmp_path: Path) -> None:
    """A .txt edit in the datasets dir must NOT change the fingerprint (the
    extension filter only hashes real dataset shapes)."""
    datasets, sot = _seed_inputs(tmp_path)
    before = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    (datasets / "ignored.txt").write_text("edited but irrelevant", encoding="utf-8")
    after = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert before.fingerprint == after.fingerprint


def test_components_include_code_modules(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    fp = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert "code:scripts.security.phi_scrub" in fp.components
    assert "code:scripts.security.phi_review" in fp.components
    # The pipeline modules resolve to real on-disk files, so their hashes are non-empty.
    assert fp.components["code:scripts.security.phi_scrub"]


def test_record_roundtrip_and_redundant_detection(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    audit = tmp_path / "audit"
    fp = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    rec_path = fingerprint_record_path(audit)
    write_fingerprint_record(rec_path, fp)

    recorded = read_recorded_fingerprint(rec_path)
    assert recorded == fp.fingerprint
    assert is_redundant_run(fp, recorded) is True

    # A changed input → no longer redundant against the recorded value.
    (datasets / "form2A.xlsx").write_bytes(b"different")
    fp2 = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert is_redundant_run(fp2, recorded) is False


def test_record_is_value_free(tmp_path: Path) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    audit = tmp_path / "audit"
    fp = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    rec_path = write_fingerprint_record(fingerprint_record_path(audit), fp)
    text = rec_path.read_text(encoding="utf-8")
    # Only hashes/names — never the raw dataset bytes.
    assert "raw-bytes-A" not in text
    assert set(__import__("json").loads(text)) == {"fingerprint", "study", "components"}


def test_read_missing_record_is_none(tmp_path: Path) -> None:
    assert read_recorded_fingerprint(tmp_path / "nope.json") is None


@pytest.mark.parametrize("recorded", [None, "deadbeef"])
def test_is_redundant_run_false_on_mismatch(tmp_path: Path, recorded: str | None) -> None:
    datasets, sot = _seed_inputs(tmp_path)
    fp = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert is_redundant_run(fp, recorded) is False
