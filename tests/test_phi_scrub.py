"""Tests for scripts/security/phi_scrub.py.

Covers:
* pseudo_id determinism + key dependence
* date_offset_days range + determinism
* shift_date round-trip across ISO / M-D-Y / D-M-Y
* load_key hard-fail (missing / wrong mode / non-hex)
* bootstrap_key refusal to overwrite
* load_scrub_config (absent → None; limited_dataset without authority → error)
* run_scrub end-to-end: Safe Harbor drops birthdate; Limited Dataset shifts it
* idempotency (sentinel + per-row marker)
* orphan quarantine + overflow failure
* audit schema uses scrubbed[] (not removed[]) and counts only
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

import config
from scripts.security import phi_scrub

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def key_bytes() -> bytes:
    """Deterministic test key (do NOT use in production)."""
    return bytes.fromhex("00" * 32)


@pytest.fixture()
def alt_key_bytes() -> bytes:
    """A different test key for cross-key independence checks."""
    return bytes.fromhex("ff" * 32)


@pytest.fixture()
def sidecar_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a valid 64-hex-char key file with 0600 and monkeypatch PHI_KEY_PATH."""
    key_path = tmp_path / "phi_key"
    key_path.write_text(secrets.token_hex(32), encoding="utf-8")
    key_path.chmod(0o600)
    monkeypatch.setattr(config, "PHI_KEY_PATH", key_path)
    return key_path


@pytest.fixture()
def scrub_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PHI_SCRUB_CONFIG_PATH at a fresh tmp_path file (absent by default)."""
    cfg_path = tmp_path / "phi_scrub.yaml"
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_PATH", cfg_path)
    return cfg_path


def _write_config(path: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "compliance_posture": "safe_harbor",
        "subject_id_field": "SUBJID",
        "date_fields": ["^VISDAT$", "_DAT$"],
        "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
        "birthdate_field": "^DOB$",
        "max_jitter_days": 30,
        "orphan_quarantine_threshold": 5,
    }
    payload.update(overrides)
    import yaml

    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


# ── pseudo_id ───────────────────────────────────────────────────────────────


class TestPseudoId:
    def test_format_is_label_plus_12_hex(self, key_bytes: bytes) -> None:
        out = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        assert out.startswith("SUBJ_")
        assert len(out) == len("SUBJ_") + 12
        # remainder is lowercase hex
        assert all(c in "0123456789abcdef" for c in out[5:])

    def test_label_propagates_to_prefix(self, key_bytes: bytes) -> None:
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="FAM").startswith("FAM_")
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="LAB").startswith("LAB_")
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="SPEC").startswith("SPEC_")

    def test_default_label_is_neutral(self, key_bytes: bytes) -> None:
        # A caller that forgets to pass ``label`` gets a generic ``ID_``
        # prefix rather than the misleading ``SUBJ_`` of the v1 scheme.
        out = phi_scrub.pseudo_id("42", key=key_bytes)
        assert out.startswith("ID_")

    def test_deterministic_same_key_same_label(self, key_bytes: bytes) -> None:
        a = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        b = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        assert a == b

    def test_different_inputs_different_outputs(self, key_bytes: bytes) -> None:
        a = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        b = phi_scrub.pseudo_id("SUBJ-0002", key=key_bytes, label="SUBJ")
        assert a != b

    def test_different_keys_different_outputs(self, key_bytes: bytes, alt_key_bytes: bytes) -> None:
        a = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        b = phi_scrub.pseudo_id("SUBJ-0001", key=alt_key_bytes, label="SUBJ")
        assert a != b

    def test_domain_separation_same_raw_different_labels(self, key_bytes: bytes) -> None:
        """HMAC domain separation: same raw value + same key + different
        labels must yield different pseudonyms. Prevents correlation
        attacks when an adversary obtains two datasets with the same
        person re-identifying under different id categories (e.g. the
        raw string ``12345`` appearing as both FID and LABID)."""
        a = phi_scrub.pseudo_id("12345", key=key_bytes, label="SUBJ")
        b = phi_scrub.pseudo_id("12345", key=key_bytes, label="FAM")
        c = phi_scrub.pseudo_id("12345", key=key_bytes, label="LAB")
        assert a != b != c != a
        assert a.startswith("SUBJ_") and b.startswith("FAM_") and c.startswith("LAB_")


# ── date_offset_days ────────────────────────────────────────────────────────


class TestDateOffset:
    def test_range_within_envelope(self, key_bytes: bytes) -> None:
        for i in range(200):
            offset = phi_scrub.date_offset_days(f"SUBJ-{i:04d}", key=key_bytes, max_days=30)
            assert -30 <= offset <= 30

    def test_deterministic(self, key_bytes: bytes) -> None:
        a = phi_scrub.date_offset_days("SUBJ-0001", key=key_bytes, max_days=30)
        b = phi_scrub.date_offset_days("SUBJ-0001", key=key_bytes, max_days=30)
        assert a == b

    def test_different_subjects_different_offsets(self, key_bytes: bytes) -> None:
        offsets = {
            phi_scrub.date_offset_days(f"SUBJ-{i:04d}", key=key_bytes, max_days=30)
            for i in range(50)
        }
        # Very unlikely all 50 hash to the same offset
        assert len(offsets) > 1

    def test_rejects_zero_max_days(self, key_bytes: bytes) -> None:
        with pytest.raises(ValueError):
            phi_scrub.date_offset_days("x", key=key_bytes, max_days=0)


# ── shift_date ──────────────────────────────────────────────────────────────


class TestShiftDate:
    def test_iso_roundtrip(self) -> None:
        out = phi_scrub.shift_date("2014-07-15", 3)
        assert out == "2014-07-18"

    def test_iso_with_time(self) -> None:
        # parse_date (clinical_dates.py) does not retain the time component:
        # it matches the regex but constructs datetime(y, mo, d), so time is
        # zeroed. Scrubber output reflects day-granularity jitter (consistent
        # with the SANT ±N-day envelope).
        out = phi_scrub.shift_date("2014-07-15 12:30:45", -5)
        assert out == "2014-07-10 00:00:00"

    def test_mdy_roundtrip(self) -> None:
        # M/D/Y default (no field_name or non-DMY field_name)
        out = phi_scrub.shift_date("7/15/2014", 10)
        assert out == "7/25/2014"

    def test_dmy_roundtrip(self) -> None:
        # IC_VISDAT is a known D/M/Y variable per DMY_VARIABLES
        out = phi_scrub.shift_date("15/05/2014", 10, field_name="IC_VISDAT")
        assert out == "25/5/2014"

    def test_unparsable_returns_none(self) -> None:
        assert phi_scrub.shift_date("not a date", 5) is None
        assert phi_scrub.shift_date("", 5) is None

    def test_negative_offset(self) -> None:
        assert phi_scrub.shift_date("2014-07-15", -30) == "2014-06-15"


# ── load_key ────────────────────────────────────────────────────────────────


class TestLoadKey:
    def test_loads_valid_key(self, sidecar_key: Path) -> None:
        k = phi_scrub.load_key()
        assert isinstance(k, bytes)
        assert len(k) == 32

    def test_missing_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        missing = tmp_path / "absent"
        monkeypatch.setattr(config, "PHI_KEY_PATH", missing)
        with pytest.raises(phi_scrub.PHIKeyMissingError):
            phi_scrub.load_key()

    def test_wrong_mode_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        key_path = tmp_path / "key"
        key_path.write_text(secrets.token_hex(32), encoding="utf-8")
        key_path.chmod(0o644)
        monkeypatch.setattr(config, "PHI_KEY_PATH", key_path)
        with pytest.raises(phi_scrub.PHIKeyPermissionError):
            phi_scrub.load_key()

    def test_non_hex_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        key_path = tmp_path / "key"
        # 64 chars but contains non-hex
        key_path.write_text("z" * 64, encoding="utf-8")
        key_path.chmod(0o600)
        monkeypatch.setattr(config, "PHI_KEY_PATH", key_path)
        with pytest.raises(phi_scrub.PHIScrubError):
            phi_scrub.load_key()


# ── bootstrap_key ───────────────────────────────────────────────────────────


class TestBootstrapKey:
    def test_creates_file_with_0600(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "phi_key"
        written = phi_scrub.bootstrap_key(target)
        assert written == target
        assert target.is_file()
        mode = target.stat().st_mode & 0o777
        assert mode == 0o600
        # 64 hex chars = 32 bytes
        assert len(target.read_text(encoding="utf-8").strip()) == 64

    def test_refuses_overwrite(self, tmp_path: Path) -> None:
        target = tmp_path / "phi_key"
        target.write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError):
            phi_scrub.bootstrap_key(target)


# ── load_scrub_config ───────────────────────────────────────────────────────


class TestLoadScrubConfig:
    def test_absent_returns_none(self, scrub_config_path: Path) -> None:
        assert phi_scrub.load_scrub_config() is None

    def test_valid_parses(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.compliance_posture == "safe_harbor"
        assert cfg.max_jitter_days == 30
        assert cfg.field_is_date("VISDAT")
        assert cfg.field_is_date("IC_VISDAT") is False or cfg.field_is_date(
            "IC_VISDAT"
        )  # matches _DAT$
        assert cfg.field_is_id("SUBJID")
        assert cfg.field_is_birthdate("DOB")

    def test_limited_dataset_requires_authority(
        self, scrub_config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point BASE_DIR at tmp_path so authorities/ lookup uses a tree we control
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        _write_config(scrub_config_path, compliance_posture="limited_dataset")
        with pytest.raises(phi_scrub.PHIScrubError, match="authority note"):
            phi_scrub.load_scrub_config()

    def test_limited_dataset_accepts_authority(
        self, scrub_config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        authority = tmp_path / "authorities" / "phi_limited_dataset.md"
        authority.parent.mkdir(parents=True)
        authority.write_text("IRB #1234 + DUA on file", encoding="utf-8")
        _write_config(scrub_config_path, compliance_posture="limited_dataset")
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.compliance_posture == "limited_dataset"

    def test_invalid_posture_raises(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path, compliance_posture="bogus")
        with pytest.raises(phi_scrub.PHIScrubError):
            phi_scrub.load_scrub_config()

    def test_birthdate_excluded_from_date_patterns(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path, date_fields=["^DOB$"], birthdate_field="^DOB$")
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        # DOB matches both date + birthdate regex, but field_is_date must
        # exclude birthdate so posture logic routes correctly.
        assert cfg.field_is_date("DOB") is False
        assert cfg.field_is_birthdate("DOB") is True


# ── run_scrub end-to-end ────────────────────────────────────────────────────


def _seed_staging(
    monkeypatch_config: Path,
    rows: list[dict[str, Any]],
    filename: str = "1A_ICScreening.jsonl",
) -> Path:
    """Write rows into the staging datasets dir and return the file path."""
    staging = config.STAGING_DATASETS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / filename
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return target


class TestRunScrub:
    def test_no_config_is_noop_and_emits_disabled_audit(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # no config file at scrub_config_path → module no-ops, but ONLY when
        # the explicit env override is set. Without the override, run_scrub
        # raises (closes the silent-disabled-scrub gap).
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")

        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        payload = json.loads(Path(config.AUDIT_SCRUB_REPORT_PATH).read_text(encoding="utf-8"))
        assert payload["leg"] == "phi-scrub"
        assert payload["compliance_posture"] == "disabled"
        assert payload["scrubbed"] == []
        # Row is unchanged
        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded == rows

    def test_no_config_without_override_raises_phi_scrub_error(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The default behavior is fail-closed: missing yaml + no env override
        raises ``PHIScrubError`` so a misconfigured production run cannot
        silently publish raw PHI."""
        monkeypatch.delenv("REPORTALIN_ALLOW_DISABLED_SCRUB", raising=False)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIScrubError, match="config not found"):
            phi_scrub.run_scrub(study_name="TEST")

    def test_safe_harbor_drops_birthdate(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path)  # safe_harbor default
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "DOB": "1975-05-20", "VISDAT": "2014-07-16"},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 2

        key = phi_scrub.load_key()
        for original, row in zip(rows, loaded, strict=True):
            assert "DOB" not in row
            assert row["SUBJID"].startswith("SUBJ_")
            assert row["_phi_scrubbed"] == "v2"
            # VISDAT was shifted by exactly the per-subject deterministic offset
            expected_offset = phi_scrub.date_offset_days(
                str(original["SUBJID"]), key=key, max_days=30
            )
            expected = phi_scrub.shift_date(str(original["VISDAT"]), expected_offset)
            assert row["VISDAT"] == expected

    def test_limited_dataset_shifts_birthdate(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        sidecar_key.write_text("00" * 32, encoding="utf-8")
        sidecar_key.chmod(0o600)
        authority = tmp_path / "authorities" / "phi_limited_dataset.md"
        authority.parent.mkdir(parents=True)
        authority.write_text("IRB + DUA", encoding="utf-8")
        _write_config(scrub_config_path, compliance_posture="limited_dataset")
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        row = loaded[0]
        # DOB present but shifted
        assert "DOB" in row
        expected_offset = phi_scrub.date_offset_days(
            "S1", key=bytes.fromhex("00" * 32), max_days=30
        )
        assert expected_offset != 0
        assert row["DOB"] == phi_scrub.shift_date("1970-01-01", expected_offset)
        # Offset must be identical for DOB and VISDAT
        dob_dt = datetime.strptime(row["DOB"], "%Y-%m-%d")
        vis_dt = datetime.strptime(row["VISDAT"], "%Y-%m-%d")
        assert (dob_dt - datetime(1970, 1, 1)).days == expected_offset
        assert (vis_dt - datetime(2014, 7, 15)).days == expected_offset

    def test_audit_schema_uses_scrubbed_key(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "2014-07-16"},
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        payload = json.loads(Path(config.AUDIT_SCRUB_REPORT_PATH).read_text(encoding="utf-8"))
        assert "scrubbed" in payload
        assert "removed" not in payload  # advisor S1 — in-place transform != removal
        assert payload["compliance_posture"] == "safe_harbor"
        assert payload["leg"] == "phi-scrub"

        # Every event entry has counts only, no raw values
        for event in payload["scrubbed"]:
            assert set(event.keys()) == {"scope", "field", "file", "count"}
            assert isinstance(event["count"], int) and event["count"] >= 1

    def test_idempotency_via_sentinel(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        src = _seed_staging(monkeypatch_config, rows)

        phi_scrub.run_scrub(study_name="TEST")
        first_pass = src.read_text()

        # Second run with sentinel present is a no-op — rows unchanged
        phi_scrub.run_scrub(study_name="TEST")
        assert src.read_text() == first_pass

    def test_idempotency_via_row_marker(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        # Row already carries the CURRENT marker → scrubber skips it even
        # without the sentinel file.
        rows = [
            {
                "SUBJID": "SUBJ_already_pseud",
                "VISDAT": "2014-07-15",
                "_phi_scrubbed": "v2",
            }
        ]
        src = _seed_staging(monkeypatch_config, rows)
        sentinel = config.STUDY_STAGING_DIR / ".phi_scrub_complete"
        sentinel.unlink(missing_ok=True)

        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["SUBJID"] == "SUBJ_already_pseud"  # unchanged
        assert loaded[0]["VISDAT"] == "2014-07-15"  # unchanged

    def test_stale_v1_marker_gets_rescrubbed(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """A row from the v1 scheme (flat ``SUBJ_`` for every id) must be
        re-scrubbed under v2 so the output never silently mixes schemes.
        This is the whole reason ``_SCRUB_VERSION`` was bumped."""
        _write_config(scrub_config_path)
        rows = [
            {
                "SUBJID": "S1",
                "VISDAT": "2014-07-15",
                "_phi_scrubbed": "v1",
            }
        ]
        src = _seed_staging(monkeypatch_config, rows)
        sentinel = config.STUDY_STAGING_DIR / ".phi_scrub_complete"
        sentinel.unlink(missing_ok=True)

        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["SUBJID"].startswith("SUBJ_")
        assert loaded[0]["SUBJID"] != "S1"  # actually scrubbed, not passed through
        assert loaded[0]["_phi_scrubbed"] == "v2"

    def test_orphan_row_quarantined(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path, orphan_quarantine_threshold=10)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "", "VISDAT": "2014-07-16"},  # orphan (empty subject_id)
            {"VISDAT": "2014-07-17"},  # orphan (missing key)
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        kept = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(kept) == 1
        assert kept[0]["SUBJID"].startswith("SUBJ_")

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert quarantine.is_file()
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 2

        payload = json.loads(Path(config.AUDIT_SCRUB_REPORT_PATH).read_text(encoding="utf-8"))
        assert payload["orphan_rows"] == {"1A_ICScreening.jsonl": 2}

    def test_orphan_partial_scrub_before_quarantine_write(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Orphan rows must have drop_fields and birthdate removed before quarantine write.

        Acceptance criteria (P0.3):
        A. drop_fields match (participant_name) → absent from quarantine JSONL.
        B. birthdate_field match (DOB, safe_harbor) → absent from quarantine JSONL.
        C. Unrelated field (SCORE) → present unchanged in quarantine JSONL.
        D. Row count unchanged: 1 orphan in → 1 row on disk.
        E. date_fields (VISDAT) → NOT jittered (no subject ID → no offset).
        """
        _write_config(
            scrub_config_path,
            orphan_quarantine_threshold=10,
            drop_fields=["(?:patient|subject|participant)[-_]?name"],
        )
        # All rows are orphans (no SUBJID populated)
        rows = [
            {
                "participant_name": "Alice",
                "DOB": "1985-06-15",
                "VISDAT": "2020-03-01",
                "SCORE": 42,
            }
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert quarantine.is_file(), "quarantine file must exist"
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]

        # D — row count unchanged
        assert len(quarantined) == 1, f"expected 1 quarantine row, got {len(quarantined)}"
        q = quarantined[0]

        # A — drop_fields match removed
        assert "participant_name" not in q, "drop_fields match must be absent from quarantine row"

        # B — birthdate removed (safe_harbor posture)
        assert "DOB" not in q, "birthdate field must be absent from quarantine row (safe_harbor)"

        # C — unrelated field present unchanged
        assert q.get("SCORE") == 42, "unrelated field must pass through unchanged"

        # E — date field present unchanged (no jitter without subject ID)
        assert q.get("VISDAT") == "2020-03-01", "date field must not be jittered in orphan row"

    def test_orphan_partial_scrub_limited_dataset_drops_birthdate(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under limited_dataset posture, orphan birthdate must still be absent.

        Jitter (rule 7) cannot apply to orphans — no subject_id means no offset.
        The fallback must be unconditional drop, not a pass-through.

        Acceptance criteria:
        A. DOB absent from quarantine JSONL even under limited_dataset posture.
        B. SCORE (unrelated) present unchanged.
        C. VISDAT (date field) present unchanged (no jitter without subject ID).
        """
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        authority = tmp_path / "authorities" / "phi_limited_dataset.md"
        authority.parent.mkdir(parents=True)
        authority.write_text("IRB + DUA", encoding="utf-8")
        _write_config(
            scrub_config_path,
            compliance_posture="limited_dataset",
            orphan_quarantine_threshold=10,
            drop_fields=["(?:patient|subject|participant)[-_]?name"],
        )
        rows = [
            {
                "participant_name": "Alice",
                "DOB": "1985-06-15",
                "VISDAT": "2020-03-01",
                "SCORE": 42,
            }
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert quarantine.is_file(), "quarantine file must exist"
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]

        assert len(quarantined) == 1, f"expected 1 quarantine row, got {len(quarantined)}"
        q = quarantined[0]

        # A — birthdate absent regardless of limited_dataset posture
        assert "DOB" not in q, (
            "birthdate field must be absent from quarantine row under limited_dataset "
            "(jitter cannot apply without subject_id; drop is the only safe fallback)"
        )

        # B — unrelated field present unchanged
        assert q.get("SCORE") == 42, "unrelated field must pass through unchanged"

        # C — date field present unchanged (no jitter without subject ID)
        assert q.get("VISDAT") == "2020-03-01", "date field must not be jittered in orphan row"

    def test_orphan_partial_scrub_drops_recorded_in_ledger(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Orphan field-drops must appear in the as-written ledger under quarantine/ prefix.

        Acceptance criteria:
        A. phi_handling_ledger.as_written.json has at least one event with
           form starting with "quarantine/".
        B. That event records the participant_name drop (phi-scrub-drop or
           phi-scrub-birthdate-drop scope).
        """
        _write_config(
            scrub_config_path,
            orphan_quarantine_threshold=10,
            drop_fields=["(?:patient|subject|participant)[-_]?name"],
        )
        rows = [
            {
                "participant_name": "Alice",
                "DOB": "1985-06-15",
                "VISDAT": "2020-03-01",
                "SCORE": 42,
            }
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        assert ledger_path.is_file(), "phi_handling_ledger.as_written.json must exist"
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))

        quarantine_events = [
            ev
            for ev in payload["events"]
            if ev.get("where", {}).get("dataset_file", "").startswith("quarantine/")
        ]
        assert quarantine_events, (
            "Expected at least one ledger event with dataset_file under quarantine/ prefix "
            f"for orphan drops; got events: {payload['events']}"
        )

        # At least one of participant_name or DOB must appear as a drop event
        dropped_fields = {ev["variable_id"] for ev in quarantine_events}
        assert dropped_fields & {"participant_name", "DOB"}, (
            f"Expected participant_name or DOB in quarantine drops; got {dropped_fields}"
        )

    def test_orphan_overflow_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path, orphan_quarantine_threshold=1)
        rows = [
            {"SUBJID": "", "VISDAT": "2014-07-15"},
            {"SUBJID": "", "VISDAT": "2014-07-16"},
            {"SUBJID": "", "VISDAT": "2014-07-17"},
        ]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIQuarantineOverflowError):
            phi_scrub.run_scrub(study_name="TEST")

    def test_key_missing_hard_fails(
        self,
        monkeypatch_config: Path,
        scrub_config_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_config(scrub_config_path)
        monkeypatch.setattr(config, "PHI_KEY_PATH", tmp_path / "does_not_exist")
        _seed_staging(monkeypatch_config, [{"SUBJID": "S1", "VISDAT": "2014-07-15"}])
        with pytest.raises(phi_scrub.PHIKeyMissingError):
            phi_scrub.run_scrub(study_name="TEST")


# ── As-written ledger (dual-write) ──────────────────────────────────────────


class TestAsWrittenLedger:
    """Verify phi_handling_ledger.as_written.json is written alongside the legacy report."""

    def test_ledger_created_after_scrub(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"},
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        assert ledger_path.is_file(), "phi_handling_ledger.as_written.json must be created"
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert "run_id" in payload
        assert "iso_timestamp" in payload
        assert "events" in payload

    def test_ledger_event_shape(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        _write_config(
            scrub_config_path
        )  # safe_harbor: DOB dropped, VISDAT shifted, SUBJID pseudonymized
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"},
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert len(payload["events"]) >= 1, "Expected at least one PHI handling event"
        event = payload["events"][0]
        assert set(event.keys()) == {
            "form",
            "variable_id",
            "action",
            "rule",
            "rationale",
            "where",
            "count",
        }

    def test_ledger_empty_on_disabled_mode(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        assert ledger_path.is_file()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["events"] == []

    def test_keep_scope_not_in_ledger(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        # keep_fields covers SUBJID AND VISDAT — neither should be emitted as PHI actions.
        # DOB is also absent from the row. Only _phi_scrubbed marker is written.
        # Result: the as_written ledger has zero events (keep is not a PHI handling action).
        _write_config(
            scrub_config_path,
            keep_fields=["^SUBJID$", "^VISDAT$"],
            # no drop / date / id / birthdate that would fire
            id_fields=[],
            date_fields=[],
        )
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        assert ledger_path.is_file()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["events"] == [], (
            "keep-scoped fields must not appear in the as_written ledger"
        )


# ── Determinism across subject_id values (SANT spot-check) ──────────────────


class TestSANTProperty:
    def test_age_at_event_preserved_in_limited_dataset(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Property: with limited_dataset posture, (VISDAT - DOB) is invariant."""
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "authorities").mkdir()
        (tmp_path / "authorities" / "phi_limited_dataset.md").write_text(
            "IRB + DUA", encoding="utf-8"
        )
        _write_config(scrub_config_path, compliance_posture="limited_dataset")

        rows = [{"SUBJID": f"S{i}", "DOB": "1970-01-01", "VISDAT": "2014-07-15"} for i in range(10)]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        expected_age_days = (datetime(2014, 7, 15) - datetime(1970, 1, 1)).days

        for line in src.read_text().splitlines():
            row = json.loads(line)
            dob = datetime.strptime(row["DOB"], "%Y-%m-%d")
            vis = datetime.strptime(row["VISDAT"], "%Y-%m-%d")
            assert (vis - dob).days == expected_age_days


# ── New-action primitive tests (2026-04-23 catalog expansion) ───────────────


class TestCapNumeric:
    def test_above_threshold_returns_label(self) -> None:
        val, capped = phi_scrub.cap_numeric(90, threshold=89, label="90+")
        assert capped is True
        assert val == "90+"

    def test_at_threshold_passthrough(self) -> None:
        val, capped = phi_scrub.cap_numeric(89, threshold=89, label="90+")
        assert capped is False
        assert val == 89

    def test_below_threshold_passthrough(self) -> None:
        val, capped = phi_scrub.cap_numeric(45, threshold=89, label="90+")
        assert capped is False
        assert val == 45

    def test_numeric_string_above_threshold_caps(self) -> None:
        val, capped = phi_scrub.cap_numeric("101", threshold=89, label="90+")
        assert capped is True
        assert val == "90+"

    def test_non_numeric_string_passthrough(self) -> None:
        val, capped = phi_scrub.cap_numeric("unknown", threshold=89, label="90+")
        assert capped is False
        assert val == "unknown"

    def test_none_passthrough(self) -> None:
        val, capped = phi_scrub.cap_numeric(None, threshold=89, label="90+")
        assert capped is False
        assert val is None

    def test_empty_string_passthrough(self) -> None:
        val, capped = phi_scrub.cap_numeric("", threshold=89, label="90+")
        assert capped is False
        assert val == ""

    def test_bool_not_treated_as_numeric(self) -> None:
        # True is an int subclass in Python, but semantically not an age.
        val, capped = phi_scrub.cap_numeric(True, threshold=89, label="90+")
        assert capped is False
        assert val is True


class TestGeneralizeValue:
    MAP: ClassVar[dict[str, str]] = {
        "married": "Married",
        "single": "Single",
        "divorced": "Other",
    }

    def test_known_value_maps(self) -> None:
        val, mapped = phi_scrub.generalize_value("married", mapping=self.MAP)
        assert mapped is True
        assert val == "Married"

    def test_case_insensitive(self) -> None:
        val, mapped = phi_scrub.generalize_value("MARRIED", mapping=self.MAP)
        assert mapped is True
        assert val == "Married"

    def test_whitespace_trimmed(self) -> None:
        val, mapped = phi_scrub.generalize_value("  Married  ", mapping=self.MAP)
        assert mapped is True
        assert val == "Married"

    def test_unknown_value_passthrough(self) -> None:
        val, mapped = phi_scrub.generalize_value("annulled", mapping=self.MAP)
        assert mapped is False
        assert val == "annulled"

    def test_non_string_passthrough(self) -> None:
        val, mapped = phi_scrub.generalize_value(42, mapping=self.MAP)
        assert mapped is False
        assert val == 42

    def test_empty_passthrough(self) -> None:
        val, mapped = phi_scrub.generalize_value("", mapping=self.MAP)
        assert mapped is False
        assert val == ""


class TestSuppressSmallCell:
    def test_above_threshold_clamps(self) -> None:
        val, clamped = phi_scrub.suppress_small_cell(12, threshold=5)
        assert clamped is True
        assert val == 5

    def test_at_threshold_passthrough(self) -> None:
        val, clamped = phi_scrub.suppress_small_cell(5, threshold=5)
        assert clamped is False
        assert val == 5

    def test_below_threshold_passthrough(self) -> None:
        val, clamped = phi_scrub.suppress_small_cell(2, threshold=5)
        assert clamped is False
        assert val == 2

    def test_float_preserves_type(self) -> None:
        val, clamped = phi_scrub.suppress_small_cell(7.0, threshold=5)
        assert clamped is True
        assert val == 5.0
        assert isinstance(val, float)

    def test_non_numeric_passthrough(self) -> None:
        val, clamped = phi_scrub.suppress_small_cell("n/a", threshold=5)
        assert clamped is False
        assert val == "n/a"


# ── Config loading for new sections ─────────────────────────────────────────


class TestNewActionConfigLoading:
    def test_keep_drop_cap_generalize_suppress_load(self, scrub_config_path: Path) -> None:
        _write_config(
            scrub_config_path,
            keep_fields=["^CBC_"],
            drop_fields=["^SC_NAME$", "(?:COMMENT|REMARK|NOTE)$"],
            cap_fields=[{"pattern": "^IC_AGE$"}],
            generalize_fields=[{"pattern": "(?:MARITAL)", "mapping": "marital"}],
            generalization_maps={"marital": {"married": "Married", "single": "Single"}},
            suppress_small_cell_fields=["^IS_CONTACTS$"],
            age_cap={"threshold": 89, "label": "90+"},
            small_cell_threshold=5,
        )
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.field_is_keep("CBC_HBAND") is True
        assert cfg.field_is_keep("SC_NAME") is False
        assert cfg.field_is_drop("SC_NAME") is True
        assert cfg.field_is_drop("ST_COMMENT") is True
        assert cfg.field_is_drop("VISDAT") is False
        rule = cfg.cap_rule_for("IC_AGE")
        assert rule is not None
        assert rule.threshold == 89 and rule.label == "90+"
        gen = cfg.generalize_rule_for("MARITAL")
        assert gen is not None
        assert gen.mapping_name == "marital"
        assert cfg.field_is_suppress_small_cell("IS_CONTACTS") is True
        assert cfg.age_cap_threshold == 89
        assert cfg.age_cap_label == "90+"
        assert cfg.small_cell_threshold == 5

    def test_generalize_rule_with_unknown_mapping_raises(self, scrub_config_path: Path) -> None:
        _write_config(
            scrub_config_path,
            generalize_fields=[{"pattern": "^M$", "mapping": "ghost"}],
            generalization_maps={"marital": {"a": "b"}},
        )
        with pytest.raises(phi_scrub.PHIScrubError, match="unknown mapping"):
            phi_scrub.load_scrub_config()

    def test_cap_fields_missing_pattern_raises(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path, cap_fields=[{"threshold": 89}])
        with pytest.raises(phi_scrub.PHIScrubError, match="missing 'pattern'"):
            phi_scrub.load_scrub_config()

    def test_defaults_when_sections_absent(self, scrub_config_path: Path) -> None:
        # Backward compat: a config written BEFORE the 2026-04-23 expansion
        # (no keep/drop/cap/generalize/suppress keys) loads cleanly.
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.keep_patterns == []
        assert cfg.drop_patterns == []
        assert cfg.cap_rules == []
        assert cfg.generalize_rules == []
        assert cfg.suppress_small_cell_patterns == []
        assert cfg.age_cap_threshold == 89  # default
        assert cfg.age_cap_label == "90+"
        assert cfg.small_cell_threshold == 5


# ── _scrub_row priority dispatch ────────────────────────────────────────────


class TestScrubRowPriority:
    """Priority: keep > birthdate > drop > cap > generalize > suppress > date > id."""

    def test_keep_wins_over_drop(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        # ST_COMMENT matches both keep (for test) AND drop — keep wins.
        _write_config(
            scrub_config_path,
            keep_fields=["^KEEP_ME$"],
            drop_fields=["^KEEP_ME$"],
        )
        rows: list[dict[str, object]] = [{"SUBJID": "S1", "KEEP_ME": "value-should-survive"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert "KEEP_ME" in out[0]
        assert out[0]["KEEP_ME"] == "value-should-survive"

    def test_drop_removes_field_entirely(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        _write_config(
            scrub_config_path,
            drop_fields=["^STAFF_NAME$", "^ST_COMMENT$"],
        )
        rows = [{"SUBJID": "S1", "STAFF_NAME": "G BABU", "ST_COMMENT": "free text", "IS_SEX": 1}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert "STAFF_NAME" not in out[0]
        assert "ST_COMMENT" not in out[0]
        # Untouched field remains
        assert out[0]["IS_SEX"] == 1

    def test_cap_applies_to_age_over_89(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        _write_config(
            scrub_config_path,
            cap_fields=[{"pattern": "^IC_AGE$"}],
            age_cap={"threshold": 89, "label": "90+"},
        )
        rows = [
            {"SUBJID": "S1", "IC_AGE": 45},
            {"SUBJID": "S2", "IC_AGE": 92},
            {"SUBJID": "S3", "IC_AGE": 89},  # at threshold — not capped
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert out[0]["IC_AGE"] == 45
        assert out[1]["IC_AGE"] == "90+"
        assert out[2]["IC_AGE"] == 89

    def test_generalize_maps_known_value(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        _write_config(
            scrub_config_path,
            generalize_fields=[{"pattern": "(?:MARITAL)", "mapping": "marital"}],
            generalization_maps={"marital": {"married": "Married", "divorced": "Other"}},
        )
        rows: list[dict[str, object]] = [
            {"SUBJID": "S1", "IS_MARITAL": "Married"},
            {"SUBJID": "S2", "IS_MARITAL": "divorced"},
            {"SUBJID": "S3", "IS_MARITAL": "annulled"},  # unknown → passthrough
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert out[0]["IS_MARITAL"] == "Married"
        assert out[1]["IS_MARITAL"] == "Other"
        assert out[2]["IS_MARITAL"] == "annulled"

    def test_suppress_small_cell_clamps(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        _write_config(
            scrub_config_path,
            suppress_small_cell_fields=["^IS_CONTACTS$"],
            small_cell_threshold=5,
        )
        rows = [
            {"SUBJID": "S1", "IS_CONTACTS": 3},
            {"SUBJID": "S2", "IS_CONTACTS": 12},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert out[0]["IS_CONTACTS"] == 3
        assert out[1]["IS_CONTACTS"] == 5

    def test_audit_report_enumerates_new_actions(
        self, scrub_config_path: Path, sidecar_key: Path, monkeypatch_config: Path
    ) -> None:
        _write_config(
            scrub_config_path,
            drop_fields=["^STAFF_NAME$"],
            cap_fields=[{"pattern": "^IC_AGE$"}],
            generalize_fields=[{"pattern": "(?:MARITAL)", "mapping": "marital"}],
            generalization_maps={"marital": {"married": "Married"}},
            suppress_small_cell_fields=["^IS_CONTACTS$"],
        )
        rows = [
            {
                "SUBJID": "S1",
                "STAFF_NAME": "G BABU",
                "IC_AGE": 95,
                "IS_MARITAL": "married",
                "IS_CONTACTS": 12,
            }
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        payload = json.loads(Path(config.AUDIT_SCRUB_REPORT_PATH).read_text(encoding="utf-8"))
        scopes = {ev["scope"] for ev in payload["scrubbed"]}
        assert "phi-scrub-drop" in scopes
        assert "phi-scrub-cap" in scopes
        assert "phi-scrub-generalize" in scopes
        assert "phi-scrub-suppress-small-cell" in scopes


# ── Catalog coverage — HIPAA §164.514(b)(2) baseline ────────────────────────


class TestCatalogCoverage:
    """Ensures the shipped phi_scrub.yaml covers every HIPAA-18 category.

    This uses the *actual production YAML* (not a test fixture) so catalog
    regressions are caught: any removal of a rule class (e.g. someone deletes
    drop_fields entries for names or govt IDs) breaks these tests.
    """

    @pytest.fixture()
    def real_cfg(self) -> phi_scrub.PHIScrubConfig:
        cfg = phi_scrub.load_scrub_config(config.PHI_SCRUB_CONFIG_PATH)
        assert cfg is not None, "phi_scrub.yaml must be shipped with the package"
        return cfg

    @pytest.mark.parametrize(
        "sample_field",
        [
            # HIPAA §164.514(b)(2)(i)(A) — names
            "PATIENT_NAME",
            "FIRST_NAME",
            "IC_NAME",
            "DR_NAME",
            # HIPAA (D) — phone / email / fax
            "MOBILE_NO",
            "EMAIL",
            "FAX_NO",
            # (E) — electronic addresses
            "EMAIL_ADDR",
            # (F) — SSN / national IDs
            "SSN",
            # (G) — MRN
            "MRN",
            # (H) — health plan / insurance
            "INSURANCE_ID",
            "POLICY_NO",
            # (I) — account numbers
            "ACCOUNT_NO",
            "BANK_ACCT_NO",
            # (J) — certificate / license
            "LICENSE_NO",
            # (K) — vehicle / serial
            "VEHICLE_NO",
            # (L) — device identifiers
            "DEVICE_SERIAL",
            # (M) — URLs
            "WEBSITE",
            # (N) — IP / MAC
            "IP_ADDRESS",
            # (O) — biometric
            "FINGERPRINT",
            # (P) — photographic
            "PHOTO_ID",
            # India-specific govt IDs
            "AADHAAR",
            "PAN_NO",
            "VOTER_ID",
            "PASSPORT_NO",
            "RATION_CARD_NO",
            # Geography < state (HIPAA B)
            "VILLAGE",
            "DISTRICT",
            "PINCODE",
            "ADDRESS",
            "GPS",
            # Narrative
            "ST_COMMENT",
            "IC_REMARK",
            "WITHDRAWEXPLAIN",
        ],
    )
    def test_hipaa_category_has_coverage(
        self, real_cfg: phi_scrub.PHIScrubConfig, sample_field: str
    ) -> None:
        # Each sample field must be caught by drop, id pseudonymize, or (for
        # DOB/death) birthdate.
        assert (
            real_cfg.field_is_drop(sample_field)
            or real_cfg.field_is_id(sample_field)
            or real_cfg.field_is_birthdate(sample_field)
        ), f"field {sample_field!r} has no catalog coverage"

    def test_age_capped(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        assert real_cfg.cap_rule_for("IC_AGE") is not None
        assert real_cfg.cap_rule_for("HHC_AGE") is not None

    def test_clinical_allowlist_keeps_lab_fields(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        # CBC_, CXR_, CC_ALC, MEDF_INSULIN — all must pass through untouched.
        for field in ["CBC_WBC", "CXR_FINDING", "CC_ALC", "MEDF_INSULIN"]:
            assert real_cfg.field_is_keep(field) is True, f"{field} should be kept"

    def test_sex_preserved(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        # Sex is the primary epi covariate — never scrubbed.
        assert real_cfg.field_is_keep("IS_SEX") is True
        assert real_cfg.field_is_keep("HHC_SEX") is True

    def test_marital_generalized(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        rule = real_cfg.generalize_rule_for("IS_MARITAL")
        assert rule is not None
        assert rule.mapping_name == "marital"
        # Sanity: at least Married/Single/Other present.
        values = set(rule.mapping.values())
        assert {"Married", "Single", "Other"} <= values

    def test_household_contact_count_suppressed(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        assert real_cfg.field_is_suppress_small_cell("IS_CONTACTS") is True


# ── Audit hash wiring (P0.1) ─────────────────────────────────────────────────


class TestAuditHashes:
    """Verify scrub_config_hash and input_dataset_hash are sealed into the ledger.

    Acceptance criteria (P0.1):
    A. phi_handling_ledger.as_written.json has non-null scrub_config_hash.
    B. scrub_config_hash matches sha256(phi_scrub.yaml bytes).
    C. input_dataset_hash is non-null and stable across two identical runs.
    """

    def test_scrub_config_hash_is_non_null(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Criterion A: scrub_config_hash in the emitted ledger must not be None."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["scrub_config_hash"] is not None, (
            "scrub_config_hash must be sealed into the ledger; got None"
        )

    def test_scrub_config_hash_matches_yaml_sha256(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Criterion B: scrub_config_hash must equal sha256(phi_scrub.yaml bytes)."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        expected = hashlib.sha256(scrub_config_path.read_bytes()).hexdigest()

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["scrub_config_hash"] == expected, (
            f"scrub_config_hash mismatch: ledger={payload['scrub_config_hash']!r} "
            f"expected={expected!r}"
        )

    def test_input_dataset_hash_is_non_null(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Criterion C (part 1): input_dataset_hash must not be None."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["input_dataset_hash"] is not None, (
            "input_dataset_hash must be sealed into the ledger; got None"
        )

    def test_input_dataset_hash_stable_across_consecutive_runs(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Criterion C (part 2): identical raw input yields the same hash on two runs.

        We bypass the sentinel by resetting it between runs and using a fresh
        staging dir seeded with the same bytes both times.
        """
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]

        # ── Run 1 ──────────────────────────────────────────────────────────
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = (
            Path(config.AUDIT_SCRUB_REPORT_PATH).parent / "phi_handling_ledger.as_written.json"
        )
        hash_run1 = json.loads(ledger_path.read_text(encoding="utf-8"))["input_dataset_hash"]

        # ── Run 2: reset sentinel + staging, re-seed identical bytes ───────
        sentinel = config.STUDY_STAGING_DIR / ".phi_scrub_complete"
        sentinel.unlink(missing_ok=True)
        _seed_staging(monkeypatch_config, rows)
        # Remove the old ledger so we don't read a stale file
        ledger_path.unlink(missing_ok=True)
        phi_scrub.run_scrub(study_name="TEST")

        hash_run2 = json.loads(ledger_path.read_text(encoding="utf-8"))["input_dataset_hash"]

        assert hash_run1 == hash_run2, (
            f"input_dataset_hash must be stable across identical runs: "
            f"run1={hash_run1!r} run2={hash_run2!r}"
        )

    # ── _compute_input_dataset_hash unit tests ───────────────────────────────

    def test_non_jsonl_file_ignored_in_input_hash(self, tmp_path: Path) -> None:
        """A non-.jsonl file (e.g. .tmp crash artefact) must NOT affect the hash."""
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()

        jsonl_file = datasets_dir / "bar.jsonl"
        jsonl_file.write_bytes(b'{"SUBJID": "S1"}\n')

        hash_without_tmp = phi_scrub._compute_input_dataset_hash(datasets_dir)

        # Add a crash-recovery artefact alongside the real file.
        (datasets_dir / "foo.tmp").write_bytes(b"garbage")

        hash_with_tmp = phi_scrub._compute_input_dataset_hash(datasets_dir)

        assert hash_without_tmp == hash_with_tmp, (
            "Non-.jsonl file should not affect input_dataset_hash; "
            f"without_tmp={hash_without_tmp!r} with_tmp={hash_with_tmp!r}"
        )

    def test_second_jsonl_file_changes_input_hash(self, tmp_path: Path) -> None:
        """Adding a second real .jsonl file MUST change the manifest hash."""
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()

        (datasets_dir / "form_a.jsonl").write_bytes(b'{"SUBJID": "S1"}\n')
        hash_one_file = phi_scrub._compute_input_dataset_hash(datasets_dir)

        (datasets_dir / "form_b.jsonl").write_bytes(b'{"SUBJID": "S2"}\n')
        hash_two_files = phi_scrub._compute_input_dataset_hash(datasets_dir)

        assert hash_one_file != hash_two_files, (
            "Adding a second .jsonl file must change input_dataset_hash"
        )

    def test_unhashable_jsonl_raises_phi_scrub_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An OSError from hash_file must surface as PHIScrubError naming the path."""
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()

        good_file = datasets_dir / "good.jsonl"
        good_file.write_bytes(b'{"SUBJID": "S1"}\n')

        bad_file = datasets_dir / "bad.jsonl"
        bad_file.write_bytes(b'{"SUBJID": "S2"}\n')

        # Make hash_file raise for the bad file only.
        original_hash_file = phi_scrub.hash_file

        def _patched_hash_file(path: Path, **kwargs: object) -> str:
            if path == bad_file:
                raise OSError("permission denied (simulated)")
            return original_hash_file(path, **kwargs)

        monkeypatch.setattr(phi_scrub, "hash_file", _patched_hash_file)

        with pytest.raises(phi_scrub.PHIScrubError, match="input manifest unhashable"):
            phi_scrub._compute_input_dataset_hash(datasets_dir)

    def test_empty_staging_dir_returns_sha256_of_empty_string(
        self, tmp_path: Path
    ) -> None:
        """Empty staging dir must yield sha256(b'').hexdigest(), not None or an error.

        Locks the empty-vs-missing distinction: a present-but-empty dir has a
        defined hash; a missing dir produces no hash at all (None in the ledger).
        """
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()

        result = phi_scrub._compute_input_dataset_hash(datasets_dir)
        expected = hashlib.sha256(b"").hexdigest()

        assert result == expected, (
            f"Empty staging dir must yield sha256(b'') = {expected!r}; got {result!r}"
        )

    def test_compute_input_dataset_hash_manifest_format(self, tmp_path: Path) -> None:
        """Direct unit test: hash is sha256 of '<relpath>\\t<size>\\t<content_hash>'."""
        from scripts.utils.integrity import hash_file as _hash_file

        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()

        content = b'{"SUBJID": "S1", "VISDAT": "2014-07-15"}\n'
        (datasets_dir / "form_a.jsonl").write_bytes(content)

        file_size = len(content)
        file_content_hash = hashlib.sha256(content).hexdigest()
        # The manifest line is: relpath\tsize\tcontent_hash
        expected_manifest = f"form_a.jsonl\t{file_size}\t{file_content_hash}"
        expected_hash = hashlib.sha256(expected_manifest.encode("utf-8")).hexdigest()

        result = phi_scrub._compute_input_dataset_hash(datasets_dir)

        assert result == expected_hash, (
            f"Manifest format mismatch: expected={expected_hash!r} got={result!r}"
        )


# ── Production-mode bypass guard ─────────────────────────────────────────────


class TestProductionBypassGuard:
    """Acceptance criteria A–D for the REPORTALIN_ALLOW_DISABLED_SCRUB guard."""

    def test_production_mode_with_env_set_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Acceptance A: prod mode ON + env var set → PHIScrubError; bypass never runs."""
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: True)

        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        with pytest.raises(
            phi_scrub.PHIScrubError,
            match="REPORTALIN_ALLOW_DISABLED_SCRUB is forbidden in production mode",
        ):
            phi_scrub.run_scrub(study_name="TEST")

    def test_non_production_mode_with_env_set_bypasses(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Acceptance B: prod mode OFF + env var set → bypass runs with WARNING."""
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: False)

        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        import logging

        with caplog.at_level(logging.WARNING, logger="scripts.security.phi_scrub"):
            phi_scrub.run_scrub(study_name="TEST")  # must not raise

        assert any(
            "REPORTALIN_ALLOW_DISABLED_SCRUB" in record.message
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ), "Expected a WARNING mentioning REPORTALIN_ALLOW_DISABLED_SCRUB"
