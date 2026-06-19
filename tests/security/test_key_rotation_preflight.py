"""Tests for the Note 12 pre-scrub key-rotation hard-stop.

`preflight_rotation_gate` must: be a no-op on first run / unchanged key; on a
fingerprint change emit a value-free rotation audit entry and HARD STOP unless
confirmed; never advance the recorded fingerprint (recording stays in
`check_and_record` post-success).
"""

from __future__ import annotations

import json

import pytest

from scripts.security.key_rotation import (
    KEY_ROTATION_EVENTS_DIRNAME,
    KeyRotationRequiresConfirmationError,
    RotationStatus,
    key_state_path,
    preflight_rotation_gate,
    record_fingerprint,
)

_FP_A = "a" * 64
_FP_B = "b" * 64


def _events(audit_dir):
    d = audit_dir / KEY_ROTATION_EVENTS_DIRNAME
    return sorted(d.glob("rotation_*.json")) if d.is_dir() else []


def test_first_run_noop(tmp_path):
    status = preflight_rotation_gate(tmp_path, _FP_A, run_id="r1", confirmed=False)
    assert status is RotationStatus.FIRST_RUN
    assert not key_state_path(tmp_path).exists()  # recording is not this gate's job
    assert _events(tmp_path) == []


def test_unchanged_noop(tmp_path):
    record_fingerprint(tmp_path, _FP_A, run_id="r0", status=RotationStatus.FIRST_RUN)
    status = preflight_rotation_gate(tmp_path, _FP_A, run_id="r1", confirmed=False)
    assert status is RotationStatus.UNCHANGED
    assert _events(tmp_path) == []


def test_rotated_unconfirmed_raises_and_audits(tmp_path):
    record_fingerprint(tmp_path, _FP_A, run_id="r0", status=RotationStatus.FIRST_RUN)
    with pytest.raises(KeyRotationRequiresConfirmationError) as exc:
        preflight_rotation_gate(tmp_path, _FP_B, run_id="r1", confirmed=False)
    # message is value-free (fingerprints are hashes; no raw key bytes)
    assert "--confirm-rotation" in str(exc.value)
    events = _events(tmp_path)
    assert len(events) == 1
    rec = json.loads(events[0].read_text())
    assert rec["previous_fingerprint"] == _FP_A
    assert rec["new_fingerprint"] == _FP_B
    assert rec["confirmed"] is False
    assert "re-scrub required" in rec["effect"]
    assert "date_utc" in rec
    # recorded state must NOT have advanced (still _FP_A)
    state = json.loads(key_state_path(tmp_path).read_text())
    assert state["fingerprint"] == _FP_A


def test_rotated_confirmed_proceeds(tmp_path):
    record_fingerprint(tmp_path, _FP_A, run_id="r0", status=RotationStatus.FIRST_RUN)
    status = preflight_rotation_gate(tmp_path, _FP_B, run_id="r1", confirmed=True)
    assert status is RotationStatus.ROTATED
    rec = json.loads(_events(tmp_path)[0].read_text())
    assert rec["confirmed"] is True


def test_audit_entry_value_free(tmp_path):
    record_fingerprint(tmp_path, _FP_A, run_id="r0", status=RotationStatus.FIRST_RUN)
    preflight_rotation_gate(tmp_path, _FP_B, run_id="r1", confirmed=True)
    rec = json.loads(_events(tmp_path)[0].read_text())
    assert set(rec) == {
        "previous_fingerprint",
        "new_fingerprint",
        "date_utc",
        "run_id",
        "confirmed",
        "effect",
    }
