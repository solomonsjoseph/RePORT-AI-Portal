"""Tests for the PHI key store + rotation detection (Wave 3 C1).

Covers the four contract properties the plan calls out for ``PHIKeyStore``
(role-gate / zeroize / fingerprint / singleton) plus the rotation detector,
including the migration-safety property (risk #4): the FIRST run a study ever
sees a recorded fingerprint must classify as ``FIRST_RUN``, never ``ROTATED``.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import pytest

import config
from scripts.security import key_rotation, phi_keystore
from scripts.security.phi_keystore import PHIKeyStore
from scripts.security.phi_scrub import PHIKeyAccessDeniedError, load_key


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Isolate the process-global singleton between tests."""
    PHIKeyStore.reset_singleton()
    yield
    PHIKeyStore.reset_singleton()


# ── PHIKeyStore ───────────────────────────────────────────────────────────────
class TestPHIKeyStore:
    def test_get_key_matches_loader(self, sidecar_key: Path) -> None:
        assert phi_keystore.get_phi_key() == load_key()

    def test_fingerprint_is_historical_compatible(self, sidecar_key: Path) -> None:
        """fingerprint() must byte-match sha256(load_key()).hexdigest() (risk #4)."""
        historical = hashlib.sha256(load_key()).hexdigest()
        assert phi_keystore.phi_key_fingerprint() == historical

    def test_singleton_identity_and_cache(self, sidecar_key: Path) -> None:
        assert PHIKeyStore.instance() is PHIKeyStore.instance()
        # Repeated reads return equal bytes (served from the cached master).
        assert phi_keystore.get_phi_key() == phi_keystore.get_phi_key()

    def test_returned_bytes_are_independent_copies(self, sidecar_key: Path) -> None:
        """Callers get immutable copies; the master is not exposed for mutation."""
        k1 = phi_keystore.get_phi_key()
        assert isinstance(k1, bytes)  # immutable
        k2 = phi_keystore.get_phi_key()
        assert k1 == k2 and k1 is not None

    def test_clear_zeroizes_master(self, sidecar_key: Path) -> None:
        store = PHIKeyStore.instance()
        store.get_key()
        assert store._key is not None  # loaded
        store.clear()
        assert store._key is None  # wiped + forgotten

    def test_path_change_busts_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # First key
        k1 = tmp_path / "key1"
        k1.write_text(secrets.token_hex(32), encoding="utf-8")
        k1.chmod(0o600)
        monkeypatch.setattr(config, "PHI_KEY_PATH", k1)
        fp1 = phi_keystore.phi_key_fingerprint()
        # Switch the configured key path → cache must reload, not serve stale.
        k2 = tmp_path / "key2"
        k2.write_text(secrets.token_hex(32), encoding="utf-8")
        k2.chmod(0o600)
        monkeypatch.setattr(config, "PHI_KEY_PATH", k2)
        fp2 = phi_keystore.phi_key_fingerprint()
        assert fp1 != fp2

    def test_role_gate_denies_llm_agent(
        self, sidecar_key: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")
        with pytest.raises(PHIKeyAccessDeniedError):
            phi_keystore.get_phi_key()
        # The low-level loader is gated too (defense in depth).
        with pytest.raises(PHIKeyAccessDeniedError):
            load_key()

    def test_role_gate_allows_default_role(
        self, sidecar_key: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REPORTAL_PROCESS_ROLE", raising=False)
        assert len(phi_keystore.get_phi_key()) == 32


# ── key rotation ──────────────────────────────────────────────────────────────
class TestDetectRotation:
    def test_first_run_when_no_record(self) -> None:
        assert key_rotation.detect_rotation(None, "abc") is key_rotation.RotationStatus.FIRST_RUN

    def test_unchanged_when_equal(self) -> None:
        assert key_rotation.detect_rotation("abc", "abc") is key_rotation.RotationStatus.UNCHANGED

    def test_rotated_when_different(self) -> None:
        assert key_rotation.detect_rotation("abc", "xyz") is key_rotation.RotationStatus.ROTATED


class TestCheckAndRecord:
    def test_lifecycle_first_unchanged_rotated(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit"
        # 1) first run — no record yet → FIRST_RUN, writes the record
        s1 = key_rotation.check_and_record(audit, "fp_aaa", run_id="run1")
        assert s1 is key_rotation.RotationStatus.FIRST_RUN
        assert key_rotation.key_state_path(audit).is_file()
        # 2) same key again → UNCHANGED
        s2 = key_rotation.check_and_record(audit, "fp_aaa", run_id="run2")
        assert s2 is key_rotation.RotationStatus.UNCHANGED
        # 3) new key → ROTATED, rotation_count increments, first_seen updates
        s3 = key_rotation.check_and_record(audit, "fp_bbb", run_id="run3")
        assert s3 is key_rotation.RotationStatus.ROTATED
        import json

        rec = json.loads(key_rotation.key_state_path(audit).read_text())
        assert rec["fingerprint"] == "fp_bbb"
        assert rec["rotation_count"] == 1
        assert rec["first_seen_run_id"] == "run3"
        assert rec["last_seen_run_id"] == "run3"

    def test_read_recorded_absent_is_none(self, tmp_path: Path) -> None:
        assert key_rotation.read_recorded_fingerprint(tmp_path) is None

    def test_read_recorded_corrupt_is_none(self, tmp_path: Path) -> None:
        """A corrupt state file fails soft to None (→ FIRST_RUN), never raises."""
        key_rotation.key_state_path(tmp_path).write_text("{not json", encoding="utf-8")
        assert key_rotation.read_recorded_fingerprint(tmp_path) is None

    def test_migration_existing_study_first_run_not_rotation(self, tmp_path: Path) -> None:
        """A pre-C1 study with no state record must NOT false-alarm rotation."""
        status = key_rotation.check_and_record(tmp_path, "fp_existing", run_id="r")
        assert status is key_rotation.RotationStatus.FIRST_RUN
