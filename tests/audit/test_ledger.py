from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit.ledger import LedgerWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_writer(tmp_path: Path, **kwargs) -> LedgerWriter:
    return LedgerWriter(output_path=tmp_path / "ledger.json", **kwargs)


def _phi_event_kwargs() -> dict:
    return {
        "form": "1A_ICScreening",
        "variable_id": "PATIENT_NAME",
        "action": "drop",
        "rule_taxonomy": "hipaa_safe_harbor:1_names",
        "rule_project_category": "name_address",
        "rationale": "Direct identifier (name); SoT-declared drop",
        "dataset_file": "1A_ICScreening.xlsx",
        "pdf_source": "data/raw/Indo-VAP/annotated_pdfs/1A_ICScreening.pdf",
        "count": 3,
    }


def _cleanup_event_kwargs() -> dict:
    return {
        "form": "1A_ICScreening",
        "variable_id": "DUP_COL_1",
        "action": "dataset_column_drop",
        "rule_project_category": "cleanup",
        "rationale": "100% identical to 'DUP_COL'",
        "dataset_file": "1A_ICScreening.xlsx",
        "count": None,
    }


# ---------------------------------------------------------------------------
# Test 1: add_phi_event + flush → file exists, envelope keys, event shape
# ---------------------------------------------------------------------------


def test_phi_event_flush_file_and_shape(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.add_phi_event(**_phi_event_kwargs())
    writer.flush()

    out = tmp_path / "ledger.json"
    assert out.exists()
    data = json.loads(out.read_text())

    assert "run_id" in data
    assert "iso_timestamp" in data
    assert "scrub_config_hash" in data
    assert "input_dataset_hash" in data
    assert "events" in data

    event = data["events"][0]
    assert event["form"] == "1A_ICScreening"
    assert event["variable_id"] == "PATIENT_NAME"
    assert event["action"] == "drop"
    assert event["rule"]["taxonomy"] == "hipaa_safe_harbor:1_names"
    assert event["rule"]["project_category"] == "name_address"
    assert event["rationale"] == "Direct identifier (name); SoT-declared drop"
    assert event["where"]["dataset_file"] == "1A_ICScreening.xlsx"
    assert event["where"]["pdf_source"] == "data/raw/Indo-VAP/annotated_pdfs/1A_ICScreening.pdf"
    assert event["count"] == 3


# ---------------------------------------------------------------------------
# Test 2: add_cleanup_event + flush → file exists, event shape correct
# ---------------------------------------------------------------------------


def test_cleanup_event_flush_file_and_shape(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.add_cleanup_event(**_cleanup_event_kwargs())
    writer.flush()

    out = tmp_path / "ledger.json"
    assert out.exists()
    data = json.loads(out.read_text())

    event = data["events"][0]
    assert event["form"] == "1A_ICScreening"
    assert event["variable_id"] == "DUP_COL_1"
    assert event["action"] == "dataset_column_drop"
    assert event["rule"]["taxonomy"] is None
    assert event["rule"]["project_category"] == "cleanup"
    assert event["where"]["pdf_source"] is None
    assert event["count"] is None


# ---------------------------------------------------------------------------
# Test 3: multiple events accumulate; event_count() increments
# ---------------------------------------------------------------------------


def test_multiple_events_accumulate(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    assert writer.event_count() == 0

    writer.add_phi_event(**_phi_event_kwargs())
    assert writer.event_count() == 1

    writer.add_cleanup_event(**_cleanup_event_kwargs())
    assert writer.event_count() == 2

    writer.flush()
    data = json.loads((tmp_path / "ledger.json").read_text())
    assert len(data["events"]) == 2


# ---------------------------------------------------------------------------
# Test 4: flush() is idempotent — call twice, same content
# ---------------------------------------------------------------------------


def test_flush_idempotent(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.add_phi_event(**_phi_event_kwargs())

    writer.flush()
    first = (tmp_path / "ledger.json").read_text()

    writer.flush()
    second = (tmp_path / "ledger.json").read_text()

    assert first == second


# ---------------------------------------------------------------------------
# Test 5: unknown PHI action raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_phi_action_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _phi_event_kwargs()
    kwargs["action"] = "not_a_real_action"
    with pytest.raises(ValueError, match="Unknown action"):
        writer.add_phi_event(**kwargs)


# ---------------------------------------------------------------------------
# Test 6: unknown cleanup action raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_cleanup_action_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _cleanup_event_kwargs()
    kwargs["action"] = "not_a_real_action"
    with pytest.raises(ValueError, match="Unknown action"):
        writer.add_cleanup_event(**kwargs)


# ---------------------------------------------------------------------------
# Test 7: empty form raises ValueError
# ---------------------------------------------------------------------------


def test_empty_form_phi_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _phi_event_kwargs()
    kwargs["form"] = ""
    with pytest.raises(ValueError, match="form"):
        writer.add_phi_event(**kwargs)


def test_empty_form_cleanup_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _cleanup_event_kwargs()
    kwargs["form"] = ""
    with pytest.raises(ValueError, match="form"):
        writer.add_cleanup_event(**kwargs)


# ---------------------------------------------------------------------------
# Test 8: empty variable_id raises ValueError
# ---------------------------------------------------------------------------


def test_empty_variable_id_phi_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _phi_event_kwargs()
    kwargs["variable_id"] = ""
    with pytest.raises(ValueError, match="variable_id"):
        writer.add_phi_event(**kwargs)


def test_empty_variable_id_cleanup_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _cleanup_event_kwargs()
    kwargs["variable_id"] = ""
    with pytest.raises(ValueError, match="variable_id"):
        writer.add_cleanup_event(**kwargs)


# ---------------------------------------------------------------------------
# Test 9: negative count raises ValueError
# ---------------------------------------------------------------------------


def test_negative_count_phi_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _phi_event_kwargs()
    kwargs["count"] = -1
    with pytest.raises(ValueError, match="count"):
        writer.add_phi_event(**kwargs)


def test_negative_count_cleanup_raises(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    kwargs = _cleanup_event_kwargs()
    kwargs["count"] = -5
    with pytest.raises(ValueError, match="count"):
        writer.add_cleanup_event(**kwargs)


# ---------------------------------------------------------------------------
# Test 10: envelope includes all required top-level keys with correct types
# ---------------------------------------------------------------------------


def test_envelope_keys_and_types(tmp_path: Path) -> None:
    writer = LedgerWriter(
        output_path=tmp_path / "ledger.json",
        run_id="run_test123",
        scrub_config_hash="sha256:abc",
        input_dataset_hash="sha256:def",
    )
    writer.add_phi_event(**_phi_event_kwargs())
    writer.flush()

    data = json.loads((tmp_path / "ledger.json").read_text())

    assert data["run_id"] == "run_test123"
    assert isinstance(data["iso_timestamp"], str)
    # Z-suffix UTC format
    assert data["iso_timestamp"].endswith("Z")
    assert data["scrub_config_hash"] == "sha256:abc"
    assert data["input_dataset_hash"] == "sha256:def"
    assert isinstance(data["events"], list)


def test_envelope_optional_hashes_none(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.flush()

    data = json.loads((tmp_path / "ledger.json").read_text())
    assert data["scrub_config_hash"] is None
    assert data["input_dataset_hash"] is None


def test_run_id_auto_generated(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.flush()

    data = json.loads((tmp_path / "ledger.json").read_text())
    assert data["run_id"].startswith("run_")
    assert len(data["run_id"]) > 4
