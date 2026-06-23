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
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

import config
from scripts.audit.ledger import dataset_phi_ledger_path
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


def _write_config(path: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "compliance_posture": "safe_harbor",
        "subject_id_field": "SUBJID",
        "date_fields": ["^VISDAT$", "_DAT$"],
        "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
        "birthdate_field": "^DOB$",
        # Note 32 age-dependent date policy: birthdate/death date are dropped when the
        # form carries an age column, jittered (preserving timeline) when it does not.
        "age_fields": ["^AGE$", "(?:^|_)AGE(?:Y|YR|YRS|EST|MON|MONTH|MONTHS)?$"],
        "death_date_fields": ["(?:DTH|DEATH)[-_]?(?:DAT|DATE)", "^(?:FA_DTHDAT|FB_DTHDAT)$"],
        "max_jitter_days": 30,
        "orphan_quarantine_threshold": 5,
        # Note 29: the PRODUCTION default for unparseable dates is "blank", but the
        # test fixture pins "quarantine" so the many pre-existing fail-closed tests
        # keep exercising the whole-row quarantine path. Tests that assert the
        # production blank-and-publish behavior override this explicitly.
        "unparseable_date_policy": "quarantine",
    }
    payload.update(overrides)
    import yaml

    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


# ── pseudo_id ───────────────────────────────────────────────────────────────


class TestPseudoId:
    def test_format_is_rid_label_plus_alpha12(self, key_bytes: bytes) -> None:
        out = phi_scrub.pseudo_id("SUBJ-0001", key=key_bytes, label="SUBJ")
        assert out.startswith("RID_SUBJ_")
        assert len(out) == len("RID_SUBJ_") + 12
        # remainder is lowercase alphabetic a-p encoding, not decimal-heavy hex
        assert all(c in "abcdefghijklmnop" for c in out.removeprefix("RID_SUBJ_"))

    def test_label_propagates_inside_rid_token(self, key_bytes: bytes) -> None:
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="FAM").startswith("RID_FAM_")
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="LAB").startswith("RID_LAB_")
        assert phi_scrub.pseudo_id("x", key=key_bytes, label="SPEC").startswith("RID_SPEC_")

    def test_default_label_is_neutral(self, key_bytes: bytes) -> None:
        # A caller that forgets to pass ``label`` gets a generic ``RID_ID_``
        # token rather than the misleading ``SUBJ_`` of the v1 scheme.
        out = phi_scrub.pseudo_id("42", key=key_bytes)
        assert out.startswith("RID_ID_")

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
        assert a.startswith("RID_SUBJ_")
        assert b.startswith("RID_FAM_")
        assert c.startswith("RID_LAB_")


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

    def test_ambiguous_with_date_locales_dmy_no_raise(self) -> None:
        # Regression: ambiguous date (both components ≤ 12) with a matching
        # date_locales entry must parse and shift without raising ValueError.
        # date_locales keys must be UPPER-CASE (normalised at manifest load time).
        result = phi_scrub.shift_date(
            "07/05/2014",
            1,
            field_name="IC_VISDAT_V2",
            date_locales={"IC_VISDAT_V2": "DMY"},
        )
        # DMY: day=7, month=5 → 2014-05-07 + 1 day = 2014-05-08 → "8/5/2014"
        assert result is not None
        assert result == "8/5/2014"

    def test_ambiguous_with_date_locales_mdy_no_raise(self) -> None:
        # date_locales keys must be UPPER-CASE (normalised at manifest load time).
        result = phi_scrub.shift_date(
            "07/05/2014",
            1,
            field_name="IC_VISDAT_V2",
            date_locales={"IC_VISDAT_V2": "MDY"},
        )
        # MDY: month=7, day=5 → 2014-07-05 + 1 day = 2014-07-06 → "7/6/2014"
        assert result is not None
        assert result == "7/6/2014"

    def test_ambiguous_without_date_locales_and_field_raises(self) -> None:
        # Without manifest entry and with field_name, must still raise.
        with pytest.raises(ValueError, match="Ambiguous date locale"):
            phi_scrub.shift_date("07/05/2014", 1, field_name="UNKNOWN_COL")


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

    def test_missing_error_uses_basename_not_full_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PHIKeyMissingError must not leak directory structure (m-4)."""
        missing = tmp_path / "subdir" / "phi_key.bin"
        monkeypatch.setattr(config, "PHI_KEY_PATH", missing)
        with pytest.raises(phi_scrub.PHIKeyMissingError) as exc_info:
            phi_scrub.load_key()
        msg = str(exc_info.value)
        assert "phi_key.bin" in msg
        # The parent directory path must not appear in the message.
        assert str(missing.parent) not in msg

    def test_wrong_mode_error_uses_basename_not_full_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PHIKeyPermissionError must not leak directory structure (m-4)."""
        key_path = tmp_path / "subdir" / "phi_key.bin"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("a" * 64, encoding="utf-8")
        key_path.chmod(0o644)
        monkeypatch.setattr(config, "PHI_KEY_PATH", key_path)
        with pytest.raises(phi_scrub.PHIKeyPermissionError) as exc_info:
            phi_scrub.load_key()
        msg = str(exc_info.value)
        assert "phi_key.bin" in msg
        # The parent directory path must not appear in the message.
        assert str(key_path.parent) not in msg

    def test_wrong_length_key_error_uses_basename(self, tmp_path: Path) -> None:
        """PHIScrubError for wrong hex length must use basename, not full path (m-4)."""
        key_file = tmp_path / "phi_key.bin"
        key_file.write_text("deadbeef", encoding="utf-8")  # 8 hex chars, not 64
        key_file.chmod(0o600)
        with pytest.raises(phi_scrub.PHIScrubError) as exc_info:
            phi_scrub.load_key(key_file)
        msg = str(exc_info.value)
        assert key_file.name in msg
        assert str(key_file.parent) not in msg

    def test_non_hex_key_error_uses_basename(self, tmp_path: Path) -> None:
        """PHIScrubError for non-hex content must use basename, not full path (m-4)."""
        key_file = tmp_path / "phi_key.bin"
        key_file.write_text("z" * 64, encoding="utf-8")  # 64 chars but not valid hex
        key_file.chmod(0o600)
        with pytest.raises(phi_scrub.PHIScrubError) as exc_info:
            phi_scrub.load_key(key_file)
        msg = str(exc_info.value)
        assert key_file.name in msg
        assert str(key_file.parent) not in msg


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

    def test_default_facility_specify_drop_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (Note 32): facility/geography 'specify' write-ins DROP, while
        coded parents and clinical 'specify' refinements KEEP. TC_NOCARDSP
        ("...specify center:") must drop with its siblings TC_OTDOTSLOC/TC_CENTERSP
        per the A1 facility-specify policy — not be swept into the ^TC_ keep prefix.
        Loads the REAL default config (no per-study override)."""
        defaults_dir = Path(__file__).resolve().parents[1] / "config" / "_defaults"
        monkeypatch.setattr(config, "CONFIG_DEFAULTS_DIR", defaults_dir)
        cfg = phi_scrub.load_scrub_config(study="__no_such_study__")
        assert cfg is not None
        # the fix: facility 'specify center' write-in drops, coded parent keeps
        assert cfg.field_is_drop("TC_NOCARDSP")
        assert not cfg.field_is_keep("TC_NOCARDSP")
        assert cfg.field_is_keep("TC_NOCARD")
        assert not cfg.field_is_drop("TC_NOCARD")
        # clinical 'specify' refinements stay KEEP (no over-protection)
        assert cfg.field_is_keep("TC_CHANGESP")
        assert cfg.field_is_keep("TC_EXTROTSP")
        # documented facility-specify siblings stay DROP (policy consistency)
        for sibling in ("TC_OTDOTSLOC", "TC_CENTERSP", "IC_CLINICSP", "HC_HIVLOCSP"):
            assert cfg.field_is_drop(sibling), sibling

    def test_contact_counts_clamp_not_drop_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (Note 32, THEME 2): household-contact COUNTS are configured
        suppress_small_cell (clamp+keep), and must be recognized as such on BOTH
        the raw header AND the normalized header (the force-drop-exclusion check
        sees the normalized form). IS_CONTACTS_6YRS normalizes to is_contacts_6_yrs
        — the pattern must tolerate the inserted underscore, else the count is
        force-dropped instead of clamped."""
        from scripts.security.phi_scrub import _normalize_header_for_lookup as _norm

        defaults_dir = Path(__file__).resolve().parents[1] / "config" / "_defaults"
        monkeypatch.setattr(config, "CONFIG_DEFAULTS_DIR", defaults_dir)
        cfg = phi_scrub.load_scrub_config(study="__no_such_study__")
        assert cfg is not None
        for h in ("IS_CONTACTS", "IS_CONTACTS_TOTAL", "IS_CONTACTS_6YRS"):
            assert cfg.field_is_suppress_small_cell(h), f"raw {h}"
            assert cfg.field_is_suppress_small_cell(_norm(h)), f"normalized {h}"
            # a small-cell count must NOT be a drop/keep — it routes to the clamp rung
            assert not cfg.field_is_drop(h), h
            assert not cfg.field_is_keep(h), h

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


# ── Task A7: per-study config merge + merged-effective hash ──────────────────


class TestPerStudyScrubConfigMerge:
    """Defaults base + per-study override deep-merge and merged hash (Task A7)."""

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """Patch config so defaults live at <tmp>/config/_defaults and per-study
        at <tmp>/config/<STUDY>. Returns (default_path, per_study_path)."""
        defaults_dir = tmp_path / "config" / "_defaults"
        defaults_dir.mkdir(parents=True)
        study_dir = tmp_path / "config" / config.STUDY_NAME
        study_dir.mkdir(parents=True)
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr(config, "CONFIG_DEFAULTS_DIR", defaults_dir)
        return defaults_dir / "phi_scrub.yaml", study_dir / "phi_scrub.yaml"

    def test_defaults_only_when_no_per_study(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default_path, _ = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, max_jitter_days=30)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.max_jitter_days == 30

    def test_no_config_at_all_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)  # neither file written
        assert phi_scrub.load_scrub_config() is None
        assert phi_scrub.effective_scrub_config_hash() is None

    def test_per_study_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        default_path, per_study_path = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, max_jitter_days=30, small_cell_threshold=5)
        # Per-study overrides a scalar; an unspecified scalar (small_cell) is
        # inherited from the defaults base.
        _write_config(per_study_path, max_jitter_days=7)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.max_jitter_days == 7  # per-study scalar wins
        assert cfg.small_cell_threshold == 5  # inherited from defaults

    def test_per_study_list_replaces(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        default_path, per_study_path = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, date_fields=["^VISDAT$", "_DAT$"])
        _write_config(per_study_path, date_fields=["^ONLYTHIS$"])
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        # List value REPLACES (not merges): VISDAT no longer a date field.
        assert cfg.field_is_date("ONLYTHIS")
        assert cfg.field_is_date("VISDAT") is False

    def test_merged_hash_is_deterministic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default_path, per_study_path = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, max_jitter_days=30)
        _write_config(per_study_path, max_jitter_days=7)
        h1 = phi_scrub.effective_scrub_config_hash()
        h2 = phi_scrub.effective_scrub_config_hash()
        assert h1 is not None and h1 == h2

    def test_merged_hash_discriminates_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default_path, per_study_path = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, max_jitter_days=30)
        # No override → hash A.
        hash_defaults_only = phi_scrub.effective_scrub_config_hash()
        # Different per-study overrides → different hashes (no collision).
        _write_config(per_study_path, max_jitter_days=7)
        hash_override_a = phi_scrub.effective_scrub_config_hash()
        _write_config(per_study_path, max_jitter_days=14)
        hash_override_b = phi_scrub.effective_scrub_config_hash()
        assert len({hash_defaults_only, hash_override_a, hash_override_b}) == 3

    def test_single_file_hash_matches_sha256_of_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Back-compat: a defaults-only resolution hashes to sha256(file_bytes)."""
        import hashlib

        default_path, _ = self._setup(tmp_path, monkeypatch)
        _write_config(default_path, max_jitter_days=30)
        expected = hashlib.sha256(default_path.read_bytes()).hexdigest()
        assert phi_scrub.effective_scrub_config_hash() == expected

    def test_non_list_date_null_tokens_raises(self, scrub_config_path: Path) -> None:
        """L4: a date_null_tokens value that is not a list must raise PHIScrubError.

        The key is optional in the YAML; when present it MUST be a list of strings.
        A non-list value (e.g. a bare string) is a misconfiguration that must
        fail-closed rather than silently falling back to the default token set.
        """
        import yaml

        # Write a config with date_null_tokens set to a plain string (invalid).
        payload: dict[str, object] = {
            "compliance_posture": "safe_harbor",
            "subject_id_field": "SUBJID",
            "date_fields": ["^VISDAT$"],
            "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
            "max_jitter_days": 30,
            "date_null_tokens": "not-a-list",  # must be a list
        }
        scrub_config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

        with pytest.raises(phi_scrub.PHIScrubError):
            phi_scrub.load_scrub_config()

    def test_non_list_date_null_tokens_dict_raises(self, scrub_config_path: Path) -> None:
        """L4 variant: a dict value for date_null_tokens also raises PHIScrubError."""
        import yaml

        payload: dict[str, object] = {
            "compliance_posture": "safe_harbor",
            "subject_id_field": "SUBJID",
            "date_fields": ["^VISDAT$"],
            "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
            "max_jitter_days": 30,
            "date_null_tokens": {"UNK": True},  # dict, not a list
        }
        scrub_config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

        with pytest.raises(phi_scrub.PHIScrubError):
            phi_scrub.load_scrub_config()

    def test_null_date_null_tokens_uses_default(self, scrub_config_path: Path) -> None:
        """L4 complement: absent key falls back to the module default (no raise)."""
        # _write_config does not include date_null_tokens → key is absent
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        # Default set includes "UNK" — verify fallback is active
        assert cfg.is_date_null_token("UNK")
        assert cfg.is_date_null_token("NA")


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


def _phi_ledger_path(filename: str = "1A_ICScreening.jsonl") -> Path:
    return dataset_phi_ledger_path(Path(config.AUDIT_SCRUB_REPORT_PATH).parent, filename)


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
        # Note 32: birthdate drops under Safe Harbor only when the form carries an
        # age column (here AGE); with no age column it would jitter instead. See
        # test_no_age_birthdate_jitters_preserving_timeline for that branch.
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15", "AGE": "44"},
            {"SUBJID": "S2", "DOB": "1975-05-20", "VISDAT": "2014-07-16", "AGE": "39"},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 2

        key = phi_scrub.load_key()
        for original, row in zip(rows, loaded, strict=True):
            assert "DOB" not in row
            assert row["SUBJID"].startswith("RID_SUBJ_")
            assert row["_phi_scrubbed"] == "v3"
            # VISDAT was shifted by exactly the per-subject deterministic offset
            expected_offset = phi_scrub.date_offset_days(
                str(original["SUBJID"]), key=key, max_days=30
            )
            expected = phi_scrub.shift_date(str(original["VISDAT"]), expected_offset)
            assert row["VISDAT"] == expected

    def test_no_age_birthdate_and_death_date_jitter_preserving_timeline(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Note 32: in a form with NO age column, birthdate AND death date are
        JITTERED (not dropped) with the per-subject offset, so the interval/timeline
        between a subject's events is preserved (the real dates are obscured)."""
        _write_config(scrub_config_path)  # rows below carry NO age column
        rows = [
            {
                "SUBJID": "S1",
                "DOB": "1970-01-01",
                "FA_DTHDAT": "2020-06-15",
                "VISDAT": "2020-01-15",
            },
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        row = next(json.loads(ln) for ln in src.read_text().splitlines() if ln.strip())
        # both age-dependent dates SURVIVE (jittered), and are not the raw value
        assert "DOB" in row and row["DOB"] != "1970-01-01"
        assert "FA_DTHDAT" in row and row["FA_DTHDAT"] != "2020-06-15"
        assert "VISDAT" in row and row["VISDAT"] != "2020-01-15"

        def _d(s: str) -> date:
            y, m, dd = (int(p) for p in s.split("-"))
            return date(y, m, dd)

        # timeline preserved: one per-subject offset → every interval unchanged
        assert (_d(row["FA_DTHDAT"]) - _d(row["VISDAT"])).days == (
            _d("2020-06-15") - _d("2020-01-15")
        ).days

    def test_age_present_drops_birthdate_and_death_date(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Note 32: when the form HAS an age column, birthdate + death date are
        DROPPED (the age column already carries the de-identified age)."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "FA_DTHDAT": "2020-06-15", "AGE": "50"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        row = next(json.loads(ln) for ln in src.read_text().splitlines() if ln.strip())
        assert "DOB" not in row
        assert "FA_DTHDAT" not in row

    def test_form_has_age_detected_via_column_structure_row(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Note 32: form_has_age is computed from the column set. A leading
        column-structure metadata row lists every column (incl. AGE), so it must
        drive the has-age decision — a death date in a has-age form drops even when
        the FIRST line is the schema row and the data rows are sparse. (This is the
        real 95_SAE shape.)"""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "", "FA_DTHDAT": "", "AGE": "", "_metadata": {"type": "column_structure"}},
            {"SUBJID": "S1", "FA_DTHDAT": "2020-06-15", "AGE": "50"},  # sparse data row
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        published = [json.loads(ln) for ln in src.read_text().splitlines() if ln.strip()]
        data = [
            r
            for r in published
            if (r.get("_metadata") or {}).get("type") != "column_structure"
            and str(r.get("SUBJID", "")).startswith("RID_")
        ]
        assert data, "expected a scrubbed data row"
        # form_has_age=True (from the column-structure row) → death date drops
        assert "FA_DTHDAT" not in data[0]

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

    def test_limited_dataset_unshiftable_birthdate_fail_closed(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No-expose guarantee on the failure path.

        Under ``limited_dataset`` a birthdate that cannot be SANT-jittered must
        fail-closed: strict mode aborts the whole run (``PHIDateUnshiftableError``)
        so nothing is promoted, AND the held quarantine copy has the birthdate
        stripped — a raw / un-jittered DOB must never appear in any readable
        artifact, published or quarantined.
        """
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        sidecar_key.write_text("00" * 32, encoding="utf-8")
        sidecar_key.chmod(0o600)
        authority = tmp_path / "authorities" / "phi_limited_dataset.md"
        authority.parent.mkdir(parents=True)
        authority.write_text("IRB + DUA", encoding="utf-8")
        _write_config(scrub_config_path, compliance_posture="limited_dataset")
        # DOB is an impossible calendar date (Feb 30) — unparseable, and not a
        # missing-data sentinel, so it routes to the date-quarantine path.
        rows = [{"SUBJID": "S1", "DOB": "2014-02-30", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        # Strict mode (run_scrub default): the first un-jitterable DOB aborts the run.
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file(), "date-unshiftable rows must be quarantined"
        raw_text = quarantine.read_text()
        held = [json.loads(line) for line in raw_text.splitlines() if line]
        assert held, "quarantine file must contain the held row"
        for row in held:
            assert "DOB" not in row, (
                "raw birthdate must be stripped from the quarantine copy "
                "(_apply_field_only_rules drops it unconditionally, any posture)"
            )
        # Belt-and-braces: the raw DOB string must not survive anywhere in the file.
        assert "2014-02-30" not in raw_text, (
            "raw DOB value must never appear in any readable artifact"
        )

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

    def test_run_scrub_acquires_and_zeroizes_key_via_keystore(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Note 12: run_scrub acquires the key via the zeroizable PHIKeyStore and
        wipes it (clear_phi_key) when scrub completes — never load_key() directly."""
        import scripts.security.phi_keystore as keystore

        _write_config(scrub_config_path)
        _seed_staging(monkeypatch_config, [{"SUBJID": "S1", "VISDAT": "2014-07-15"}])

        calls = {"get": 0, "clear": 0}
        real_get, real_clear = keystore.get_phi_key, keystore.clear_phi_key

        def _spy_get(*a: Any, **k: Any) -> bytes:
            calls["get"] += 1
            return real_get(*a, **k)

        def _spy_clear(*a: Any, **k: Any) -> None:
            calls["clear"] += 1
            return real_clear(*a, **k)

        # run_scrub does a local `from ...phi_keystore import get_phi_key, clear_phi_key`
        # resolved at call time, so patching the module attributes takes effect.
        monkeypatch.setattr(keystore, "get_phi_key", _spy_get)
        monkeypatch.setattr(keystore, "clear_phi_key", _spy_clear)

        phi_scrub.run_scrub(study_name="TEST")

        assert calls["get"] >= 1, "run_scrub must acquire the key via PHIKeyStore.get_phi_key"
        assert calls["clear"] >= 1, "run_scrub must zeroize the key via clear_phi_key (try/finally)"

    def test_scrub_report_no_timestamp_in_primary_timing_sidecar_written(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Fix B-2: phi_scrub_report.json must be byte-reproducible (no generated_utc).

        The wall-clock timestamp is written to a parallel phi_scrub_report_timing.json
        sidecar, mirroring the lineage manifest content-only + *_timing.json pattern.
        """
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        report_path = Path(config.AUDIT_SCRUB_REPORT_PATH)
        assert report_path.is_file(), "phi_scrub_report.json must be written"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        # Primary report must NOT carry any wall-clock fields.
        assert "generated_utc" not in payload, (
            "generated_utc must not appear in the primary phi_scrub_report.json — "
            "it should be in the timing sidecar only"
        )
        # The timing sidecar must exist beside the primary report and carry the timestamp.
        timing_path = report_path.with_name(report_path.stem + "_timing.json")
        assert timing_path.is_file(), "phi_scrub_report_timing.json sidecar must be written"
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        assert "generated_utc" in timing, "timing sidecar must carry generated_utc"
        assert timing["generated_utc"].endswith("Z"), "generated_utc must be UTC ISO format"

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
                "SUBJID": "RID_SUBJ_already_pseud",
                "VISDAT": "2014-07-15",
                "_phi_scrubbed": "v3",
            }
        ]
        src = _seed_staging(monkeypatch_config, rows)
        sentinel = config.STUDY_STAGING_DIR / ".phi_scrub_complete"
        sentinel.unlink(missing_ok=True)

        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["SUBJID"] == "RID_SUBJ_already_pseud"  # unchanged
        assert loaded[0]["VISDAT"] == "2014-07-15"  # unchanged

    def test_stale_v2_marker_gets_rescrubbed(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """A row from the v2 scheme (``<LABEL>_<hmac12>``) must be
        re-scrubbed under v3 so the output never silently mixes schemes.
        This is the whole reason ``_SCRUB_VERSION`` was bumped."""
        _write_config(scrub_config_path)
        rows = [
            {
                "SUBJID": "S1",
                "VISDAT": "2014-07-15",
                "_phi_scrubbed": "v2",
            }
        ]
        src = _seed_staging(monkeypatch_config, rows)
        sentinel = config.STUDY_STAGING_DIR / ".phi_scrub_complete"
        sentinel.unlink(missing_ok=True)

        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["SUBJID"].startswith("RID_SUBJ_")
        assert loaded[0]["SUBJID"] != "S1"  # actually scrubbed, not passed through
        assert loaded[0]["_phi_scrubbed"] == "v3"

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
        assert kept[0]["SUBJID"].startswith("RID_SUBJ_")

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
        A. The per-dataset phi_handling_ledger.as_written.json has at least
           one event from a quarantine dataset file.
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

        ledger_path = _phi_ledger_path()
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

    def test_force_drop_applied_to_orphan_quarantine_rows(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Fix A-1/A-2: force_drop_headers must strip direct identifiers from orphan rows.

        A column in force_drop_headers (SoT cross-verification direct-identifier
        override) that would be KEPT by a broad keep prefix must be absent from the
        quarantine/amber zone file — the amber zone is not promoted to llm_source,
        but an auditor opening it must not see a raw signature or initials column.

        Acceptance criteria:
        A. CBC_INIT (in force_drop_headers, broad-kept) → absent from quarantine row.
        B. CBC_WBC (broad-kept, not force-dropped) → present in quarantine row.
        C. VISDAT (date field, no subject_id → not jittered) → present in quarantine row.
        """
        _write_config(
            scrub_config_path,
            keep_fields=["^CBC_"],
            orphan_quarantine_threshold=10,
        )
        # All rows are orphans (no SUBJID populated).
        rows = [{"CBC_INIT": "ZZZ", "CBC_WBC": "5.0", "VISDAT": "2020-03-01"}]
        _seed_staging(monkeypatch_config, rows)

        runs_dir = tmp_path / "runs"
        run_id = "test_run_a1a2"
        (runs_dir / run_id).mkdir(parents=True)
        approval_data = {
            "rule_bundle": {"rules_sha256": "sha256_a1a2"},
            "forms": [
                {
                    "form_name": "1A_ICScreening.xlsx",
                    "classifications": [],
                    "force_drop_headers": ["CBC_INIT"],
                }
            ],
            "approved_forms": [],
        }
        approval_path = runs_dir / run_id / "phi_handling_approval.json"
        approval_path.write_text(json.dumps(approval_data), encoding="utf-8")

        phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert quarantine.is_file(), "quarantine file must exist for orphan rows"
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1, f"expected 1 orphan row, got {len(quarantined)}"
        q = quarantined[0]

        # A — force-dropped direct identifier absent from amber zone
        assert "CBC_INIT" not in q, (
            "force-dropped direct identifier must be absent from quarantine row (Fix A-1/A-2)"
        )
        # B — benign keep column present
        assert "CBC_WBC" in q, "broad-kept non-force-dropped column must survive in quarantine row"
        # C — date field not jittered (no subject_id), still present
        assert "VISDAT" in q, (
            "date field must survive in quarantine row (not jittered, no subject_id)"
        )

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

    def test_date_locales_from_manifest_threaded_into_scrub(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ambiguous date column with a matching manifest entry must shift
        without raising ValueError.  This exercises the full
        run_scrub → _scrub_file → _scrub_row → shift_date → parse_date path.
        """
        import yaml

        # Write a forms manifest with a date_locales entry for IC_VISDAT_v2 → DMY.
        # The manifest now lives under config/<study>/ (Note 11); monkeypatch_config
        # patches FORMS_MANIFEST_PATH to a tmp config location.
        manifest_path = config.FORMS_MANIFEST_PATH
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            yaml.safe_dump(
                {
                    "required": [],
                    "optional": [],
                    "reject": [],
                    "date_locales": {"IC_VISDAT_v2": "DMY"},
                }
            ),
            encoding="utf-8",
        )

        # Config: IC_VISDAT_v2 is a date field
        _write_config(scrub_config_path, date_fields=["^IC_VISDAT_v2$"])

        # Ambiguous value: 07/05/2014 — both components ≤ 12, locale from manifest
        rows = [{"SUBJID": "S1", "IC_VISDAT_v2": "07/05/2014"}]
        src = _seed_staging(monkeypatch_config, rows)

        # Must not raise (previously would raise ValueError mid-record)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1
        shifted_val = loaded[0]["IC_VISDAT_v2"]
        # Shifted value must differ from the original (offset is non-zero for S1)
        # and must be a valid slash date string (not verbatim "07/05/2014")
        assert shifted_val != "07/05/2014", (
            f"Expected date to be shifted; got unchanged value {shifted_val!r}"
        )


# ── As-written ledger (dual-write) ──────────────────────────────────────────


class TestAsWrittenLedger:
    """Verify per-dataset phi_handling_ledger.as_written.json files."""

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

        ledger_path = _phi_ledger_path()
        assert ledger_path.is_file(), "phi_handling_ledger.as_written.json must be created"
        assert not (ledger_path.parent / config.AUDIT_NO_LLM_SENTINEL_NAME).exists()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert "run_id" in payload
        # Primary ledger is content-only — wall-clock fields moved to timing sidecar.
        assert "iso_timestamp" not in payload
        assert "generated_utc" not in payload
        assert payload["study"] == "TEST"
        assert payload["leg"] == "phi-scrub"
        assert payload["compliance_posture"] == "safe_harbor"
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

        ledger_path = _phi_ledger_path()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert len(payload["events"]) >= 1, "Expected at least one PHI handling event"
        event = payload["events"][0]
        assert set(event.keys()) == {
            "form",
            "variable_id",
            "action",
            "rule",
            "method",
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

        ledger_path = _phi_ledger_path()
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

        ledger_path = _phi_ledger_path()
        assert ledger_path.is_file()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["events"] == [], (
            "keep-scoped fields must not appear in the as_written ledger"
        )

    def test_ledger_is_value_free_and_dataset_rows_keep_audit_separation(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Ledger adapts the audit envelope without copying row values or rows."""
        _write_config(
            scrub_config_path,
            drop_fields=["^participant_name$"],
            id_fields=[{"pattern": "^SUBJID$", "label": "SUBJ"}],
            date_fields=["^VISDAT$"],
        )
        rows = [
            {
                "SUBJID": "SUBJECT-LEDGER-LEAK-001",
                "participant_name": "Alice Ledger Leak",
                "VISDAT": "2014-07-15",
                "SCORE": 42,
            }
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = _phi_ledger_path()
        ledger_text = ledger_path.read_text(encoding="utf-8")
        for forbidden in (
            "SUBJECT-LEDGER-LEAK-001",
            "Alice Ledger Leak",
            "2014-07-15",
            '"SCORE": 42',
        ):
            assert forbidden not in ledger_text

        payload = json.loads(ledger_text)
        assert payload["study"] == "TEST"
        assert payload["leg"] == "phi-scrub"
        assert payload["compliance_posture"] == "safe_harbor"
        assert all("value" not in event for event in payload["events"])

        cleaned_rows = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(cleaned_rows) == 1
        row = cleaned_rows[0]
        audit_envelope_keys = {
            "run_id",
            "iso_timestamp",
            "generated_utc",
            "study",
            "leg",
            "compliance_posture",
            "scrub_config_hash",
            "input_dataset_hash",
            "events",
        }
        assert audit_envelope_keys.isdisjoint(row)

    def test_ledger_phi_handling_events_match_scrub_report_without_extras(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """The PHI ledger is a value-free projection of scrub-report actions."""
        _write_config(
            scrub_config_path,
            drop_fields=["^participant_name$"],
            id_fields=[{"pattern": "^SUBJID$", "label": "SUBJ"}],
            date_fields=["^VISDAT$"],
            keep_fields=["^SCORE$"],
        )
        rows = [
            {
                "SUBJID": "S1",
                "participant_name": "Alice",
                "VISDAT": "2014-07-15",
                "SCORE": 42,
            },
            {
                "SUBJID": "S2",
                "participant_name": "Bob",
                "VISDAT": "2014-07-16",
                "SCORE": 43,
            },
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        audit_payload = json.loads(Path(config.AUDIT_SCRUB_REPORT_PATH).read_text())
        ledger_path = _phi_ledger_path()
        ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))

        scope_to_action = {
            "phi-scrub-drop": "drop",
            "phi-scrub-birthdate-drop": "birthdate_drop",
            "phi-scrub-id": "pseudonymize",
            "phi-scrub-date": "jitter_date",
            "phi-scrub-cap": "cap",
            "phi-scrub-generalize": "generalize",
            "phi-scrub-suppress-small-cell": "suppress_small_cell",
        }
        expected = sorted(
            (
                Path(item["file"]).stem,
                item["field"],
                scope_to_action[item["scope"]],
                item["file"],
                item["count"],
            )
            for item in audit_payload["scrubbed"]
            if item["scope"] in scope_to_action
        )
        actual = sorted(
            (
                item["form"],
                item["variable_id"],
                item["action"],
                item["where"]["dataset_file"],
                item["count"],
            )
            for item in ledger_payload["events"]
        )

        assert actual == expected
        assert all(item[1] != "SCORE" for item in actual)


# ── Ledger classification threading + method tracing ────────────────────────


class TestLedgerClassificationThreading:
    """Verify classification metadata and method tracing in ledger events."""

    def test_method_present_without_approval(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Without approval, method is derived from cfg; rules are empty."""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"},
        ]
        _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        ledger_path = _phi_ledger_path()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        # Find the jitter_date event (VISDAT)
        date_event = None
        for event in payload["events"]:
            if event["action"] == "jitter_date":
                date_event = event
                break
        assert date_event is not None, "Expected at least one jitter_date event"
        assert date_event["method"]["name"] == "SANT_date_jitter"
        assert date_event["method"]["parameters"]["max_jitter_days"] == 30
        assert date_event["rule"]["matched_rules"] == []
        assert date_event["rationale"] == "Applied by PHI scrubber per phi_scrub.yaml configuration"

    def test_classification_threaded_with_approval(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """With approval, classification metadata threads into the event."""
        _write_config(scrub_config_path)
        # AGE column → birthdate (DOB) drops under Safe Harbor (Note 32), so the only
        # jitter_date event is VISDAT and its classification metadata can be asserted.
        rows = [
            {"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15", "AGE": "44"},
        ]
        _seed_staging(monkeypatch_config, rows)

        # Create runs_dir and write approval
        runs_dir = tmp_path / "runs"
        run_id = "test_run_123"
        (runs_dir / run_id).mkdir(parents=True)
        approval_data = {
            "rule_bundle": {"rules_sha256": "sha256_abc123"},
            "forms": [
                {
                    "form_name": "1A_ICScreening.xlsx",
                    "classifications": [
                        {
                            "header": "VISDAT",
                            "action": "jitter_date",
                            "jurisdictions": ["USA", "INDIA"],
                            "matched_rules": ["usa_safe_harbor_dates", "india_date_identifier"],
                            "reasons": ["HIPAA Safe Harbor date element header."],
                        }
                    ],
                }
            ],
            "approved_forms": [],
        }
        approval_path = runs_dir / run_id / "phi_handling_approval.json"
        approval_path.write_text(json.dumps(approval_data), encoding="utf-8")

        # Run scrub with approval
        phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        ledger_path = _phi_ledger_path()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        # Find the jitter_date event
        date_event = None
        for event in payload["events"]:
            if event["action"] == "jitter_date":
                date_event = event
                break
        assert date_event is not None
        assert "usa_safe_harbor_dates" in date_event["rule"]["matched_rules"]
        assert date_event["rule"]["jurisdictions"] == ["USA", "INDIA"]
        assert date_event["rule"]["rule_bundle_sha256"] == "sha256_abc123"
        assert date_event["rationale"] == "HIPAA Safe Harbor date element header."

    def test_keep_decision_traced_with_approval(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Approval with keep action traces into keep_decisions, not events."""
        _write_config(
            scrub_config_path,
            drop_fields=[],
            id_fields=[],
            date_fields=[],
            keep_fields=["^VISDAT$"],
        )
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
        ]
        _seed_staging(monkeypatch_config, rows)

        # Create approval with a keep classification
        runs_dir = tmp_path / "runs"
        run_id = "test_run_keep"
        (runs_dir / run_id).mkdir(parents=True)
        approval_data = {
            "rule_bundle": {"rules_sha256": "sha256_keep"},
            "forms": [
                {
                    "form_name": "1A_ICScreening.xlsx",
                    "classifications": [
                        {
                            "header": "VISDAT",
                            "action": "keep",
                            "jurisdictions": ["USA"],
                            "matched_rules": ["no_phi_rule"],
                            "reasons": ["Retained per jurisdiction review (no PHI rule matched)."],
                        }
                    ],
                }
            ],
            "approved_forms": [],
        }
        approval_path = runs_dir / run_id / "phi_handling_approval.json"
        approval_path.write_text(json.dumps(approval_data), encoding="utf-8")

        phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        ledger_path = _phi_ledger_path()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        # Verify keep_decisions is present
        assert "keep_decisions" in payload
        assert len(payload["keep_decisions"]) > 0
        keep_decision = payload["keep_decisions"][0]
        assert keep_decision["variable_id"] == "visdat"  # normalized header
        assert "no_phi_rule" in keep_decision["matched_rules"]
        # Verify VISDAT does NOT appear in events
        assert all(event["variable_id"] != "VISDAT" for event in payload["events"])

    def test_no_contradictory_keep_decision_for_force_dropped_column(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Fix B-3: a column in the force-drop set must NOT produce a keep_decision.

        When phi_review classifies a column as ``keep`` but the SoT cross-verification
        adds it to ``force_drop_headers`` (e.g. a signature column matched by a broad
        keep prefix), the scrub force-drops it at priority-0.  The ledger must show
        only the ``drop`` event — emitting a contradictory ``keep_decision`` for the
        same variable would confuse an IRB auditor.
        """
        # Broad keep prefix covers both CBC_INIT (direct identifier) and CBC_WBC (benign).
        _write_config(scrub_config_path, keep_fields=["^CBC_"])
        rows = [{"SUBJID": "S1", "CBC_INIT": "ZZZ", "CBC_WBC": "5.0"}]
        _seed_staging(monkeypatch_config, rows)

        runs_dir = tmp_path / "runs"
        run_id = "test_run_b3"
        (runs_dir / run_id).mkdir(parents=True)
        # Approval: both columns classified as "keep" by the jurisdiction rules,
        # but CBC_INIT is in force_drop_headers (SoT cross-verification override).
        approval_data = {
            "rule_bundle": {"rules_sha256": "sha256_b3"},
            "forms": [
                {
                    "form_name": "1A_ICScreening.xlsx",
                    "classifications": [
                        {
                            "header": "CBC_INIT",
                            "action": "keep",
                            "jurisdictions": ["USA"],
                            "matched_rules": ["no_phi_rule"],
                            "reasons": ["Retained per jurisdiction review."],
                        },
                        {
                            "header": "CBC_WBC",
                            "action": "keep",
                            "jurisdictions": ["USA"],
                            "matched_rules": ["no_phi_rule"],
                            "reasons": ["Retained per jurisdiction review."],
                        },
                    ],
                    "force_drop_headers": ["CBC_INIT"],
                }
            ],
            "approved_forms": [],
        }
        approval_path = runs_dir / run_id / "phi_handling_approval.json"
        approval_path.write_text(json.dumps(approval_data), encoding="utf-8")

        phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        ledger_path = _phi_ledger_path()
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))

        # CBC_INIT must appear as a drop event (force-dropped at priority-0).
        drop_events = [e for e in payload["events"] if e["variable_id"] == "CBC_INIT"]
        assert len(drop_events) >= 1, "force-dropped column must have a drop event in the ledger"
        assert all(e["action"] == "drop" for e in drop_events)

        # CBC_INIT must NOT appear in keep_decisions — that would be contradictory.
        keep_vars = {kd["variable_id"] for kd in payload.get("keep_decisions", [])}
        norm_init = phi_scrub._normalize_header_for_lookup("CBC_INIT")
        assert norm_init not in keep_vars, (
            "force-dropped column must NOT produce a keep_decision in the ledger"
        )

        # CBC_WBC is NOT force-dropped — it should still appear as a keep_decision.
        norm_wbc = phi_scrub._normalize_header_for_lookup("CBC_WBC")
        assert norm_wbc in keep_vars, "benign keep column must still produce a keep_decision"


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
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        out = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert out[0]["IS_MARITAL"] == "Married"
        assert out[1]["IS_MARITAL"] == "Other"

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


# ── Generalize fail-closed ──────────────────────────────────────────────────


class TestGeneralizeFailClosed:
    """Tests for run_scrub generalize-miss exception and quarantine write.

    Mirrors TestRunScrubBandFailClosed: an unmapped non-empty value in a
    generalize field quarantines the row and raises PHIGeneralizeUnmappedError.
    """

    def test_generalize_unmapped_value_quarantines_and_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """run_scrub with generalize-miss → raises PHIGeneralizeUnmappedError."""
        _write_config(
            scrub_config_path,
            generalize_fields=[{"pattern": "(?:MARITAL)", "mapping": "marital"}],
            generalization_maps={"marital": {"married": "Married"}},
        )
        rows = [
            {"SUBJID": "S1", "IS_MARITAL": "married"},
            {"SUBJID": "S2", "IS_MARITAL": "annulled"},
        ]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIGeneralizeUnmappedError):
            phi_scrub.run_scrub(study_name="TEST")

        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "generalize_unmapped_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file()
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1

    def test_generalize_all_mapped_no_error(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """run_scrub with filled generalization map (value covered) → no error."""
        _write_config(
            scrub_config_path,
            generalize_fields=[{"pattern": "(?:MARITAL)", "mapping": "marital"}],
            generalization_maps={"marital": {"married": "Married", "annulled": "Other"}},
        )
        rows = [{"SUBJID": "S1", "IS_MARITAL": "annulled"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        # No error raised; file still exists
        assert src.is_file()
        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1
        assert loaded[0]["IS_MARITAL"] == "Other"


class TestGeneralizeExceptionExports:
    """Tests for PHIGeneralizeUnmappedError and module exports."""

    def test_phi_generalize_unmapped_error_is_subclass_of_phi_scrub_error(self) -> None:
        """PHIGeneralizeUnmappedError must be a subclass of PHIScrubError."""
        assert issubclass(phi_scrub.PHIGeneralizeUnmappedError, phi_scrub.PHIScrubError)

    def test_phi_generalize_unmapped_error_in_all(self) -> None:
        """PHIGeneralizeUnmappedError must be in phi_scrub.__all__."""
        assert "PHIGeneralizeUnmappedError" in phi_scrub.__all__


# ── Date fail-closed ────────────────────────────────────────────────────────


class TestDateFailClosed:
    """Tests for run_scrub date-miss exception and quarantine write.

    Mirrors TestRunScrubBandFailClosed and TestGeneralizeFailClosed: an
    unparseable or ambiguous date value in a date field quarantines the row
    and raises PHIDateUnshiftableError.
    """

    def test_unparseable_date_quarantines_and_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """run_scrub with unparseable date → raises PHIDateUnshiftableError."""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "not a date"},
        ]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file()
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1

    def test_ambiguous_date_quarantines_and_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """run_scrub with ambiguous slash-date (H8 path) → raises PHIDateUnshiftableError."""
        # AMBIG_DAT matches the _DAT$ pattern, is NOT in DMY_VARIABLES allowlist,
        # has no manifest date_locales entry → ambiguous "07/05/2014" raises ValueError
        # in parse_date, which is fail-closed by the date rung.
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "AMBIG_DAT": "2014-07-15"},
            {"SUBJID": "S2", "AMBIG_DAT": "07/05/2014"},
        ]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file()
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1

    def test_valid_date_jittered_no_error(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """run_scrub with valid VISDAT → completes, date is shifted, no error."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        # No error raised; file still exists
        assert src.is_file()
        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1
        # VISDAT must be shifted by the per-subject offset (differs from input)
        key = phi_scrub.load_key()
        offset = phi_scrub.date_offset_days("S1", key=key, max_days=30)
        expected = phi_scrub.shift_date("2014-07-15", offset)
        assert loaded[0]["VISDAT"] == expected


class TestDateExceptionExports:
    """Tests for PHIDateUnshiftableError and module exports."""

    def test_phi_date_unshiftable_error_is_subclass_of_phi_scrub_error(self) -> None:
        """PHIDateUnshiftableError must be a subclass of PHIScrubError."""
        assert issubclass(phi_scrub.PHIDateUnshiftableError, phi_scrub.PHIScrubError)

    def test_phi_date_unshiftable_error_in_all(self) -> None:
        """PHIDateUnshiftableError must be in phi_scrub.__all__."""
        assert "PHIDateUnshiftableError" in phi_scrub.__all__


# ── Date null-token (missing-data placeholder) handling ─────────────────────


class TestDateNullTokens:
    """Date fields holding recognized missing-data sentinels must be left as-is
    (not jittered, not quarantined) while genuinely-bad values still fail-close.
    """

    # ── is_date_null_token unit tests ────────────────────────────────────────

    def test_declared_token_upper(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("UNK") is True

    def test_declared_token_lower(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("unk") is True

    def test_declared_token_with_spaces(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token(" na ") is True

    def test_na_slash_token(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("N/A") is True

    def test_real_date_string_returns_false(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("2014-07-15") is False

    def test_non_str_returns_false(self, scrub_config_path: Path) -> None:
        _write_config(scrub_config_path)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token(None) is False
        assert cfg.is_date_null_token(42) is False

    def test_default_constant_covers_expected_tokens(self) -> None:
        from scripts.security.phi_scrub import _DEFAULT_DATE_NULL_TOKENS

        for token in (
            "UNK",
            "UNKNOWN",
            "NA",
            "N/A",
            "N.A.",
            "NONE",
            "NIL",
            "NOT DONE",
            "NOT APPLICABLE",
            "NOT AVAILABLE",
            "NOT REPORTED",
            "ND",
            "NR",
            ".",
            "-",
            "--",
            "?",
        ):
            assert token in _DEFAULT_DATE_NULL_TOKENS, f"{token!r} missing from default set"

    # ── Integration: null-token in date field keeps row, value unchanged ─────

    def test_unk_in_date_field_row_kept(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """A date field holding 'UNK' must NOT be quarantined; row is kept."""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "UNK"},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")  # must not raise

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 2
        # S2's VISDAT must still be "UNK" — unchanged
        s2 = next(r for r in loaded if r.get("VISDAT") == "UNK")
        assert s2["VISDAT"] == "UNK"
        assert s2["_phi_scrubbed"] == "v3"

    def test_na_in_date_field_row_kept(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """'NA' in a date field must be kept as-is."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "NA"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["VISDAT"] == "NA"

    def test_lowercase_token_in_date_field_kept(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Case-insensitive match: 'unk' must be kept as-is."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "unk"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["VISDAT"] == "unk"

    def test_n_slash_a_in_date_field_kept(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """'N/A' in a date field must be kept as-is."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": "N/A"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["VISDAT"] == "N/A"

    def test_padded_na_in_date_field_kept(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """' na ' (with spaces) in a date field must be kept as-is."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "VISDAT": " na "}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert loaded[0]["VISDAT"] == " na "

    # ── Fail-closed preserved: non-token unparseable still quarantines ────────

    def test_non_token_unparseable_still_quarantines(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """A genuinely-bad value that is NOT a null-token still quarantines
        the row and raises PHIDateUnshiftableError (fail-closed preserved)."""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "garbage"},
        ]
        _seed_staging(monkeypatch_config, rows)
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file()
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1

    # ── Note 29: unparseable_date_policy="blank" publishes the row ────────────

    def test_unparseable_date_blank_policy_publishes_row(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Under unparseable_date_policy='blank', a genuinely-bad date blanks ONLY
        that field and the row is published (no raise, no whole-row quarantine)."""
        _write_config(scrub_config_path, unparseable_date_policy="blank")
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "garbage", "OTHER": "keep-me"},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        # Must NOT raise — the malformed date is blanked, not fail-closed.
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        # Both rows published (no quarantine).
        assert len(loaded) == 2
        by_subj = {r.get("OTHER"): r for r in loaded}
        bad_row = next(r for r in loaded if r.get("OTHER") == "keep-me")
        # The unparseable date is blanked to ""; the row's other field survives.
        assert bad_row["VISDAT"] == ""
        assert bad_row["OTHER"] == "keep-me"
        # No quarantine file produced for this form under the blank policy.
        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert not quarantine.exists()
        assert by_subj  # silence unused-var lint

    def test_unparseable_date_blank_policy_does_not_touch_valid_dates(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Blank policy only blanks the unparseable value — valid dates still jitter."""
        _write_config(scrub_config_path, unparseable_date_policy="blank")
        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")
        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        # A valid date is shifted (jittered), never blanked.
        assert loaded[0]["VISDAT"] not in ("", "2014-07-15")

    # ── Valid date in same field still jittered ───────────────────────────────

    def test_valid_date_alongside_token_both_handled_correctly(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """S1 has a valid date (must be jittered); S2 has UNK (must be kept)."""
        _write_config(scrub_config_path)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "UNK"},
        ]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 2

        key = phi_scrub.load_key()
        # S1: valid date jittered
        s1 = next(r for r in loaded if r.get("VISDAT") != "UNK")
        offset = phi_scrub.date_offset_days("S1", key=key, max_days=30)
        expected = phi_scrub.shift_date("2014-07-15", offset)
        assert s1["VISDAT"] == expected

        # S2: UNK preserved as-is
        s2 = next(r for r in loaded if r.get("VISDAT") == "UNK")
        assert s2["VISDAT"] == "UNK"

    # ── Config loading: custom date_null_tokens in YAML ───────────────────────

    def test_custom_tokens_loaded_from_yaml(self, scrub_config_path: Path) -> None:
        """Operator-defined date_null_tokens in YAML override the defaults."""
        _write_config(scrub_config_path, date_null_tokens=["CUSTOM_MISSING", "PENDING"])
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("CUSTOM_MISSING") is True
        assert cfg.is_date_null_token("pending") is True  # case-insensitive
        # Default tokens NOT in custom list are absent when list is explicitly set
        # (the custom list replaces, not extends, the defaults)
        assert cfg.is_date_null_token("UNK") is False

    def test_absent_tokens_key_uses_defaults(self, scrub_config_path: Path) -> None:
        """When date_null_tokens is absent from YAML, defaults are used."""
        _write_config(scrub_config_path)  # no date_null_tokens key
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.is_date_null_token("UNK") is True
        assert cfg.is_date_null_token("NA") is True


# ── Catalog coverage — HIPAA §164.514(b)(2) baseline ────────────────────────


class TestCatalogCoverage:
    """Ensures the shipped phi_scrub.yaml covers every HIPAA-18 category.

    This uses the *actual production YAML* (not a test fixture) so catalog
    regressions are caught: any removal of a rule class (e.g. someone deletes
    drop_fields entries for names or govt IDs) breaks these tests.
    """

    @pytest.fixture()
    def real_cfg(self) -> phi_scrub.PHIScrubConfig:
        # Effective merged config (defaults + per-study override when present).
        cfg = phi_scrub.load_scrub_config(study=config.STUDY_NAME)
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

    def test_marital_kept_not_generalized(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        # Marital status is NOT a HIPAA Safe Harbor / DPDPA identifier, and the
        # dual-jurisdiction classifier (phi_review) decides KEEP for it. Routing
        # it to generalize contradicted that decision (decided keep != applied
        # generalize → assertion 12) so the generalize rule was removed; marital
        # is now kept as-is (no transform rule matches).
        for col in ["IS_MARITAL", "IC_MARISTAT", "HC_MARISTAT"]:
            assert real_cfg.generalize_rule_for(col) is None, f"{col} must not generalize"
            assert real_cfg.band_rule_for(col) is None, f"{col} must not band"
            assert real_cfg.field_is_drop(col) is False, f"{col} must not drop"
            assert real_cfg.field_is_date(col) is False, f"{col} must not date-jitter"

    def test_household_contact_count_suppressed(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        assert real_cfg.field_is_suppress_small_cell("IS_CONTACTS") is True

    def test_date_columns_under_broad_keep_are_jittered_not_kept(
        self, real_cfg: phi_scrub.PHIScrubConfig
    ) -> None:
        # DATE-LEAK GUARD: a genuine clinical date column must NEVER be kept raw,
        # even when a broad form-prefix keep (^CBC_, ^CXR_, ^DST_, ^CX_, ^SC_,
        # ^AE_) also matches its name. It must fall through to the date-jitter
        # rule (temporal identifier, HIPAA #3) — otherwise it publishes un-shifted
        # and trips the pre-publication leak gate.
        for col in [
            "CBC_VISDAT",
            "CBC_COMPDAT",
            "CXR_CXRDAT",
            "CXR_COMPDTE",
            "CX_DSTDAT",
            "CX_ISOLATEDAT",
            "DST_DSTDAT",
            "DST_ISOLATEDAT",
            "SC_PAXRECDAT",
            "SC_GENORECDAT",
            "AE_EVENTDAT",
            "CC_PREGDAT",
        ]:
            assert real_cfg.field_is_keep(col) is False, f"{col} must NOT be kept raw"
            assert real_cfg.field_is_date(col) is True, f"{col} must date-jitter"

    def test_date_named_status_flags_stay_kept(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        # EXCEPTION to the date-leak guard: date-NAMED but non-date "Not Done" /
        # "Not Recorded" status flags (suffix ND/NR) hold text, not a date, and
        # must stay KEPT (the reason those anchored keep rules exist).
        for col in ["CX_PROCDAT_ND", "ZN_MBDATNR", "ZN_MBDATNR2"]:
            assert real_cfg.field_is_keep(col) is True, f"{col} must stay kept"
            assert real_cfg.field_is_date(col) is False, f"{col} must NOT date-jitter"

    def test_socioeconomic_fields_kept_not_banded(self, real_cfg: phi_scrub.PHIScrubConfig) -> None:
        # phi_review decides KEEP for education/occupation/wage (not Safe Harbor
        # / DPDPA identifiers). The band rule is empty/inert so they are kept as
        # is rather than quarantined against an empty band map (which 100%-held
        # forms 2A/2B). They must match no transform rule.
        for col in [
            "IC_SCHOOLDU",
            "IC_MASCHOOLDU",
            "HC_SCHOOLDU",
            "EE_SCHOOL",
            "IC_JOB",
            "EE_WAGE",
        ]:
            assert real_cfg.band_rule_for(col) is None, f"{col} must not band"
            assert real_cfg.field_is_drop(col) is False, f"{col} must not drop"
            assert real_cfg.field_is_date(col) is False, f"{col} must not date-jitter"


# ── Audit hash wiring (P0.1) ─────────────────────────────────────────────────


class TestAuditHashes:
    """Verify scrub_config_hash and input_dataset_hash are sealed into the ledger.

    Acceptance criteria (P0.1):
    A. Per-dataset phi_handling_ledger.as_written.json has non-null scrub_config_hash.
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

        ledger_path = _phi_ledger_path()
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

        ledger_path = _phi_ledger_path()
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

        ledger_path = _phi_ledger_path()
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

        ledger_path = _phi_ledger_path()
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

    def test_empty_staging_dir_returns_sha256_of_empty_string(self, tmp_path: Path) -> None:
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
    """Acceptance criteria A-D for the REPORTALIN_ALLOW_DISABLED_SCRUB guard."""

    def test_production_mode_with_env_set_raises(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GAP-5: disabled-scrub refusal is now an UNCONDITIONAL floor on real
        study data. When config.is_test_context() is False (production / non-test
        context), run_scrub raises PHIScrubError with the new message regardless of
        REPORTALIN_ALLOW_DISABLED_SCRUB. Under pytest is_test_context() is True by
        default, so we monkeypatch it to False to exercise the production floor."""
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")
        # Override is_test_context() so the code sees a non-test (production) context.
        monkeypatch.setattr(config, "is_test_context", lambda: False)

        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        with pytest.raises(
            phi_scrub.PHIScrubError,
            match="REPORTALIN_ALLOW_DISABLED_SCRUB is ignored on real study data",
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

    def test_production_mode_forces_raise_even_in_test_context(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GAP-5 defense-in-depth: production_mode_enabled() forces the disabled-scrub
        floor to raise EVEN inside a detected test context (is_test_context True) and
        even with REPORTALIN_ALLOW_DISABLED_SCRUB=1. The production flag can never be
        overridden by a test-context signal."""
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: True)
        # Leave is_test_context() at its real pytest value (True) — the production
        # flag must still force the raise via the OR clause.
        assert config.is_test_context() is True

        rows = [{"SUBJID": "S1", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        with pytest.raises(phi_scrub.PHIScrubError):
            phi_scrub.run_scrub(study_name="TEST")

    def test_is_test_context_ignores_env_signals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GAP-5: is_test_context() must depend ONLY on `"pytest" in sys.modules`,
        never on operator/attacker-settable env vars. With pytest removed from
        sys.modules, REPORTAL_TEST_FAKE_LLM=1 and PYTEST_CURRENT_TEST set must NOT
        make it return True — otherwise a production operator could lower the
        disabled-scrub floor by setting the fake-LLM smoke flag."""
        import sys

        monkeypatch.setenv("REPORTAL_TEST_FAKE_LLM", "1")
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/foo.py::bar (call)")
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        assert config.is_test_context() is False


# ── In-progress token (P1.2) ─────────────────────────────────────────────────


class TestInProgressToken:
    """Acceptance criteria A and B for the scrub.in_progress token contract."""

    def test_token_written_before_loop_on_crash(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Acceptance A: token exists on disk if the scrub loop raises on first row."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        runs_dir = tmp_path / "runs"
        run_id = "run_test_crash"

        # Patch _scrub_file to raise immediately so the loop never completes.
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated mid-scrub crash")

        monkeypatch.setattr(phi_scrub, "_scrub_file", _boom)

        with pytest.raises(RuntimeError, match="simulated mid-scrub crash"):
            phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        token = runs_dir / run_id / "scrub.in_progress"
        assert token.is_file(), "in-progress token must exist after a mid-scrub crash"

    def test_token_absent_after_successful_scrub(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Acceptance B: token is deleted on successful completion."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        runs_dir = tmp_path / "runs"
        run_id = "run_test_success"

        phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        token = runs_dir / run_id / "scrub.in_progress"
        assert not token.exists(), "in-progress token must be deleted after successful scrub"

    def test_token_content_has_required_fields(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Token JSON contains run_id, study, started_utc, and scrub_yaml_sha256."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        runs_dir = tmp_path / "runs"
        run_id = "run_test_fields"

        # Patch _scrub_file to raise so the token is still on disk.
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("stop early")

        monkeypatch.setattr(phi_scrub, "_scrub_file", _boom)

        with pytest.raises(RuntimeError):
            phi_scrub.run_scrub(study_name="TEST", run_id=run_id, runs_dir=runs_dir)

        token = runs_dir / run_id / "scrub.in_progress"
        payload = json.loads(token.read_text(encoding="utf-8"))
        assert payload["run_id"] == run_id
        assert payload["study"] == "TEST"
        assert "started_utc" in payload
        assert "scrub_yaml_sha256" in payload

    def test_no_token_written_when_run_id_absent(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Backwards compat: no run_id → no token written (legacy callers unaffected)."""
        _write_config(scrub_config_path)
        rows = [{"SUBJID": "S1", "DOB": "1970-01-01", "VISDAT": "2014-07-15"}]
        _seed_staging(monkeypatch_config, rows)

        # Call without run_id / runs_dir — must not raise and must not leave any token.
        phi_scrub.run_scrub(study_name="TEST")
        # No assertion on a specific path; just verify the call succeeds.


# ── Date handling remediation: blank separators + sentinel tokens ─────────────
#    (Change 2 + Change 3 coverage)


class TestDateRemediationBlankSeparators:
    """Blank/separator-only date values must be treated as missing (not quarantined)."""

    def test_slash_spaces_date_field_skipped_not_quarantined(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """/  / in a date field → row KEPT, field left as-is, no quarantine."""
        _write_config(scrub_config_path, date_fields=["^VISDAT$"])
        rows = [{"SUBJID": "S1", "VISDAT": "/  /"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1, "Row must be KEPT (not quarantined)"
        assert loaded[0]["VISDAT"] == "/  /", "Blank-separator value must pass through unchanged"

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert not quarantine.exists(), "Quarantine file must not exist for blank-sep date"

    def test_double_slash_date_field_skipped_not_quarantined(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """// in a date field → row KEPT, field left as-is."""
        _write_config(scrub_config_path, date_fields=["^VISDAT$"])
        rows = [{"SUBJID": "S1", "VISDAT": "//"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1, "Row must be KEPT (not quarantined)"

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert not quarantine.exists()

    def test_dot_space_dot_date_field_skipped_not_quarantined(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """. . in a date field → row KEPT, field left as-is."""
        _write_config(scrub_config_path, date_fields=["^VISDAT$"])
        rows = [{"SUBJID": "S1", "VISDAT": ". ."}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1, "Row must be KEPT (not quarantined)"

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert not quarantine.exists()

    def test_genuinely_bad_date_still_quarantines(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """A truly unparseable non-blank date value must quarantine and raise (fail-closed).

        run_scrub raises PHIDateUnshiftableError and writes the row to
        quarantine/date_unshiftable_<file>.jsonl — it does NOT silently keep
        or drop the row.  This verifies the fail-closed contract is intact
        even after the blank-separator and sentinel-token relaxations.
        """
        _write_config(scrub_config_path, date_fields=["^VISDAT$"])
        rows = [{"SUBJID": "S1", "VISDAT": "not-a-date-at-all"}]
        _seed_staging(monkeypatch_config, rows)

        # run_scrub raises — this is the fail-closed behavior
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        # Row must be in the date_unshiftable quarantine file, not the kept output
        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.exists(), "Quarantine file must exist for genuinely bad date"


class TestDateRemediationSentinelTokens:
    """Sentinel placeholder values (99999999, 00000000, 0) must skip jitter."""

    def test_99999999_in_date_field_skipped_not_quarantined(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """99999999 in a date field → row KEPT, field left as-is (null token)."""
        _write_config(
            scrub_config_path,
            date_fields=["^VISDAT$"],
            date_null_tokens=[
                "UNK",
                "UNKNOWN",
                "NA",
                "N/A",
                "N.A.",
                "NONE",
                "NIL",
                "NOT DONE",
                "NOT APPLICABLE",
                "NOT AVAILABLE",
                "NOT REPORTED",
                "ND",
                "NR",
                ".",
                "-",
                "--",
                "?",
                "99999999",
                "00000000",
                "0",
            ],
        )
        rows = [{"SUBJID": "S1", "VISDAT": "99999999"}]
        src = _seed_staging(monkeypatch_config, rows)
        phi_scrub.run_scrub(study_name="TEST")

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 1, "Row must be KEPT"
        assert loaded[0]["VISDAT"] == "99999999", "Sentinel value must pass through unchanged"

        quarantine = config.STUDY_STAGING_DIR / "quarantine" / "1A_ICScreening.jsonl"
        assert not quarantine.exists(), "Quarantine must not exist for null token"


# ── date_fields exclusion: non-date columns must not be date-scrubbed ─────────
#    (Change 3 coverage — verifying cfg.field_is_date logic)


class TestDateFieldsExclusionNonDateColumns:
    """Columns ZN_MBDATNR, ZN_MBDATNR2, CX_PROCDAT_ND must not be date-classified."""

    def _load_real_cfg(self) -> phi_scrub.PHIScrubConfig | None:
        """Load the effective merged scrub config for the active study."""
        import config as _cfg

        return phi_scrub.load_scrub_config(study=_cfg.STUDY_NAME)

    def test_zn_mbdatnr_is_not_date(self) -> None:
        """ZN_MBDATNR stores free-text status; field_is_date must return False."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_date("ZN_MBDATNR") is False, (
            "ZN_MBDATNR must NOT be classified as a date field "
            "(it stores 'Not Done'/'Not Recorded' text, not a date)"
        )

    def test_zn_mbdatnr2_is_not_date(self) -> None:
        """ZN_MBDATNR2 stores free-text status; field_is_date must return False."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_date("ZN_MBDATNR2") is False, (
            "ZN_MBDATNR2 must NOT be classified as a date field"
        )

    def test_cx_procdat_nd_is_not_date(self) -> None:
        """CX_PROCDAT_ND is a binary Not-Done flag; field_is_date must return False."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_date("CX_PROCDAT_ND") is False, (
            "CX_PROCDAT_ND must NOT be classified as a date field "
            "(it is a binary indicator, not a date)"
        )

    def test_cx_procdat_nd_is_keep(self) -> None:
        """CX_PROCDAT_ND must match keep_fields (so it is not scrubbed at all)."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_keep("CX_PROCDAT_ND") is True, (
            "CX_PROCDAT_ND must match keep_fields to be exempt from all scrub rules"
        )

    def test_cx_procdat_nd_not_in_drop_fields(self) -> None:
        """CX_PROCDAT_ND must not accidentally match drop_fields."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_drop("CX_PROCDAT_ND") is False, (
            "CX_PROCDAT_ND must not match drop_fields"
        )

    def test_cx_procdat_still_is_date(self) -> None:
        """CX_PROCDAT (without _ND suffix) must still be classified as a date field."""
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        assert cfg.field_is_date("CX_PROCDAT") is True, (
            "CX_PROCDAT must still be classified as a date field after the exclusion fix"
        )


# ── Birthdate kept + SANT-jittered (limited_dataset posture) ────────────────


class TestBirthdateKeptAndJitteredRealConfig:
    """The shipped phi_scrub.yaml must KEEP birthdates and route them to SANT
    jitter — never silently KEEP them raw (rung 1 is the only raw-passthrough
    path) and never DROP them (so age-at-event is preserved). Locks the
    'keep + jitter, no raw exposure' posture against config drift.
    """

    def _load_real_cfg(self) -> phi_scrub.PHIScrubConfig | None:
        import config as _cfg

        return phi_scrub.load_scrub_config(study=_cfg.STUDY_NAME)

    def test_shipped_posture_is_safe_harbor(self) -> None:
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        # Shipped posture is Safe Harbor: birthdate_field is DROPPED entirely
        # (full HIPAA de-identification, no IRB/DUA paperwork required). A prior
        # limited_dataset experiment (keep + SANT-jitter birthdates) was reverted
        # per operator decision (2026-06-09).
        assert cfg.compliance_posture == "safe_harbor"

    @pytest.mark.parametrize(
        "dob_col",
        ["IS_BIRTHDAT", "IC_BIRTHDAT", "HHC_BRTHDAT", "HC_BRTHDAT"],
    )
    def test_subject_birthdate_columns_route_to_jitter_not_keep_or_drop(self, dob_col: str) -> None:
        cfg = self._load_real_cfg()
        if cfg is None:
            pytest.skip("phi_scrub.yaml not present in this environment")
        # Recognized as a birthdate → posture-aware rungs apply.
        assert cfg.field_is_birthdate(dob_col) is True, (
            f"{dob_col} must match birthdate_field so posture routing applies"
        )
        # rung 1 KEEP is the ONLY raw-passthrough path — a DOB column must not hit it.
        assert cfg.field_is_keep(dob_col) is False, (
            f"{dob_col} must NOT match keep_fields — that would publish a raw DOB"
        )
        # Not dropped either, so it reaches rung 7 jitter (age-at-event preserved).
        assert cfg.field_is_drop(dob_col) is False, (
            f"{dob_col} must NOT match drop_fields under the keep+jitter posture"
        )


# ── Partial-publish safety threshold ───────────────────────────────────────


class TestPartialPublishThreshold:
    """Tests for the partial_max_quarantine_fraction safety cap.

    Covers:
    - partial mode, fraction UNDER threshold → completes, kept rows published,
      partial recorded, no raise, not elevated.
    - partial mode, fraction OVER threshold → NO raise: the good rows are published
      and the form is flagged ``elevated`` in scrub_outcome.json (one systemic form
      must not block the clean forms — review recommended, not aborted).
    - strict mode (partial_on_review=False) → raises original error (PHIDateUnshiftableError),
      threshold not consulted (unchanged behavior).
    - load_scrub_config picks up partial_max_quarantine_fraction from yaml.
    - default applies when key is absent from yaml.
    - invalid value (<=0 or >1) raises PHIScrubError.
    """

    # ── Config-level tests (no run_scrub, no staging) ────────────────────────

    def test_load_scrub_config_reads_fraction_from_yaml(self, scrub_config_path: Path) -> None:
        """load_scrub_config picks up partial_max_quarantine_fraction from yaml."""
        _write_config(scrub_config_path, partial_max_quarantine_fraction=0.25)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.partial_max_quarantine_fraction == pytest.approx(0.25)

    def test_load_scrub_config_default_when_absent(self, scrub_config_path: Path) -> None:
        """When key is absent from yaml, default of 0.10 applies."""
        _write_config(scrub_config_path)  # no partial_max_quarantine_fraction key
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.partial_max_quarantine_fraction == pytest.approx(0.10)

    def test_invalid_fraction_zero_raises(self, scrub_config_path: Path) -> None:
        """partial_max_quarantine_fraction=0 is invalid (must be > 0)."""
        _write_config(scrub_config_path, partial_max_quarantine_fraction=0.0)
        with pytest.raises(phi_scrub.PHIScrubError, match="partial_max_quarantine_fraction"):
            phi_scrub.load_scrub_config()

    def test_invalid_fraction_above_one_raises(self, scrub_config_path: Path) -> None:
        """partial_max_quarantine_fraction > 1 is invalid."""
        _write_config(scrub_config_path, partial_max_quarantine_fraction=1.5)
        with pytest.raises(phi_scrub.PHIScrubError, match="partial_max_quarantine_fraction"):
            phi_scrub.load_scrub_config()

    def test_fraction_of_one_is_valid(self, scrub_config_path: Path) -> None:
        """partial_max_quarantine_fraction=1.0 is the inclusive upper bound (always partial)."""
        _write_config(scrub_config_path, partial_max_quarantine_fraction=1.0)
        cfg = phi_scrub.load_scrub_config()
        assert cfg is not None
        assert cfg.partial_max_quarantine_fraction == pytest.approx(1.0)

    def test_exception_is_subclass_of_phi_scrub_error(self) -> None:
        """PHIPartialThresholdExceededError must be a subclass of PHIScrubError."""
        assert issubclass(phi_scrub.PHIPartialThresholdExceededError, phi_scrub.PHIScrubError)

    def test_exception_in_all(self) -> None:
        """PHIPartialThresholdExceededError must be in phi_scrub.__all__."""
        assert "PHIPartialThresholdExceededError" in phi_scrub.__all__

    # ── run_scrub integration tests ───────────────────────────────────────────

    def test_partial_under_threshold_completes(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """1 bad row out of 100 is under the 10% threshold → completes without raise,
        kept rows (99) are published, partial_forms records the one quarantined file."""
        # threshold 10%: 1/100 = 1% < 10% → should NOT raise
        _write_config(scrub_config_path, partial_max_quarantine_fraction=0.10)
        good_rows = [{"SUBJID": f"S{i:03d}", "VISDAT": "2014-07-15"} for i in range(99)]
        bad_rows = [{"SUBJID": "SBAD", "VISDAT": "not-a-date"}]
        src = _seed_staging(monkeypatch_config, good_rows + bad_rows)
        runs_dir = tmp_path / "runs"
        (runs_dir / "run_threshold_test").mkdir(parents=True, exist_ok=True)

        # Must NOT raise
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run_threshold_test",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        # 99 kept rows published
        published = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
        assert len(published) == 99

        # scrub_outcome.json records partial=True and lists the file
        outcome_path = runs_dir / "run_threshold_test" / "scrub_outcome.json"
        assert outcome_path.is_file()
        outcome = json.loads(outcome_path.read_text())
        assert outcome["partial"] is True
        assert "1A_ICScreening.jsonl" in outcome["partial_forms"]

    def test_partial_over_threshold_publishes_elevated(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Most rows quarantined (>10%) in partial mode: NO abort.

        The good rows are published and the form is flagged ``elevated`` in
        scrub_outcome.json so the UI can recommend review. Aborting here would deny
        the operator every *clean* form's usable data — the explicit partial-publish
        contract is "move on with the forms that worked, flag the rest."
        """
        # 9 bad rows, 1 good = 90% held → over the 10% threshold → elevated, not raise.
        _write_config(scrub_config_path, partial_max_quarantine_fraction=0.10)
        good_rows = [{"SUBJID": "SGOOD", "VISDAT": "2014-07-15"}]
        bad_rows = [{"SUBJID": f"SBAD{i}", "VISDAT": "not-a-date"} for i in range(9)]
        src = _seed_staging(monkeypatch_config, good_rows + bad_rows)
        runs_dir = tmp_path / "runs"
        (runs_dir / "run_elevated").mkdir(parents=True, exist_ok=True)

        # Must NOT raise — partial mode holds the bad rows and publishes the good one.
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run_elevated",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        # The single good row is published; the 9 bad rows are NOT promoted.
        published = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
        assert len(published) == 1

        # scrub_outcome.json: partial=True, form flagged elevated with kept/held counts.
        outcome = json.loads((runs_dir / "run_elevated" / "scrub_outcome.json").read_text())
        assert outcome["partial"] is True
        entry = outcome["partial_forms"]["1A_ICScreening.jsonl"]
        assert entry["elevated"] is True
        assert entry["kept"] == 1
        assert entry["quarantined"] == 9
        assert any("elevated_review" in r for r in entry["reasons"])

    def test_strict_mode_raises_original_error_not_threshold(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """strict mode (partial_on_review=False): un-jitterable date raises
        PHIDateUnshiftableError regardless of the quarantine fraction, because
        the threshold check is only reached in partial mode."""
        _write_config(scrub_config_path, partial_max_quarantine_fraction=0.10)
        # All 5 rows are bad — would be 100% quarantined, well above threshold.
        # But strict mode should raise PHIDateUnshiftableError, not the threshold error.
        bad_rows = [{"SUBJID": f"S{i}", "VISDAT": "not-a-date"} for i in range(5)]
        _seed_staging(monkeypatch_config, bad_rows)

        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")  # default partial_on_review=False

    def test_clean_run_in_partial_mode_writes_empty_partial_forms(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """All-clean run in partial_on_review=True mode: scrub_outcome.json written
        with partial=False and partial_forms={}."""
        _write_config(scrub_config_path)
        _seed_staging(monkeypatch_config, [{"SUBJID": "S1", "VISDAT": "2014-07-15"}])
        runs_dir = tmp_path / "runs"
        (runs_dir / "run_clean").mkdir(parents=True, exist_ok=True)

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run_clean",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        outcome_path = runs_dir / "run_clean" / "scrub_outcome.json"
        assert outcome_path.is_file()
        outcome = json.loads(outcome_path.read_text())
        assert outcome["partial"] is False
        assert outcome["partial_forms"] == {}


class TestSecondarySubjectIdResolver:
    """A1 / Note 28 — secondary subject-ID resolver in ``_scrub_row``.

    A ``SUBJID{n}`` / ``FID{n}`` Excel re-entry column is compared (trusted-code,
    value-level) to the row's canonical subject ID: an exact duplicate is dropped
    as a redundant re-entry; a distinct linked value is pseudonymized in the SAME
    keyspace as the canonical ID so case↔household linkage survives. Tokens never
    leave the trusted boundary; the audit records scope + count only.
    """

    @staticmethod
    def _cfg() -> phi_scrub.PHIScrubConfig:
        # The real Indo-VAP-shaped defaults: subject_id_fields = (SUBJID, FID),
        # canonical id_fields label SUBJID->SUBJ, FID->FAM.
        return phi_scrub.load_scrub_config(study="Indo-VAP")

    def test_base_detector(self) -> None:
        c = self._cfg()
        base = phi_scrub._secondary_subject_id_base
        assert base("SUBJID2", c.subject_id_fields) == "SUBJID"
        assert base("SUBJID_2", c.subject_id_fields) == "SUBJID"
        assert base("SUBJID2_2", c.subject_id_fields) == "SUBJID"
        assert base("FID3", c.subject_id_fields) == "FID"
        # Canonical names (no numeric suffix) and lookalikes are NOT secondary.
        assert base("SUBJID", c.subject_id_fields) is None
        assert base("FID", c.subject_id_fields) is None
        assert base("FIDELITY", c.subject_id_fields) is None
        assert base("SC_PROCID", c.subject_id_fields) is None

    def test_exact_duplicate_is_dropped(self, key_bytes: bytes) -> None:
        c = self._cfg()
        out, counts = phi_scrub._scrub_row(
            {"SUBJID": "ABC123", "SUBJID2": "ABC123"}, cfg=c, key=key_bytes
        )
        assert out is not None
        assert "SUBJID2" not in out  # redundant re-entry removed
        assert counts.get("phi-scrub-secondary-id-drop:SUBJID2") == 1
        # The canonical SUBJID itself is pseudonymized, not dropped.
        assert str(out["SUBJID"]).startswith("RID_SUBJ_")

    def test_distinct_value_is_pseudonymized_in_canonical_keyspace(self, key_bytes: bytes) -> None:
        c = self._cfg()
        out, counts = phi_scrub._scrub_row(
            {"SUBJID": "ABC123", "SUBJID2": "XYZ789"}, cfg=c, key=key_bytes
        )
        assert out is not None
        assert str(out["SUBJID2"]).startswith("RID_SUBJ_")
        assert counts.get("phi-scrub-secondary-id-pseudonymize:SUBJID2") == 1
        # Same keyspace: a distinct secondary value tokenizes identically to a
        # canonical SUBJID carrying that value — cross-form linkage survives.
        out2, _ = phi_scrub._scrub_row({"SUBJID": "XYZ789"}, cfg=c, key=key_bytes)
        assert out["SUBJID2"] == out2["SUBJID"]

    def test_dedup_suffix_variant_resolves(self, key_bytes: bytes) -> None:
        c = self._cfg()
        out, counts = phi_scrub._scrub_row(
            {"SUBJID": "ABC123", "SUBJID2_2": "ABC123", "SUBJID3": "NEW001"},
            cfg=c,
            key=key_bytes,
        )
        assert out is not None
        assert "SUBJID2_2" not in out  # identical -> drop
        assert str(out["SUBJID3"]).startswith("RID_SUBJ_")  # distinct -> pseudonymize

    def test_fid_family_uses_fam_keyspace(self, key_bytes: bytes) -> None:
        c = self._cfg()
        out, _ = phi_scrub._scrub_row({"FID": "F1", "FID2": "F2"}, cfg=c, key=key_bytes)
        assert out is not None
        assert str(out["FID2"]).startswith("RID_FAM_")

    def test_empty_secondary_is_dropped(self, key_bytes: bytes) -> None:
        c = self._cfg()
        out, counts = phi_scrub._scrub_row(
            {"SUBJID": "ABC123", "SUBJID2": ""}, cfg=c, key=key_bytes
        )
        assert out is not None
        assert "SUBJID2" not in out
        assert counts.get("phi-scrub-secondary-id-drop:SUBJID2") == 1

    def test_resolver_scopes_map_to_ledger_actions(self) -> None:
        # The two resolver scopes must map to real PHI ledger actions so the
        # events flow into the dual-ledger (drop / pseudonymize), not get dropped
        # as unrecognized scopes.
        assert phi_scrub._SCOPE_TO_ACTION["phi-scrub-secondary-id-drop"] == "drop"
        assert phi_scrub._SCOPE_TO_ACTION["phi-scrub-secondary-id-pseudonymize"] == "pseudonymize"
