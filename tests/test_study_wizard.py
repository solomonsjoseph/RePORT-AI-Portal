"""Tests for the N11 guided study-setup wizard's pure core (no terminal I/O)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import config


def _wizard():
    # The skill module is importable as a path module; load it via importlib from
    # its file so the test does not depend on the dotted package path resolving.
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "plugins/report-ai-study-pipeline/skills/study-setup/scripts/wizard.py"
    )
    spec = importlib.util.spec_from_file_location("study_setup_wizard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def setup_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    return tmp_path


def _make_datasets(tmp_path, study, names):
    d = tmp_path / "raw" / study / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("x", encoding="utf-8")


def test_discover_datasets_ignores_lock_and_nondataset(setup_paths):
    w = _wizard()
    _make_datasets(setup_paths, "S", ["1_a.xlsx", "2_b.csv", "~$tmp.xlsx", "notes.txt"])
    assert w.discover_datasets("S") == ["1_a.xlsx", "2_b.csv"]


def test_available_choices(setup_paths):
    w = _wizard()
    assert set(w.available_jurisdictions()) >= {"USA", "INDIA"}
    assert "safe_harbor" in w.available_postures()


def test_suggest_rejects_normalized_duplicate_and_final(setup_paths):
    w = _wizard()
    sug = w.suggest_form_classification(
        ["14_CaseControl.xlsx", "14_Case_Control.xlsx", "1_enroll.xlsx", "data_final.xlsx"]
    )
    assert sug["14_CaseControl.xlsx"] == "required"
    assert sug["14_Case_Control.xlsx"] == "reject"  # normalized dup
    assert sug["data_final.xlsx"] == "reject"  # superseded-copy suffix
    assert sug["1_enroll.xlsx"] == "required"


def test_validate_privacy_inputs(setup_paths):
    w = _wizard()
    assert (
        w.validate_privacy_inputs(
            jurisdictions=["USA"], posture="safe_harbor", data_as_of="2024-12-31"
        )
        == []
    )
    errs = w.validate_privacy_inputs(
        jurisdictions=["XYZ"], posture="bogus", data_as_of="not-a-date"
    )
    assert len(errs) == 3


def test_validate_manifest_inputs_overlap_and_missing(setup_paths):
    w = _wizard()
    _make_datasets(setup_paths, "S", ["a.xlsx", "b.xlsx"])
    # 'a' in two buckets, 'ghost' missing, 'b' unclassified
    errs = w.validate_manifest_inputs(
        "S", required=["a.xlsx", "ghost.xlsx"], optional=["a.xlsx"], reject=[]
    )
    joined = " ".join(errs)
    assert "both" in joined and "not found" in joined and "unclassified" in joined


def test_build_configs_shapes(setup_paths):
    w = _wizard()
    priv = w.build_privacy_config(jurisdictions=["usa"], data_as_of="2024-12-31")
    assert priv["jurisdictions"] == ["USA"]
    assert priv["data_as_of"] == "2024-12-31"
    # N11 posture fix: compliance_posture is NOT written into _study_privacy.yaml
    # (the scrub reads it from phi_scrub.yaml).
    assert "compliance_posture" not in priv
    man = w.build_forms_manifest(required=["b.xlsx", "a.xlsx"], optional=[], reject=["j.xlsx"])
    assert man["required"] == ["a.xlsx", "b.xlsx"]  # sorted


def test_write_scrub_override_pins_posture(setup_paths):
    w = _wizard()
    path = w.write_scrub_override("S", "limited_dataset")
    cfg = yaml.safe_load(path.read_text())
    assert cfg["compliance_posture"] == "limited_dataset"
    assert path.name == "phi_scrub.yaml"
    with pytest.raises(FileExistsError):
        w.write_scrub_override("S", "safe_harbor")
    w.write_scrub_override("S", "safe_harbor", force=True)  # force overwrites


def test_write_configs_refuses_overwrite_without_force(setup_paths):
    w = _wizard()
    priv = w.build_privacy_config(jurisdictions=["USA"])
    man = w.build_forms_manifest(required=["a.xlsx"], optional=[], reject=[])
    p1, p2 = w.write_configs("S", priv, man)
    assert p1.is_file() and p2.is_file()
    with pytest.raises(FileExistsError):
        w.write_configs("S", priv, man)
    w.write_configs("S", priv, man, force=True)  # force overwrites


def test_run_interactive_writes_posture_to_scrub_config(setup_paths):
    w = _wizard()
    _make_datasets(setup_paths, "S", ["1_enroll.xlsx", "2_base.xlsx"])
    answers = iter(["USA", "", "2024-12-31", "R", "R"])  # juris, posture(default), date, two files
    p1, p2 = w.run_interactive(
        "S", input_fn=lambda _prompt: next(answers), print_fn=lambda *_a, **_k: None, force=True
    )
    priv = yaml.safe_load(p1.read_text())
    man = yaml.safe_load(p2.read_text())
    assert priv["jurisdictions"] == ["USA"]
    assert "compliance_posture" not in priv  # NOT here anymore
    assert man["required"] == ["1_enroll.xlsx", "2_base.xlsx"]
    # The posture is written where the scrub engine reads it (phi_scrub.yaml).
    import config

    scrub_cfg = yaml.safe_load(
        Path(config.study_config_path("phi_scrub.yaml", study="S")).read_text()
    )
    assert scrub_cfg["compliance_posture"] == "safe_harbor"


def test_run_interactive_accept_reject_default(setup_paths):
    # Re-audit regression: pressing ENTER on a file SUGGESTED 'reject' must honor
    # the suggestion (reject), not flip it to required via sug[:1]=='r'.
    w = _wizard()
    _make_datasets(setup_paths, "S", ["1_enroll.xlsx", "data_final.xlsx"])
    assert w.suggest_form_classification(["data_final.xlsx"])["data_final.xlsx"] == "reject"
    answers = iter(["USA", "", "", "", ""])  # juris, posture, date(skip), then ENTER for both files
    _p1, p2 = w.run_interactive(
        "S", input_fn=lambda _prompt: next(answers), print_fn=lambda *_a, **_k: None, force=True
    )
    man = yaml.safe_load(p2.read_text())
    assert man["reject"] == ["data_final.xlsx"]  # NOT required
    assert man["required"] == ["1_enroll.xlsx"]


def test_validate_rejects_invalid_calendar_date(setup_paths):
    w = _wizard()
    errs = w.validate_privacy_inputs(
        jurisdictions=["USA"], posture="safe_harbor", data_as_of="2024-13-45"
    )
    assert any("calendar date" in e for e in errs)
