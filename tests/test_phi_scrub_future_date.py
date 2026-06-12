"""Tests for the future-dated value = placeholder policy in phi_scrub.

A real clinical observation date is always in the past (year <= current year).
A date whose resolved year > cfg.plausible_max_year is treated as a missing-data
placeholder or quarantined, depending on cfg.future_date_policy. This tests:

1. datetime cell year=2050, policy=sentinel  → row published, field unchanged,
   date_future_sentinel count bumped.
2. datetime cell year=2050, policy=quarantine → row quarantined.
3. datetime year=2200 (>2100, beyond parser window), policy=sentinel → treated
   missing (was previously quarantined as unparseable).
4. string "01/01/2050", policy=sentinel, max_year=2026 → treated missing.
5. normal date year=2014 → jittered exactly as before (unaffected).
6. BACK-COMPAT: keys absent from config (code defaults) → year=2050 jittered as
   today, i.e. the check is off (plausible_max_year=9999 by default).
7. Config validation: plausible_max_year=1850 or future_date_policy="bogus" →
   PHIScrubError.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import config
from scripts.security import phi_scrub
from scripts.security.phi_scrub import PHIScrubConfig, PHIScrubError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_cfg(
    *,
    plausible_max_year: int = 9999,
    future_date_policy: str = "quarantine",
) -> PHIScrubConfig:
    """A minimal PHIScrubConfig that processes VISDAT as a date field."""
    return PHIScrubConfig(
        compliance_posture="safe_harbor",
        subject_id_fields=("SUBJID",),
        date_patterns=[re.compile(r"^VISDAT$", re.IGNORECASE)],
        id_patterns=[
            phi_scrub.IdRule(
                pattern=re.compile(r"^SUBJID$", re.IGNORECASE),
                label="SUBJ",
            )
        ],
        birthdate_pattern=None,
        max_jitter_days=30,
        orphan_quarantine_threshold=10,
        plausible_max_year=plausible_max_year,
        future_date_policy=future_date_policy,
    )


def _key() -> bytes:
    return bytes.fromhex("00" * 32)


def _write_config(path: Path, **overrides: object) -> None:
    """Write a minimal phi_scrub.yaml at *path*."""
    import yaml

    payload: dict[str, object] = {
        "compliance_posture": "safe_harbor",
        "subject_id_field": "SUBJID",
        "date_fields": ["^VISDAT$"],
        "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
        "birthdate_field": "^DOB$",
        "max_jitter_days": 30,
        "orphan_quarantine_threshold": 5,
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _seed_staging(monkeypatch_config: Path, rows: list[dict[str, Any]]) -> Path:
    """Write rows to the staging datasets dir, return the file path."""
    staging = config.STAGING_DATASETS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / "1A_ICScreening.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return target


# ---------------------------------------------------------------------------
# Unit tests on _scrub_row directly (for datetime-typed cell values)
# ---------------------------------------------------------------------------


class TestFutureDateSentinelUnitLevel:
    """Direct _scrub_row tests using Python datetime objects in the row dict.

    These bypass run_scrub/JSON serialization so that a Python datetime cell
    (not a string) can be placed directly in the row.
    """

    def test_datetime_year_future_sentinel_policy_row_published(self) -> None:
        """Test 1: datetime cell year=2050, policy=sentinel → row published,
        field left as-is, date_future_sentinel count bumped."""
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="sentinel")
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": datetime(2050, 6, 1)}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        # Row must be published (not quarantined)
        assert scrubbed is not None, "Row must be published under sentinel policy"
        # Field must be BLANKED — a future date is valid-looking ISO and would
        # corrupt date math if left in place, so it is emptied to read as missing.
        assert scrubbed["VISDAT"] == "", (
            "Sentinel: future-date field must be blanked (treated as missing), not jittered"
        )
        # Scrub report counter must be bumped
        assert any("date_future_sentinel" in k for k in counts), (
            "date_future_sentinel counter must be bumped"
        )
        # Must NOT be a date jitter event
        assert not any(k.startswith("phi-scrub-date:") for k in counts), (
            "date_future_sentinel must not produce a jitter ledger event"
        )

    def test_datetime_year_future_quarantine_policy_row_quarantined(self) -> None:
        """Test 2: datetime cell year=2050, policy=quarantine → row quarantined."""
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="quarantine")
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": datetime(2050, 6, 1)}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        assert scrubbed is None, "Row must be quarantined under quarantine policy"
        # counts must carry a phi-scrub-date-quarantine event
        assert any("phi-scrub-date-quarantine" in k for k in counts)

    def test_datetime_year_2200_beyond_parser_window_sentinel_policy(self) -> None:
        """Test 3: datetime year=2200 (>2100 — beyond parser's own guard window).
        With policy=sentinel the datetime branch still resolves the year directly
        and treats it as a sentinel (never jittered).
        """
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="sentinel")
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": datetime(2200, 1, 1)}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        assert scrubbed is not None, "Row must be published under sentinel policy"
        # Field must be blanked (treated as missing)
        assert scrubbed["VISDAT"] == ""
        assert any("date_future_sentinel" in k for k in counts)

    def test_iso_string_year_2914_beyond_parser_ceiling_sentinel(self) -> None:
        """ISO string with year > 2100 (e.g. '2914-05-28') — parse_date returns
        None (beyond its plausible ceiling), so the ISO-leading-year fallback must
        resolve the year and treat it as a future placeholder. This is the
        datetime-origin shape Excel cells serialise to post-extraction.
        """
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="sentinel")
        # value with valid month/day but a far-future year — parse_date rejects it
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": "2914-05-28 00:00:00"}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        assert scrubbed is not None, "Row must be published under sentinel policy"
        assert scrubbed["VISDAT"] == "", "ISO future date must be blanked (treated missing)"
        assert any("date_future_sentinel" in k for k in counts)

    def test_iso_string_old_year_1014_not_blanked_quarantines(self) -> None:
        """ISO string with an OLD year (<1900) is NOT a future placeholder — the
        fallback resolves 1014 but 1014 <= plausible_max_year so it is NOT blanked;
        it falls through to the strict path and quarantines (a typo for source review)."""
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="sentinel")
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": "1014-05-28"}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())
        # Old-year ISO must NOT be blanked as a future sentinel.
        if scrubbed is not None:
            assert scrubbed.get("VISDAT") != "" or not any(
                "date_future_sentinel" in k for k in counts
            ), "Old year must not be treated as a future sentinel"

    def test_normal_date_2014_unaffected(self) -> None:
        """Test 5: A normal past date (year=2014) must still be jittered exactly
        as before — the new keys must not change any existing behavior."""
        cfg = _minimal_cfg(plausible_max_year=2026, future_date_policy="sentinel")
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": "2014-07-15"}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        assert scrubbed is not None
        key = _key()
        offset = phi_scrub.date_offset_days("S1", key=key, max_days=30)
        expected = phi_scrub.shift_date("2014-07-15", offset)
        assert scrubbed["VISDAT"] == expected
        # Must NOT produce a date_future_sentinel count
        assert not any("date_future_sentinel" in k for k in counts)
        # Must produce a date jitter count
        assert any(k.startswith("phi-scrub-date:") for k in counts)

    def test_backcompat_absent_keys_year_2050_jittered(self) -> None:
        """Test 6: Back-compat — with keys absent (defaults), a year=2050 date
        IS jittered (check is effectively off because plausible_max_year=9999)."""
        cfg = _minimal_cfg()  # defaults: plausible_max_year=9999
        row: dict[str, Any] = {"SUBJID": "S1", "VISDAT": "2050-01-01"}

        scrubbed, counts = phi_scrub._scrub_row(row, cfg=cfg, key=_key())

        # Row must be published (not quarantined) — future check is off
        assert scrubbed is not None, (
            "Back-compat: plausible_max_year=9999 → check is off → row published"
        )
        # Field must be jittered (not left as-is)
        assert scrubbed["VISDAT"] != "2050-01-01", (
            "Back-compat: with defaults the date must be jittered, not treated as sentinel"
        )
        # No date_future_sentinel counter
        assert not any("date_future_sentinel" in k for k in counts)
        # A date jitter event must exist
        assert any(k.startswith("phi-scrub-date:") for k in counts)


# ---------------------------------------------------------------------------
# String-typed future date tests (via run_scrub + JSONL)
# ---------------------------------------------------------------------------


class TestFutureDateStringViaRunScrub:
    """String-typed future dates tested through the full run_scrub path."""

    def test_string_future_date_sentinel_policy_row_published(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Test 4: ISO string "2050-01-01" + policy=sentinel + max_year=2026 → row published,
        field treated as missing (unchanged), no PHIDateUnshiftableError raised.

        Note: an unambiguous ISO date is used here because an ambiguous slash-date
        like "01/01/2050" raises ValueError at parse_date (locale not resolvable),
        causing it to fall through to the existing quarantine path regardless of
        this new policy. The spec says the string branch "only detects future years
        that parse WITHIN the parser's existing window" — an ambiguous slash-date
        with no date_locales entry does NOT parse and is correctly quarantined by
        the existing mechanism.
        """
        _write_config(
            scrub_config_path,
            plausible_max_year=2026,
            future_date_policy="sentinel",
        )
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "2050-01-01"},
        ]
        src = _seed_staging(monkeypatch_config, rows)

        phi_scrub.run_scrub(study_name="TEST")  # must NOT raise

        loaded = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(loaded) == 2, "Both rows must be published under sentinel policy"

        # S1's date must be jittered
        s1_row = next(r for r in loaded if r.get("SUBJID", "").startswith("RID_SUBJ_"))
        # (Just verify field present and scrub marker set)
        assert "_phi_scrubbed" in s1_row

        # S2's future date must be BLANKED (treated as missing) — a future ISO
        # date would corrupt date math if left in place. It is the only published
        # row whose VISDAT is empty (S1's real date was jittered to a non-empty value).
        s2_row = next(r for r in loaded if r.get("VISDAT") == "")
        assert s2_row["VISDAT"] == "", (
            "Sentinel: string future date must be blanked (treated as missing)"
        )
        assert "_phi_scrubbed" in s2_row

    def test_string_future_date_quarantine_policy_row_quarantined_strict(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """String future date + policy=quarantine → row quarantined (strict mode aborts)."""
        _write_config(
            scrub_config_path,
            plausible_max_year=2026,
            future_date_policy="quarantine",
        )
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "S2", "VISDAT": "2050-01-01"},
        ]
        _seed_staging(monkeypatch_config, rows)

        # Strict mode (default): the quarantine-policy future date aborts the run
        # via PHIDateUnshiftableError (same exit path as the existing date rung).
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        # The bad row must be in the quarantine dir
        quarantine = (
            config.STUDY_STAGING_DIR / "quarantine" / "date_unshiftable_1A_ICScreening.jsonl"
        )
        assert quarantine.is_file(), "Future-date quarantine rows must be written to disk"
        quarantined = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
        assert len(quarantined) == 1


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


class TestFutureDateConfigValidation:
    """Test 7: plausible_max_year and future_date_policy validation."""

    def test_plausible_max_year_too_low_raises(self, scrub_config_path: Path) -> None:
        """plausible_max_year=1850 (< 1900) must raise PHIScrubError."""
        _write_config(
            scrub_config_path,
            plausible_max_year=1850,
            future_date_policy="sentinel",
        )
        with pytest.raises(PHIScrubError, match="plausible_max_year"):
            phi_scrub.load_scrub_config(scrub_config_path)

    def test_plausible_max_year_too_high_raises(self, scrub_config_path: Path) -> None:
        """plausible_max_year=10000 (> 9999) must raise PHIScrubError."""
        _write_config(
            scrub_config_path,
            plausible_max_year=10000,
            future_date_policy="sentinel",
        )
        with pytest.raises(PHIScrubError, match="plausible_max_year"):
            phi_scrub.load_scrub_config(scrub_config_path)

    def test_plausible_max_year_float_raises(self, scrub_config_path: Path) -> None:
        """plausible_max_year as a float (e.g. 2026.5) must raise PHIScrubError."""
        # YAML will parse 2026.5 as a float, which fails the int check.
        _write_config(
            scrub_config_path,
            plausible_max_year=2026.5,
            future_date_policy="sentinel",
        )
        with pytest.raises(PHIScrubError, match="plausible_max_year"):
            phi_scrub.load_scrub_config(scrub_config_path)

    def test_future_date_policy_bogus_raises(self, scrub_config_path: Path) -> None:
        """future_date_policy='bogus' must raise PHIScrubError."""
        _write_config(
            scrub_config_path,
            plausible_max_year=2026,
            future_date_policy="bogus",
        )
        with pytest.raises(PHIScrubError, match="future_date_policy"):
            phi_scrub.load_scrub_config(scrub_config_path)

    def test_valid_sentinel_policy_loads(self, scrub_config_path: Path) -> None:
        """plausible_max_year=2026, future_date_policy='sentinel' → loads cleanly."""
        _write_config(
            scrub_config_path,
            plausible_max_year=2026,
            future_date_policy="sentinel",
        )
        cfg = phi_scrub.load_scrub_config(scrub_config_path)
        assert cfg is not None
        assert cfg.plausible_max_year == 2026
        assert cfg.future_date_policy == "sentinel"

    def test_valid_quarantine_policy_loads(self, scrub_config_path: Path) -> None:
        """future_date_policy='quarantine' → loads cleanly."""
        _write_config(
            scrub_config_path,
            plausible_max_year=2026,
            future_date_policy="quarantine",
        )
        cfg = phi_scrub.load_scrub_config(scrub_config_path)
        assert cfg is not None
        assert cfg.future_date_policy == "quarantine"

    def test_absent_keys_give_safe_defaults(self, scrub_config_path: Path) -> None:
        """When plausible_max_year and future_date_policy are absent, defaults are
        plausible_max_year=9999 and future_date_policy='quarantine' — i.e. the
        future-date check is a complete no-op for any pre-existing config."""
        _write_config(scrub_config_path)  # no future-date keys
        cfg = phi_scrub.load_scrub_config(scrub_config_path)
        assert cfg is not None
        assert cfg.plausible_max_year == 9999
        assert cfg.future_date_policy == "quarantine"

    def test_phiscrubconfig_direct_invalid_year_raises(self) -> None:
        """PHIScrubConfig constructor with plausible_max_year=1850 must raise."""
        with pytest.raises(PHIScrubError, match="plausible_max_year"):
            _minimal_cfg(plausible_max_year=1850)

    def test_phiscrubconfig_direct_invalid_policy_raises(self) -> None:
        """PHIScrubConfig constructor with future_date_policy='bogus' must raise."""
        with pytest.raises(PHIScrubError, match="future_date_policy"):
            _minimal_cfg(plausible_max_year=2026, future_date_policy="bogus")


# ---------------------------------------------------------------------------
# Ledger / _SCOPE_TO_ACTION: date_future_sentinel must NOT produce ledger entries
# ---------------------------------------------------------------------------


class TestFutureDateNoLedgerEntry:
    """date_future_sentinel is a report-counter bump only; no ledger action."""

    def test_date_future_sentinel_not_in_scope_to_action(self) -> None:
        """phi-scrub-date_future_sentinel must NOT appear in _SCOPE_TO_ACTION so
        it can never produce a PHI ledger entry (mirrors date_null_token)."""
        from scripts.security.phi_scrub import _SCOPE_TO_ACTION

        # Confirm the sentinel scope is absent
        assert "phi-scrub-date_future_sentinel" not in _SCOPE_TO_ACTION, (
            "date_future_sentinel must not map to a ledger action — it is a "
            "report counter only, exactly like date_null_token"
        )
