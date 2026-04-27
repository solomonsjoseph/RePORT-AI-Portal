"""Shared fixtures for the RePORT AI Portal test suite.

All fixtures use tmp_path to avoid touching real data or output directories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from scripts.ai_assistant.study_knowledge import StudyKnowledge

# ── Synthetic data helpers ──────────────────────────────────────────────────


def _fake_records(n: int = 10) -> list[dict[str, Any]]:
    """Generate synthetic JSONL records."""
    return [
        {
            "SUBJID": f"SUBJ-{i:04d}",
            "VISDAT": f"2014-07-{15 + (i % 15):02d}",
            "AGE": 25 + i * 3,
            "NAME": f"TestName{i}",
            "PHONE": f"555-000-{i:04d}",
            "RESULT": f"value_{i}",
            "SCORE": float(i * 1.5),
        }
        for i in range(n)
    ]


# ── Core fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def synthetic_jsonl_records() -> list[dict[str, Any]]:
    """10 synthetic JSONL records (fake names, dates, IDs)."""
    return _fake_records(10)


@pytest.fixture()
def trio_bundle_dir(tmp_path: Path) -> Path:
    """Temporary trio bundle tree with datasets/, dictionary/, and pdfs/ subdirs."""
    ds = tmp_path / "trio_bundle" / "datasets"
    dd = tmp_path / "trio_bundle" / "dictionary"
    pdf = tmp_path / "trio_bundle" / "pdfs"
    ds.mkdir(parents=True)
    dd.mkdir(parents=True)
    pdf.mkdir(parents=True)
    return tmp_path / "trio_bundle"


@pytest.fixture()
def monkeypatch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Patch config paths to tmp_path-based locations and return the root."""
    import config

    trio = tmp_path / "trio_bundle"
    trio.mkdir(exist_ok=True)
    (trio / "datasets").mkdir(exist_ok=True)
    (trio / "dictionary").mkdir(exist_ok=True)
    (trio / "pdfs").mkdir(exist_ok=True)
    (tmp_path / "audit").mkdir(exist_ok=True)
    (tmp_path / "agent").mkdir(exist_ok=True)

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", trio)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", trio / "datasets")
    monkeypatch.setattr(config, "DICTIONARY_JSON_OUTPUT_DIR", trio / "dictionary")
    monkeypatch.setattr(config, "PDF_EXTRACTIONS_DIR", trio / "pdfs")
    monkeypatch.setattr(config, "VARIABLES_JSON_PATH", trio / "variables.json")
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(
        config,
        "AUDIT_DATASET_REPORT_PATH",
        tmp_path / "audit" / "dataset_cleanup_report.json",
    )
    monkeypatch.setattr(
        config,
        "AUDIT_SCRUB_REPORT_PATH",
        tmp_path / "audit" / "phi_scrub_report.json",
    )
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    # Agent state tier — per-session state + restore snapshots, under
    # output/{STUDY}/agent/. Telemetry lives under audit/ (not agent/) so the
    # LLM's permitted agent/** zone stays clear of operator-audit bytes.
    agent_state = tmp_path / "agent"
    (agent_state / "analysis").mkdir(parents=True, exist_ok=True)
    (agent_state / "conversations").mkdir(parents=True, exist_ok=True)
    telemetry_dir = tmp_path / "audit" / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    # Operator-restore "named runs" tier (gitignored, agent-writable).
    # Distinct from the tracked baseline at ``snapshots/{STUDY}/`` —
    # see ``docs/sphinx/developer_guide/operations.rst`` (Trio-Bundle Snapshot Maintenance section) and ``config.STUDY_SNAPSHOTS_DIR``.
    restore_points = agent_state / "restore_points"
    restore_points.mkdir(exist_ok=True)
    # Tracked-baseline tier — under tmp_path (not the real repo) to keep
    # the test isolated from the on-disk snapshots/ directory.
    snapshots_baseline = tmp_path / "snapshots_baseline"
    snapshots_baseline.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "AGENT_STATE_DIR", agent_state)
    monkeypatch.setattr(config, "AGENT_OUTPUT_DIR", agent_state / "analysis")
    monkeypatch.setattr(config, "CONVERSATIONS_DIR", agent_state / "conversations")
    monkeypatch.setattr(config, "TELEMETRY_DIR", telemetry_dir)
    monkeypatch.setattr(config, "TELEMETRY_SINK", telemetry_dir / "events.jsonl")
    monkeypatch.setattr(config, "STUDY_RESTORE_POINTS_DIR", restore_points)
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", snapshots_baseline)

    # Patch TMP_DIR so build_variables_reference uses isolated temp locations.
    tmp_dir = tmp_path / "tmp"
    (tmp_dir / "extracted_variables").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TMP_DIR", tmp_dir)

    # Patch staging workspace paths (Task 6/7: extraction legs default here)
    staging_root = tmp_dir / config.STUDY_NAME
    staging_datasets = staging_root / "datasets"
    staging_dictionary = staging_root / "dictionary"
    staging_pdfs = staging_root / "pdfs"
    monkeypatch.setattr(config, "STUDY_STAGING_DIR", staging_root)
    monkeypatch.setattr(config, "STAGING_DATASETS_DIR", staging_datasets)
    monkeypatch.setattr(config, "STAGING_DICTIONARY_DIR", staging_dictionary)
    monkeypatch.setattr(config, "STAGING_PDFS_DIR", staging_pdfs)

    # Also patch secure_env markers so zone guards accept tmp_path-based paths
    import scripts.security.secure_env as _se

    monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(tmp_path.resolve()))
    monkeypatch.setattr(_se, "_CLEAN_MARKER", str(trio.resolve()))
    monkeypatch.setattr(_se, "_TRIO_BUNDLE_MARKER", str(trio.resolve()))
    monkeypatch.setattr(_se, "_RAW_MARKER", str((tmp_path / "raw").resolve()))
    monkeypatch.setattr(_se, "_DATA_MARKER", str((tmp_path / "data").resolve()))
    # _TMP_MARKER: staging paths live under tmp_dir (tmp_path/tmp), so assert_write_zone
    # accepts them without widening _OUTPUT_MARKER.
    monkeypatch.setattr(_se, "_TMP_MARKER", str(tmp_dir.resolve()))

    return tmp_path


@pytest.fixture()
def synthetic_excel(tmp_path: Path) -> Path:
    """Create a minimal .xlsx file with known columns/rows."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["SUBJID", "AGE", "RESULT", "VISDAT"])
    for i in range(5):
        ws.append([f"SUBJ-{i:04d}", 25 + i, f"val_{i}", f"2014-07-{15 + i}"])
    path = tmp_path / "test_data.xlsx"
    wb.save(path)
    return path


# ── JSONL helpers ──────────────────────────────────────────────────────────


@pytest.fixture()
def write_jsonl(tmp_path: Path):
    """Factory fixture: write records to a JSONL file and return the path."""

    def _write(records: list[dict[str, Any]], filename: str = "data.jsonl") -> Path:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return path

    return _write


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records to a JSONL file."""
    with path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ── Pytest markers ─────────────────────────────────────────────────────────


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (real LLM calls)")


# ── Analytical-engine fixtures ─────────────────────────────────────────────


@pytest.fixture()
def synthetic_cohort_data(monkeypatch_config: Path) -> Path:
    """Create minimal synthetic JSONL files for cohort building tests.

    Creates 50 subjects with known values covering text→binary encoding edge cases.
    Returns the data directory containing the JSONL files, placed inside the
    trio-bundle datasets zone so ``validate_agent_read`` accepts it.
    """
    import config

    ds_dir = config.TRIO_DATASETS_DIR
    ds_dir.mkdir(parents=True, exist_ok=True)

    subjects = [f"SUBJ-{i:04d}" for i in range(50)]

    # 1A_ICScreening — demographics
    screening_records = []
    for i, sid in enumerate(subjects):
        screening_records.append(
            {
                "SUBJID": sid,
                "IS_SEX": "Male" if i % 2 == 0 else "Female",
                "IS_AGE": 20 + i,
            }
        )
    _write_jsonl(ds_dir / "1A_ICScreening.jsonl", screening_records)

    # 2A_ICBaseline — predictors
    smoking_vals = [
        "Yes, current smoker (Go to 18)",
        "No (Skip to 19)",
        "Yes, former smoker (Go to 18)",
        "No, never",
        "Yes, current",
        "No",
    ]
    diabetes_vals = [
        "Yes",
        "No",
        "Yes (Go to 21a-b)",
        "Don't know",
        "Yes (Go to 18a-b)",
        "No (Skip to 25)",
    ]
    baseline_records = []
    for i, sid in enumerate(subjects):
        baseline_records.append(
            {
                "SUBJID": sid,
                "IC_SMOKHX": smoking_vals[i % len(smoking_vals)],
                "IC_DMDX": diabetes_vals[i % len(diabetes_vals)],
                "IC_ALCFRQ": i % 5,  # 0-4 ordinal
                "IC_HEIGHT": 150 + i if i % 10 != 0 else "",  # some missing
                "IC_WEIGHT": 50 + i * 0.5,
                "IC_KNEEHT": 40 + i * 0.2,  # for Chumlea estimation
            }
        )
    _write_jsonl(ds_dir / "2A_ICBaseline.jsonl", baseline_records)

    # 5_CBC — lab results (multiple records per subject for aggregation testing)
    cbc_records = []
    for i, sid in enumerate(subjects):
        cbc_records.append({"SUBJID": sid, "CBC_HGAPCT": 5.0 + i * 0.1})
        if i % 5 == 0:  # duplicate for first-per-subject test
            cbc_records.append({"SUBJID": sid, "CBC_HGAPCT": 99.0})
    _write_jsonl(ds_dir / "5_CBC.jsonl", cbc_records)

    # 98A_FOA — outcomes (make ~10% events)
    foa_records = []
    event_labels = [
        "Bacteriologic relapse",
        "Bact. relapse",
        "Clinical relapse",
        "Bacteriologic failure",
        "Bact. failure",
        "Clinical failure",
    ]
    non_event_labels = ["Bact. cure", "Completed treatment", "Treatment completed"]
    for i, sid in enumerate(subjects):
        if i < 5:  # 5 events = 10%
            foa_records.append({"SUBJID": sid, "FOA_COHAOUT": event_labels[i % len(event_labels)]})
        else:
            foa_records.append(
                {"SUBJID": sid, "FOA_COHAOUT": non_event_labels[i % len(non_event_labels)]}
            )
    _write_jsonl(ds_dir / "98A_FOA.jsonl", foa_records)

    # 1B_HCScreening — HHC demographics
    hc_screening = []
    for i, sid in enumerate(subjects):
        hc_screening.append(
            {
                "SUBJID": sid,
                "HHC_SEX": "Male" if i % 3 == 0 else "Female",
                "HHC_AGE": 18 + i,
            }
        )
    _write_jsonl(ds_dir / "1B_HCScreening.jsonl", hc_screening)

    # 2B_HCBaseline — HHC predictors
    hc_baseline = []
    for i, sid in enumerate(subjects):
        hc_baseline.append(
            {
                "SUBJID": sid,
                "HC_SMOKHX": smoking_vals[i % len(smoking_vals)],
                "HC_DMDX": diabetes_vals[i % len(diabetes_vals)],
                "HC_ALCFRQ": i % 5,
                "HC_HEIGHT": 150 + i,
                "HC_WEIGHT": 50 + i * 0.5,
                "HC_KNEEHT": 40 + i * 0.2,
            }
        )
    _write_jsonl(ds_dir / "2B_HCBaseline.jsonl", hc_baseline)

    # 98B_FOB — HHC outcomes (only 2 events for underpowered test)
    fob_records = []
    for i, sid in enumerate(subjects):
        if i < 2:
            fob_records.append({"SUBJID": sid, "FOB_COHBOUT": "Definite case"})
        else:
            fob_records.append({"SUBJID": sid, "FOB_COHBOUT": "No TB"})
    _write_jsonl(ds_dir / "98B_FOB.jsonl", fob_records)

    # 12B_FUB — HHC follow-up (1 additional event via FUB_TBDIAG=1)
    fub_records = []
    for i, sid in enumerate(subjects):
        fub_records.append({"SUBJID": sid, "FUB_TBDIAG": 1 if i == 2 else 0})
    _write_jsonl(ds_dir / "12B_FUB.jsonl", fub_records)

    return ds_dir


@pytest.fixture()
def study_knowledge_fixture() -> StudyKnowledge:
    """Load the real study_knowledge.yaml for integration tests."""
    from scripts.ai_assistant.study_knowledge import StudyKnowledge

    return StudyKnowledge()


@pytest.fixture()
def analysis_output_dir(monkeypatch_config: Path) -> Path:
    """Temporary directory for analysis output files, inside the agent zone.

    Depending on ``monkeypatch_config`` ensures the path sits under
    ``config.AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.
    """
    import config

    out = config.AGENT_OUTPUT_DIR / "test_output"
    out.mkdir(parents=True, exist_ok=True)
    return out
