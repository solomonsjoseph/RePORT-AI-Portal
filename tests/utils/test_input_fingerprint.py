"""Tests for the scrub-affecting input fingerprint (Wave 4 C5.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils.input_fingerprint import (
    compute_input_fingerprint,
    compute_per_form_fingerprint,
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


def test_fingerprint_includes_key_and_rulebook_components(tmp_path: Path, monkeypatch) -> None:
    # Note 14: the PHI key fingerprint + rulebook version are scrub-affecting inputs,
    # so a key rotation must change the overall fingerprint (else a redundant-run
    # check would skip a study that needs re-scrubbing with the new key).
    datasets, sot = _seed_inputs(tmp_path)
    import scripts.utils.input_fingerprint as ifp

    monkeypatch.setattr(ifp, "_phi_key_fingerprint_safe", lambda: "KEYFP_OLD")
    before = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert before.components["phi_key_fingerprint"] == "KEYFP_OLD"
    assert "phi_rulebook_version" in before.components

    monkeypatch.setattr(ifp, "_phi_key_fingerprint_safe", lambda: "KEYFP_NEW")
    after = compute_input_fingerprint(study="S", datasets_dir=datasets, sot_dir=sot)
    assert after.fingerprint != before.fingerprint  # key rotation forces a re-run


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


# ── Per-form fingerprint (Note 16 — per-form input fingerprinting) ───────────


def _seed_two_forms(tmp_path: Path) -> Path:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    (datasets / "1_Enrollment.xlsx").write_bytes(b"enrollment-bytes")
    (datasets / "9_EEval.xlsx").write_bytes(b"eeval-bytes")
    return datasets


def test_per_form_fingerprint_is_deterministic(tmp_path: Path) -> None:
    datasets = _seed_two_forms(tmp_path)
    a = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    b = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_per_form_fingerprint_accepts_filename_or_stem(tmp_path: Path) -> None:
    """A dataset extension is stripped, so the stem and the filename agree."""
    datasets = _seed_two_forms(tmp_path)
    by_stem = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    by_name = compute_per_form_fingerprint("9_EEval.xlsx", study="S", datasets_dir=datasets)
    assert by_stem == by_name


def test_per_form_fingerprint_changes_when_that_form_changes(tmp_path: Path) -> None:
    datasets = _seed_two_forms(tmp_path)
    before = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    (datasets / "9_EEval.xlsx").write_bytes(b"eeval-bytes-CHANGED")
    after = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    assert before != after


def test_per_form_fingerprint_isolates_forms(tmp_path: Path) -> None:
    """Editing form B's raw bytes must NOT change form A's per-form fingerprint —
    this isolation is what lets the readback classify a single form cache-valid."""
    datasets = _seed_two_forms(tmp_path)
    a_before = compute_per_form_fingerprint("1_Enrollment", study="S", datasets_dir=datasets)
    (datasets / "9_EEval.xlsx").write_bytes(b"eeval-bytes-CHANGED")
    a_after = compute_per_form_fingerprint("1_Enrollment", study="S", datasets_dir=datasets)
    assert a_before == a_after


def test_per_form_fingerprint_missing_file_is_empty(tmp_path: Path) -> None:
    datasets = _seed_two_forms(tmp_path)
    assert compute_per_form_fingerprint("does_not_exist", study="S", datasets_dir=datasets) != ""
    # ^ shared components still hash to a stable digest; the raw_form component is
    #   "" but the canonical string is non-empty, so the result is a real hash.
    # The raw bytes never appear in the digest (it is a SHA-256):
    fp = compute_per_form_fingerprint("9_EEval", study="S", datasets_dir=datasets)
    assert "eeval-bytes" not in fp
