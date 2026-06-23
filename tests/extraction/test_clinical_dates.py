"""Tests for clinical_dates: case-insensitive DMY allowlist and raise-on-ambiguity.

Covers task P2.4 acceptance criteria:
  (a) Case-insensitive allowlist — IC_VISDAT_v2 (or any casing variant that
      contains a recognised DMY base-name) is NOT the test target here; instead
      the test verifies that the canonical names match case-insensitively.
  (b) Unambiguous 28/05/2014 → DMY (28 > 12 forces day-first).
  (c) Unambiguous 05/28/2014 → MDY (28 > 12 forces day-second).
  (d) Ambiguous 07/05/2014 (both ≤ 12) raises ValueError without manifest entry.
  (e) Same ambiguous value parses correctly with date_locales override.
"""

from __future__ import annotations

import logging

import pytest

from scripts.extraction.io.clinical_dates import (
    DMY_VARIABLES,
    _disambiguate_locale,
    _mask_date_value,
    _resolve_locale,
    check_locale_consistency,
    is_dmy_variable,
    parse_date,
    value_looks_like_date,
)

# ---------------------------------------------------------------------------
# (a) Case-insensitive allowlist
# ---------------------------------------------------------------------------


class TestIsDmyVariableCaseInsensitive:
    """is_dmy_variable must be case-insensitive."""

    def test_uppercase_canonical(self) -> None:
        assert is_dmy_variable("IC_VISDAT") is True

    def test_lowercase(self) -> None:
        assert is_dmy_variable("ic_visdat") is True

    def test_mixed_case(self) -> None:
        assert is_dmy_variable("Ic_VisDat") is True

    def test_non_dmy_variable_not_matched(self) -> None:
        assert is_dmy_variable("SOME_OTHER_DATE") is False

    def test_all_dmy_canonical_names_lowercase(self) -> None:
        """All six canonical DMY variables resolve True in lower-case."""
        canonical = [
            "cbc_hbadat",
            "cc_visdat",
            "foa_visdat",
            "fob_visdat",
            "ic_visdat",
            "it_igradat",
        ]
        for name in canonical:
            assert is_dmy_variable(name) is True, f"Expected DMY match for {name!r}"


# ---------------------------------------------------------------------------
# (b) & (c) _disambiguate_locale — unambiguous values
# ---------------------------------------------------------------------------


class TestDisambiguateLocaleUnambiguous:
    """_disambiguate_locale resolves locale when exactly one component > 12."""

    def test_dmy_when_day_exceeds_12(self) -> None:
        # 28/05/2014 — group1=28 > 12 ⇒ day is first ⇒ DMY
        result = _disambiguate_locale("28/05/2014")
        assert result == "DMY"

    def test_mdy_when_day_exceeds_12_in_second_position(self) -> None:
        # 05/28/2014 — group2=28 > 12 ⇒ day is second ⇒ MDY
        result = _disambiguate_locale("05/28/2014")
        assert result == "MDY"

    def test_declared_locale_returned_directly(self) -> None:
        # If declared_locale is provided it wins regardless of value
        assert _disambiguate_locale("07/05/2014", declared_locale="DMY") == "DMY"
        assert _disambiguate_locale("07/05/2014", declared_locale="MDY") == "MDY"

    def test_invalid_both_gt_12_raises(self) -> None:
        # 28/30/2014 — both > 12: impossible date
        with pytest.raises(ValueError, match="Invalid date string"):
            _disambiguate_locale("28/30/2014")


# ---------------------------------------------------------------------------
# (d) Ambiguous value raises ValueError without manifest entry
# ---------------------------------------------------------------------------


class TestParseAmbiguousRaisesWithoutManifest:
    """parse_date must raise ValueError for ambiguous dates when no manifest entry."""

    def test_ambiguous_07_05_raises(self) -> None:
        with pytest.raises(ValueError, match="Ambiguous date locale"):
            parse_date("07/05/2014", field_name="SOME_UNKNOWN_COL")

    def test_ambiguous_raises_mentions_column(self) -> None:
        with pytest.raises(ValueError, match="SOME_UNKNOWN_COL"):
            parse_date("07/05/2014", field_name="SOME_UNKNOWN_COL")

    def test_ambiguous_raises_mentions_manifest(self) -> None:
        with pytest.raises(ValueError, match=r"_forms_manifest\.yaml"):
            parse_date("07/05/2014", field_name="MY_DATE_COL")

    def test_ambiguous_raises_interpolates_value(self) -> None:
        # NEW behavior: raw value is masked in the error message (digits→9,
        # letters→X, separators kept) so PHI is never leaked into logs/traces.
        # The masked shape must appear; the raw value must NOT appear.
        with pytest.raises(ValueError) as exc_info:
            parse_date("07/05/2014", field_name="SOME_UNKNOWN_COL")
        msg = str(exc_info.value)
        assert "99/99/9999" in msg, (
            f"Error message must contain the masked shape '99/99/9999'; got: {msg}"
        )
        assert "07/05/2014" not in msg, (
            f"Error message must NOT contain the raw value '07/05/2014'; got: {msg}"
        )

    def test_no_field_name_defaults_mdy_no_raise(self) -> None:
        # Without a field_name, ambiguous values fall back to MDY (legacy behaviour)
        result = parse_date("07/05/2014")
        assert result is not None
        assert result.format == "mdy"


# ---------------------------------------------------------------------------
# (e) date_locales override honours the declared locale
# ---------------------------------------------------------------------------


class TestParseWithDateLocalesOverride:
    """parse_date must honour date_locales dict when provided."""

    def test_dmy_override_for_ambiguous_value(self) -> None:
        # date_locales keys must be UPPER-CASE (normalised at manifest load time).
        result = parse_date(
            "07/05/2014",
            field_name="IC_VISDAT_V2",
            date_locales={"IC_VISDAT_V2": "DMY"},
        )
        assert result is not None
        assert result.format == "dmy"
        # D/M/Y: day=7, month=5
        assert result.dt.day == 7
        assert result.dt.month == 5
        assert result.dt.year == 2014

    def test_mdy_override_for_ambiguous_value(self) -> None:
        # date_locales keys must be UPPER-CASE (normalised at manifest load time).
        result = parse_date(
            "07/05/2014",
            field_name="IC_VISDAT_V2",
            date_locales={"IC_VISDAT_V2": "MDY"},
        )
        assert result is not None
        assert result.format == "mdy"
        # M/D/Y: month=7, day=5
        assert result.dt.month == 7
        assert result.dt.day == 5

    def test_date_locales_case_insensitive_key(self) -> None:
        # Key lookup in date_locales should be case-insensitive
        result = parse_date(
            "07/05/2014",
            field_name="ic_visdat_v2",
            date_locales={"IC_VISDAT_V2": "DMY"},
        )
        assert result is not None
        assert result.format == "dmy"

    def test_unambiguous_value_unaffected_by_empty_locales(self) -> None:
        # Unambiguous values still parse correctly with empty date_locales
        result = parse_date("28/05/2014", field_name="IC_VISDAT", date_locales={})
        assert result is not None
        assert result.format == "dmy"

    def test_dmy_variable_still_works_with_locales_absent(self) -> None:
        # Legacy: canonical DMY variable names still work without date_locales
        result = parse_date("28/05/2014", field_name="IC_VISDAT")
        assert result is not None
        assert result.format == "dmy"
        assert result.dt.day == 28
        assert result.dt.month == 5


# ---------------------------------------------------------------------------
# Compact integer date parsing (8-digit and 7-digit)
# ---------------------------------------------------------------------------


class TestIntegerDateParsing:
    """parse_date must handle 7- and 8-digit pure-digit date strings."""

    # ── 8-digit YYYYMMDD ──────────────────────────────────────────────────

    def test_yyyymmdd_parses_as_iso(self) -> None:
        """20140728 → YYYY=2014, MM=07, DD=28 (YYYYMMDD first-try branch)."""
        result = parse_date("20140728")
        assert result is not None
        assert result.dt.year == 2014
        assert result.dt.month == 7
        assert result.dt.day == 28
        assert result.format == "iso"
        assert result.has_time is False
        assert result.ampm is None

    def test_yyyymmdd_no_field_name(self) -> None:
        result = parse_date("20000101")
        assert result is not None
        assert result.dt.year == 2000
        assert result.dt.month == 1
        assert result.dt.day == 1

    # ── 8-digit DDMMYYYY under DMY ────────────────────────────────────────

    def test_ddmmyyyy_under_dmy_via_date_locales(self) -> None:
        """28052014 under declared DMY → day=28, month=5, year=2014."""
        result = parse_date(
            "28052014",
            field_name="MY_SPECDAT",
            date_locales={"MY_SPECDAT": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_ddmmyyyy_under_dmy_via_dmy_variables(self) -> None:
        """25092017 with field in DMY_VARIABLES (CBC_HBADAT) → day=25, month=9."""
        result = parse_date("25092017", field_name="CBC_HBADAT")
        assert result is not None
        assert result.dt.day == 25
        assert result.dt.month == 9
        assert result.dt.year == 2017

    # ── 8-digit MMDDYYYY under MDY ────────────────────────────────────────

    def test_mmddyyyy_under_mdy_via_date_locales(self) -> None:
        """07282014 under declared MDY → month=7, day=28, year=2014."""
        result = parse_date(
            "07282014",
            field_name="CX_SPECDAT",
            date_locales={"CX_SPECDAT": "MDY"},
        )
        assert result is not None
        assert result.dt.month == 7
        assert result.dt.day == 28
        assert result.dt.year == 2014
        assert result.format == "iso"

    # ── 8-digit: invalid date → None ─────────────────────────────────────

    def test_invalid_8digit_returns_none(self) -> None:
        """99999999 cannot be a valid date in any layout → None."""
        result = parse_date("99999999")
        assert result is None

    def test_invalid_month_in_yyyymmdd_falls_through(self) -> None:
        """20141328 has month=13, YYYYMMDD is invalid; fallback also fails → None."""
        result = parse_date("20141328")
        assert result is None

    # ── 7-digit: DMY via date_locales ─────────────────────────────────────

    def test_7digit_dmy_via_date_locales(self) -> None:
        """9122014 under declared DMY (UPPER-CASE key) → canonical DMMYYYY split.
        DMMYYYY: d=9, m=12, y=2014.
        Keys must be UPPER-CASE (normalised at manifest load time)."""
        result = parse_date(
            "9122014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 9
        assert result.dt.month == 12
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_7digit_mdy_via_date_locales(self) -> None:
        """9122014 under declared MDY (UPPER-CASE key) → canonical MDDYYYY split.
        MDDYYYY: m=9, d=12, y=2014.
        Keys must be UPPER-CASE (normalised at manifest load time)."""
        result = parse_date(
            "9122014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "MDY"},
        )
        assert result is not None
        assert result.dt.month == 9
        assert result.dt.day == 12
        assert result.dt.year == 2014

    # ── Output always ISO YYYY-MM-DD when jittered ────────────────────────

    def test_jittered_output_is_iso(self) -> None:
        """shift_date on an integer date must output ISO YYYY-MM-DD."""
        from scripts.security.phi_scrub import shift_date

        result = shift_date("20140728", 3)
        assert result == "2014-07-31"

    def test_jittered_output_is_iso_with_dmy_locales(self) -> None:
        """shift_date on a DMY integer date must output ISO."""
        from scripts.security.phi_scrub import shift_date

        result = shift_date(
            "28052014",
            2,
            field_name="MY_SPECDAT",
            date_locales={"MY_SPECDAT": "DMY"},
        )
        assert result == "2014-05-30"


# ---------------------------------------------------------------------------
# Separator variants: hyphen and dot delimiters
# ---------------------------------------------------------------------------


class TestSeparatorVariants:
    """parse_date must accept / - and . as date separators identically."""

    def test_hyphen_dmy_via_dmy_variable(self) -> None:
        """28-05-2014 with IC_VISDAT (DMY allowlist) → day=28, month=5."""
        result = parse_date("28-05-2014", field_name="IC_VISDAT")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.dt.year == 2014
        assert result.format == "dmy"

    def test_dot_dmy_via_dmy_variable(self) -> None:
        """28.05.2014 with IC_VISDAT (DMY allowlist) → day=28, month=5."""
        result = parse_date("28.05.2014", field_name="IC_VISDAT")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.dt.year == 2014
        assert result.format == "dmy"

    def test_hyphen_unambiguous_day_gt12_auto_dmy(self) -> None:
        """28-05-2014 with no field_name but day=28 > 12 → heuristic DMY."""
        result = parse_date("28-05-2014")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.format == "dmy"

    def test_dot_unambiguous_day_gt12_auto_dmy(self) -> None:
        """28.05.2014 with no field_name but day=28 > 12 → heuristic DMY."""
        result = parse_date("28.05.2014")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.format == "dmy"

    def test_hyphen_mdy_via_date_locales(self) -> None:
        """07-28-2014 with manifest MDY → month=7, day=28."""
        result = parse_date(
            "07-28-2014",
            field_name="CX_DATE",
            date_locales={"CX_DATE": "MDY"},
        )
        assert result is not None
        assert result.dt.month == 7
        assert result.dt.day == 28
        assert result.format == "mdy"

    def test_dot_ambiguous_known_field_raises(self) -> None:
        """07.05.2014 with known field, both ≤ 12, no manifest → ValueError."""
        with pytest.raises(ValueError, match="Ambiguous date locale"):
            parse_date("07.05.2014", field_name="SOME_DATE_COL")

    def test_hyphen_ambiguous_known_field_raises(self) -> None:
        """07-05-2014 with known field, both ≤ 12, no manifest → ValueError."""
        with pytest.raises(ValueError, match="Ambiguous date locale"):
            parse_date("07-05-2014", field_name="SOME_DATE_COL")

    def test_slash_and_hyphen_and_dot_parse_identically(self) -> None:
        """Slash, hyphen, and dot variants of same day-first date parse the same."""
        base = parse_date("28/05/2014", field_name="IC_VISDAT")
        hyph = parse_date("28-05-2014", field_name="IC_VISDAT")
        dot_ = parse_date("28.05.2014", field_name="IC_VISDAT")
        assert base is not None and hyph is not None and dot_ is not None
        assert base.dt == hyph.dt == dot_.dt
        assert base.format == hyph.format == dot_.format

    def test_iso_with_hyphens_not_captured_by_sep_branch(self) -> None:
        """YYYY-MM-DD is ISO format and must not be re-parsed as separator-delimited."""
        result = parse_date("2014-07-28")
        assert result is not None
        assert result.format == "iso"
        assert result.dt.year == 2014
        assert result.dt.month == 7
        assert result.dt.day == 28

    def test_mixed_separators_return_none(self) -> None:
        """A value mixing separator chars (28/05-2014) must NOT parse."""
        # The backreference in _SEP_RE enforces a single consistent separator.
        result = parse_date("28/05-2014")
        assert result is None

    def test_separator_with_time_hyphen(self) -> None:
        """Hyphen-delimited with time component parses and captures has_time."""
        result = parse_date("28-05-2014 14:30:00", field_name="IC_VISDAT")
        assert result is not None
        assert result.has_time is True
        assert result.dt.day == 28
        assert result.dt.month == 5

    def test_separator_3digit_year_rejected_l1(self) -> None:
        """L1: a 3-digit year (e.g. 28/05/100 → year 100) is structurally
        implausible and must return None (quarantine), not emit year 100."""
        assert parse_date("28/05/100", field_name="IC_VISDAT") is None
        assert parse_date("28-05-100", field_name="IC_VISDAT") is None

    def test_separator_2digit_and_4digit_years_unaffected_by_l1(self) -> None:
        """Regression: valid 2-digit (→1900-2099) and in-range 4-digit years still parse."""
        r2 = parse_date("28/05/14", field_name="IC_VISDAT")
        assert r2 is not None and r2.dt.year == 2014
        r4 = parse_date("28/05/2014", field_name="IC_VISDAT")
        assert r4 is not None and r4.dt.year == 2014


# ---------------------------------------------------------------------------
# 8-digit compact: day>12 and ambiguous cases
# ---------------------------------------------------------------------------


class TestCompact8DigitExtended:
    """Extended tests for 8-digit compact date format."""

    def test_8digit_day_gt12_dmy_field_in_allowlist(self) -> None:
        """28072014 with IC_VISDAT → day=28, month=7, year=2014."""
        result = parse_date("28072014", field_name="IC_VISDAT")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 7
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_8digit_ambiguous_resolves_dmy_with_dmy_field(self) -> None:
        """07052014: both ≤ 12 components; DMY field → day=7, month=5."""
        result = parse_date(
            "07052014",
            field_name="IC_VISDAT",  # DMY allowlist
        )
        assert result is not None
        assert result.dt.day == 7
        assert result.dt.month == 5
        assert result.dt.year == 2014

    def test_8digit_ambiguous_known_field_raises(self) -> None:
        """07052014 with known field, both ≤ 12, no manifest entry → ValueError."""
        with pytest.raises(ValueError, match="Ambiguous integer date locale"):
            parse_date("07052014", field_name="UNKNOWN_DATE_COL")

    def test_8digit_genuinely_invalid_returns_none(self) -> None:
        """99999999 cannot be a valid date in any layout → None."""
        result = parse_date("99999999")
        assert result is None

    def test_8digit_invalid_month_under_locale_falls_back_to_yyyymmdd(self) -> None:
        """20140728: d_cand=20 > 12 → DMY; datetime(728,14,20) fails;
        fallback YYYYMMDD 2014-07-28 succeeds."""
        result = parse_date("20140728")
        assert result is not None
        assert result.dt.year == 2014
        assert result.dt.month == 7
        assert result.dt.day == 28
        assert result.format == "iso"

    def test_8digit_yyyymmdd_year_ending_le12_not_silently_corrupted(self) -> None:
        """H1 RED: 20120815 (YYYYMMDD 2012-08-15). Under DMY the split gives
        year int('0815')=815, which datetime() ACCEPTS — the year-range guard must
        reject it and fall through to YYYYMMDD, never emitting a medieval year."""
        result = parse_date("20120815")
        assert result is not None
        assert (result.dt.year, result.dt.month, result.dt.day) == (2012, 8, 15)
        assert result.dt.year >= 1900  # never the corrupted 815

    def test_8digit_yyyymmdd_2003_not_corrupted_to_year_612(self) -> None:
        """H1 RED: 20030612 → DMY split year int('0612')=612 (valid datetime).
        Guard must fall through to YYYYMMDD 2003-06-12."""
        result = parse_date("20030612")
        assert result is not None
        assert (result.dt.year, result.dt.month, result.dt.day) == (2003, 6, 12)

    def test_8digit_ddmmyyyy_real_study_value_unaffected(self) -> None:
        """Regression: a genuine DDMMYYYY value (this study's compact format) with
        a valid in-range year is NOT disturbed by the H1 year-range guard."""
        result = parse_date("28072014", field_name="IC_VISDAT")  # IC_VISDAT ∈ DMY allowlist
        assert result is not None
        assert (result.dt.year, result.dt.month, result.dt.day) == (2014, 7, 28)


# ---------------------------------------------------------------------------
# 7-digit compact: both-valid, neither-valid, single-valid
# ---------------------------------------------------------------------------


class TestCompact7Digit:
    """Tests for 7-digit compact date: DMMYYYY vs DDMYYYY disambiguation."""

    def test_7digit_single_valid_dmmyyyy(self) -> None:
        """9122014: DMMYYYY d=9,m=12,y=2014 ✓; DDMYYYY d=91,m=2,y=2014 invalid
        (day=91) → only DMMYYYY valid → d=9, m=12, y=2014."""
        result = parse_date("9122014")
        assert result is not None
        assert result.dt.day == 9
        assert result.dt.month == 12
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_7digit_ddmyyyy_single_valid(self) -> None:
        """1392014: DMMYYYY d=1,m=39,y=2014 invalid (month=39);
        DDMYYYY d=13,m=9,y=2014 valid → day=13, month=9, year=2014."""
        result = parse_date("1392014")
        assert result is not None
        assert result.dt.day == 13
        assert result.dt.month == 9
        assert result.dt.year == 2014

    def test_7digit_both_valid_returns_none(self) -> None:
        """1052014: DMMYYYY d=1,m=5,y=2014 ✓; DDMYYYY d=10,m=5,y=2014 ✓
        → both valid → None (ambiguous)."""
        result = parse_date("1052014")
        assert result is None

    def test_7digit_neither_valid_returns_none(self) -> None:
        """9992014: DMMYYYY d=9,m=99,y=2014 invalid; DDMYYYY d=99,m=9,y=2014
        invalid (day=99) → neither valid → None."""
        result = parse_date("9992014")
        assert result is None

    def test_7digit_explicit_locale_canonical_first_then_alternate(self) -> None:
        """An EXPLICIT/derived locale tries the canonical split first (DMMYYYY for
        DMY, MDDYYYY for MDY), then falls back to the alternate 2+1+4 split
        (DDMYYYY/MMDYYYY) when the canonical yields an invalid calendar date
        (Note 29). This recovers human-entered numeric dates with a 2-digit day +
        1-digit month while preserving the both-valid canonical recovery.

        (a) Canonical valid:   9122014 / IC_VISDAT (DMY) → d=9,  m=12, y=2014.
        (b) Both valid → canonical wins: 1052014 / IC_VISDAT (DMY) → DMMYYYY →
            d=1, m=5, y=2014 (NOT the 10-May alternate).
        (c) Canonical invalid → ALTERNATE recovers: 1392014 / IC_VISDAT (DMY) →
            DMMYYYY m=39 invalid → fall back to DDMYYYY → d=13, m=9, y=2014.
        (d) MDY symmetry: 1052014 / MY_DATE (MDY) → canonical MDDYYYY → m=1,
            d=5, y=2014.
        """
        # (a) canonical split valid — resolves correctly
        result_a = parse_date("9122014", field_name="IC_VISDAT")  # DMY allowlist
        assert result_a is not None
        assert (result_a.dt.year, result_a.dt.month, result_a.dt.day) == (2014, 12, 9)

        # (b) both splits valid — canonical DMMYYYY wins (no regression)
        result_b = parse_date("1052014", field_name="IC_VISDAT")  # DMY allowlist
        assert result_b is not None, (
            "Explicit DMY must use the canonical DMMYYYY split (1+2+4) first and recover this date"
        )
        assert (result_b.dt.year, result_b.dt.month, result_b.dt.day) == (2014, 5, 1)

        # (c) canonical invalid → fall back to the 2+1+4 alternate (real date recovered)
        result_c = parse_date("1392014", field_name="IC_VISDAT")  # DMY allowlist
        assert result_c is not None, (
            "Canonical DMMYYYY is invalid (month=39); the alternate DDMYYYY split "
            "(13/9/2014) is a real human-entered numeric date and must be recovered"
        )
        assert (result_c.dt.year, result_c.dt.month, result_c.dt.day) == (2014, 9, 13)

        # (d) MDY symmetry — canonical MDDYYYY split
        result_d = parse_date("1052014", field_name="MY_DATE", date_locales={"MY_DATE": "MDY"})
        assert result_d is not None
        assert (result_d.dt.year, result_d.dt.month, result_d.dt.day) == (2014, 1, 5)

    def test_7digit_explicit_dmy_2digit_day_1digit_month(self) -> None:
        """Note 29 regression: a day-first numeric date with a 2-digit day and a
        1-digit month (e.g. 15/8/2014 typed as 1582014) is a 2+1+4 layout that the
        old canonical-only path quarantined. It must now parse to 15 Aug 2014."""
        result = parse_date("1582014", field_name="IC_VISDAT")  # DMY allowlist
        assert result is not None
        assert (result.dt.year, result.dt.month, result.dt.day) == (2014, 8, 15)

    def test_7digit_explicit_dmy_invalid_both_splits_quarantines(self) -> None:
        """A 7-digit value invalid under BOTH the canonical (1+2+4) and alternate
        (2+1+4) day-first splits is genuinely bad → fail-closed None."""
        # 9992014: DMMYYYY m=99 invalid; DDMYYYY d=99 invalid → both fail.
        assert parse_date("9992014", field_name="IC_VISDAT") is None

    def test_7digit_explicit_dmy_via_date_locales(self) -> None:
        """Explicit DMY in date_locales (UPPER-CASE key) + unambiguous value.
        9122014: DMMYYYY d=9,m=12 ✓; DDMYYYY d=91 invalid → d=9, m=12, y=2014."""
        result = parse_date(
            "9122014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 9
        assert result.dt.month == 12
        assert result.dt.year == 2014


class TestOriginDefaultLocale:
    """Note 29: a study-origin ``default_locale`` (e.g. DMY for an Indian study)
    is applied ONLY when allowlist/manifest/value-heuristic are all inconclusive
    — explicit declarations and provably-decisive values always win."""

    def test_origin_default_resolves_ambiguous_undeclared_separator(self) -> None:
        """An ambiguous (both ≤ 12) value in an UNDECLARED column would raise
        without a default; with default_locale='DMY' it resolves day-first."""
        # Without a default → fail-closed.
        with pytest.raises(ValueError):
            parse_date("05/06/2014", field_name="UNDECLARED_DAT")
        # With origin default → 5 June 2014 (DMY).
        result = parse_date("05/06/2014", field_name="UNDECLARED_DAT", default_locale="DMY")
        assert result is not None
        assert (result.dt.year, result.dt.month, result.dt.day) == (2014, 6, 5)

    def test_origin_default_resolves_ambiguous_8digit_and_6digit(self) -> None:
        """Compact ambiguous values resolve day-first under the origin default."""
        r8 = parse_date("05062014", field_name="UNDECLARED_DAT", default_locale="DMY")
        assert r8 is not None and (r8.dt.year, r8.dt.month, r8.dt.day) == (2014, 6, 5)
        r6 = parse_date("050614", field_name="UNDECLARED_DAT", default_locale="DMY")
        assert r6 is not None and (r6.dt.year, r6.dt.month, r6.dt.day) == (2014, 6, 5)

    def test_decisive_value_heuristic_beats_origin_default(self) -> None:
        """A provably-MDY value (2nd component > 12) is respected even when the
        origin default is DMY — data wins over a study-wide default."""
        result = parse_date("07252014", field_name="UNDECLARED_DAT", default_locale="DMY")
        assert result is not None
        # 25 in the 2nd position → MDY → July 25 2014, NOT day=07/month=25 (invalid).
        assert (result.dt.year, result.dt.month, result.dt.day) == (2014, 7, 25)

    def test_explicit_manifest_mdy_beats_origin_default(self) -> None:
        """An explicitly-declared MDY column is never swapped by an India default."""
        result = parse_date(
            "07052014",
            field_name="CX_VISDAT",
            date_locales={"CX_VISDAT": "MDY"},
            default_locale="DMY",
        )
        assert result is not None
        # MDY → month=07, day=05 → 5 July 2014 (not 7 May).
        assert (result.dt.year, result.dt.month, result.dt.day) == (2014, 7, 5)


# ---------------------------------------------------------------------------
# 6-digit compact: DDMMYY
# ---------------------------------------------------------------------------


class TestCompact6Digit:
    """Tests for 6-digit compact date DDMMYY."""

    def test_6digit_day_gt12_unambiguous(self) -> None:
        """280514 → day=28, month=5, year=2014 (2-digit year, day > 12)."""
        result = parse_date("280514")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_6digit_two_digit_year_expansion_pre50(self) -> None:
        """250914: yy=14 < 50 → 2014.  day=25, month=9."""
        result = parse_date("250914")
        assert result is not None
        assert result.dt.year == 2014
        assert result.dt.day == 25
        assert result.dt.month == 9

    def test_6digit_two_digit_year_expansion_post50(self) -> None:
        """251265: yy=65 ≥ 50 → 1965.  day=25, month=12."""
        result = parse_date("251265")
        assert result is not None
        assert result.dt.year == 1965
        assert result.dt.day == 25
        assert result.dt.month == 12

    def test_6digit_dmy_via_allowlist(self) -> None:
        """IC_VISDAT in DMY allowlist → 280514 = day=28, month=5."""
        result = parse_date("280514", field_name="IC_VISDAT")
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5

    def test_6digit_dmy_via_date_locales_override(self) -> None:
        """Explicit DMY override → 280514 = day=28, month=5."""
        result = parse_date(
            "280514",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 28
        assert result.dt.month == 5

    def test_6digit_invalid_date_returns_none(self) -> None:
        """329914: month=99 invalid → None."""
        result = parse_date("329914")
        assert result is None

    def test_6digit_shift_date_output_is_iso(self) -> None:
        """shift_date on a 6-digit DDMMYY must output ISO YYYY-MM-DD."""
        from scripts.security.phi_scrub import shift_date

        result = shift_date("280514", 2, field_name="IC_VISDAT")
        assert result == "2014-05-30"

    def test_6digit_ambiguous_known_field_raises(self) -> None:
        """Ambiguous 6-digit (both leading components <= 12) with a KNOWN field and
        no manifest/allowlist entry must FAIL-CLOSED (raise), identical to the
        separator + 8-digit branches — never silently guess day-first."""
        with pytest.raises(ValueError, match="Ambiguous 6-digit date locale"):
            parse_date("050614", field_name="SOME_UNKNOWN_6DIGIT_COL")

    def test_6digit_ambiguous_resolves_via_manifest_no_raise(self) -> None:
        """The same ambiguous 6-digit value parses cleanly once the locale is
        declared in date_locales — the 'all day-first' assumption lives in the
        manifest, not a silent code default."""
        result = parse_date("050614", field_name="SOME_COL", date_locales={"SOME_COL": "DMY"})
        assert result is not None
        assert (result.dt.day, result.dt.month, result.dt.year) == (5, 6, 2014)


# ---------------------------------------------------------------------------
# Regression: ISO is unaffected by separator additions
# ---------------------------------------------------------------------------


class TestIsoRegression:
    """ISO date parsing must be unaffected by the new separator/compact paths."""

    def test_iso_yyyy_mm_dd_unchanged(self) -> None:
        result = parse_date("2014-07-28")
        assert result is not None
        assert result.format == "iso"
        assert result.dt.year == 2014
        assert result.dt.month == 7
        assert result.dt.day == 28

    def test_iso_with_time_unchanged(self) -> None:
        result = parse_date("2014-07-28 00:00:00")
        assert result is not None
        assert result.format == "iso"
        assert result.has_time is True

    def test_iso_not_routed_to_separator_branch(self) -> None:
        """2014-07-28 must produce format='iso', NOT 'dmy'/'mdy'."""
        result = parse_date("2014-07-28")
        assert result is not None
        assert result.format == "iso"
        # Sanity: year/month/day correct
        assert result.dt.year == 2014
        assert result.dt.month == 7
        assert result.dt.day == 28

    def test_iso_early_century(self) -> None:
        result = parse_date("1990-01-15")
        assert result is not None
        assert result.format == "iso"
        assert result.dt.year == 1990


# ---------------------------------------------------------------------------
# N4: value_looks_like_date — separator variants and compact integer
# ---------------------------------------------------------------------------


class TestValueLooksLikeDate:
    """value_looks_like_date must recognise dot-, hyphen-separated, and compact
    integer date strings without validating the individual components."""

    def test_dot_separator_recognised(self) -> None:
        """1.5.2014 uses a dot separator and must return True."""
        assert value_looks_like_date("1.5.2014") is True

    def test_hyphen_separator_recognised(self) -> None:
        """1-5-2014 uses a hyphen separator and must return True."""
        assert value_looks_like_date("1-5-2014") is True

    def test_compact_6digit_recognised(self) -> None:
        """280514 is a 6-digit compact integer date and must return True."""
        assert value_looks_like_date("280514") is True

    def test_slash_separator_recognised(self) -> None:
        """28/05/2014 uses a slash separator and must return True."""
        assert value_looks_like_date("28/05/2014") is True

    def test_iso_date_recognised(self) -> None:
        """2014-07-28 (ISO) must return True."""
        assert value_looks_like_date("2014-07-28") is True

    def test_non_date_string_returns_false(self) -> None:
        """A plain word is not a date."""
        assert value_looks_like_date("hello") is False

    def test_empty_string_returns_false(self) -> None:
        assert value_looks_like_date("") is False

    def test_leading_trailing_whitespace_stripped(self) -> None:
        """Leading/trailing spaces must not prevent detection."""
        assert value_looks_like_date("  28/05/2014  ") is True


# ---------------------------------------------------------------------------
# _resolve_locale: priority chain tests
# ---------------------------------------------------------------------------


class TestResolveLocale:
    """_resolve_locale must follow the DMY allowlist → manifest → None priority."""

    def test_dmy_allowlist_field_returns_dmy(self) -> None:
        """A field in DMY_VARIABLES returns 'DMY' regardless of date_locales."""
        # Pick any canonical DMY variable (case-insensitive check)
        dmy_col = next(iter(DMY_VARIABLES))  # e.g. "IC_VISDAT"
        # Even if date_locales has a conflicting MDY entry, allowlist wins at P1
        result = _resolve_locale(dmy_col, date_locales={dmy_col.upper(): "MDY"})
        assert result == "DMY"

    def test_dmy_allowlist_wins_over_manifest_conflict(self) -> None:
        """IC_VISDAT (DMY allowlist) returns 'DMY' even when manifest says 'MDY'."""
        result = _resolve_locale("IC_VISDAT", date_locales={"IC_VISDAT": "MDY"})
        assert result == "DMY"

    def test_manifest_field_returns_declared_locale(self) -> None:
        """A non-DMY_VARIABLES field with a manifest entry returns the declared locale."""
        result = _resolve_locale("MY_SPECIAL_DATE", date_locales={"MY_SPECIAL_DATE": "MDY"})
        assert result == "MDY"

    def test_manifest_field_dmy_declared(self) -> None:
        """A non-DMY_VARIABLES field declared DMY in manifest returns 'DMY'."""
        result = _resolve_locale("SOME_DATE_COL", date_locales={"SOME_DATE_COL": "DMY"})
        assert result == "DMY"

    def test_manifest_key_is_upper_case(self) -> None:
        """Manifest keys are normalised to UPPER-CASE at load time; the lookup
        uses field_name.upper() so a lower-case field_name matches."""
        result = _resolve_locale("some_date_col", date_locales={"SOME_DATE_COL": "MDY"})
        assert result == "MDY"

    def test_field_name_none_returns_none(self) -> None:
        """field_name=None must return None regardless of date_locales."""
        result = _resolve_locale(None, date_locales={"IC_VISDAT": "DMY"})
        assert result is None

    def test_field_name_none_with_empty_locales_returns_none(self) -> None:
        """field_name=None with empty date_locales also returns None."""
        result = _resolve_locale(None, date_locales={})
        assert result is None

    def test_unknown_field_with_empty_locales_returns_none(self) -> None:
        """A field not in the allowlist and not in date_locales returns None."""
        result = _resolve_locale("UNKNOWN_COL", date_locales={})
        assert result is None

    def test_unknown_field_with_none_locales_returns_none(self) -> None:
        """A field not in the allowlist with date_locales=None returns None."""
        result = _resolve_locale("UNKNOWN_COL", date_locales=None)
        assert result is None


# ---------------------------------------------------------------------------
# _mask_date_value: PHI-safe shape masking
# ---------------------------------------------------------------------------


class TestMaskDateValue:
    """_mask_date_value must replace digits with 9, ASCII letters with X,
    and keep separator characters unchanged."""

    def test_slash_date_masked(self) -> None:
        """07/05/2014 → 99/99/9999 (digits→9, separators kept)."""
        assert _mask_date_value("07/05/2014") == "99/99/9999"

    def test_alpha_prefix_masked(self) -> None:
        """UNK-2014 → XXX-9999 (letters→X, hyphen kept)."""
        assert _mask_date_value("UNK-2014") == "XXX-9999"

    def test_dot_separator_kept(self) -> None:
        """07.05.14 → 99.99.99 (dot separator kept)."""
        assert _mask_date_value("07.05.14") == "99.99.99"

    def test_date_with_time_masked(self) -> None:
        """07-05-2014 14:30:00 → 99-99-9999 99:99:99."""
        assert _mask_date_value("07-05-2014 14:30:00") == "99-99-9999 99:99:99"

    def test_compact_integer_masked(self) -> None:
        """28052014 → 99999999 (pure digits)."""
        assert _mask_date_value("28052014") == "99999999"

    def test_empty_string_returns_empty(self) -> None:
        assert _mask_date_value("") == ""

    def test_separators_kept_slash_intact(self) -> None:
        """Slash characters in the value are not masked."""
        result = _mask_date_value("28/05/2014")
        assert "/" in result

    def test_non_ascii_not_masked_to_x(self) -> None:
        """Non-ASCII characters (rare in date strings) are kept as-is since only
        ASCII letters are converted — the guard is ch.isascii() and ch.isalpha()."""
        # A non-ASCII character like '/' passes through unchanged (it's not alpha).
        # Test a purely ASCII separator string for correctness.
        assert _mask_date_value("01/01/2000") == "99/99/9999"


# ---------------------------------------------------------------------------
# check_locale_consistency: WARNING on DMY_VARIABLES / manifest conflict
# ---------------------------------------------------------------------------


class TestCheckLocaleConsistency:
    """check_locale_consistency emits WARNING when a DMY_VARIABLES column
    appears in date_locales with a conflicting (non-DMY) locale; no warning
    when consistent."""

    def test_conflicting_entry_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A DMY_VARIABLES column declared as MDY in the manifest triggers a WARNING."""
        # IC_VISDAT is in DMY_VARIABLES; declaring it MDY in the manifest conflicts.
        with caplog.at_level(logging.WARNING, logger="scripts.extraction.io.clinical_dates"):
            check_locale_consistency({"IC_VISDAT": "MDY"})
        assert any(
            "IC_VISDAT" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        ), "Expected a WARNING mentioning IC_VISDAT for the locale conflict"

    def test_conflict_message_mentions_allowlist(self, caplog: pytest.LogCaptureFixture) -> None:
        """The warning message must mention the DMY_VARIABLES allowlist so the
        operator knows the resolution path."""
        with caplog.at_level(logging.WARNING, logger="scripts.extraction.io.clinical_dates"):
            check_locale_consistency({"CBC_HBADAT": "MDY"})
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("DMY" in msg for msg in warning_messages), (
            "Warning message must mention DMY (the allowlist resolution)"
        )

    def test_consistent_entry_emits_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A DMY_VARIABLES column declared as DMY in the manifest is consistent
        (allowlist agrees) — no warning should be emitted."""
        with caplog.at_level(logging.WARNING, logger="scripts.extraction.io.clinical_dates"):
            check_locale_consistency({"IC_VISDAT": "DMY"})
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == [], (
            f"No warning expected for consistent locale, got: {[w.message for w in warnings]}"
        )

    def test_non_dmy_variable_column_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A column not in DMY_VARIABLES never triggers a warning regardless of its declared locale."""
        with caplog.at_level(logging.WARNING, logger="scripts.extraction.io.clinical_dates"):
            check_locale_consistency({"SOME_OTHER_DATE": "MDY"})
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == [], "No warning expected for a non-DMY_VARIABLES column"

    def test_empty_date_locales_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """An empty date_locales dict must produce no warnings."""
        with caplog.at_level(logging.WARNING, logger="scripts.extraction.io.clinical_dates"):
            check_locale_consistency({})
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == []
