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

import pytest

from scripts.extraction.io.clinical_dates import (
    _disambiguate_locale,
    is_dmy_variable,
    parse_date,
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
        # Regression: third f-string fragment was a plain string literal,
        # so {value!r} appeared verbatim instead of being interpolated.
        # Assert the repr of the actual value appears in the message.
        with pytest.raises(ValueError) as exc_info:
            parse_date("07/05/2014", field_name="SOME_UNKNOWN_COL")
        assert "'07/05/2014'" in str(exc_info.value), (
            f"Error message must interpolate the offending value; got: {exc_info.value}"
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
        result = parse_date(
            "07/05/2014",
            field_name="IC_VISDAT_v2",
            date_locales={"IC_VISDAT_v2": "DMY"},
        )
        assert result is not None
        assert result.format == "dmy"
        # D/M/Y: day=7, month=5
        assert result.dt.day == 7
        assert result.dt.month == 5
        assert result.dt.year == 2014

    def test_mdy_override_for_ambiguous_value(self) -> None:
        result = parse_date(
            "07/05/2014",
            field_name="IC_VISDAT_v2",
            date_locales={"IC_VISDAT_v2": "MDY"},
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
        """1052014 under declared DMY → d=1, m=5, y=2014 (DMMYYYY)."""
        result = parse_date(
            "1052014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 1
        assert result.dt.month == 5
        assert result.dt.year == 2014
        assert result.format == "iso"

    def test_7digit_mdy_via_date_locales(self) -> None:
        """1282014 under declared MDY → m=1, d=28, y=2014 (MDDYYYY)."""
        result = parse_date(
            "1282014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "MDY"},
        )
        assert result is not None
        assert result.dt.month == 1
        assert result.dt.day == 28
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


# ---------------------------------------------------------------------------
# 7-digit compact: both-valid, neither-valid, single-valid
# ---------------------------------------------------------------------------


class TestCompact7Digit:
    """Tests for 7-digit compact date: DMMYYYY vs DDMYYYY disambiguation."""

    def test_7digit_single_valid_dmmyyyy(self) -> None:
        """1052014: DMMYYYY → d=1,m=5,y=2014; DDMYYYY=10,5,2014 also valid
        — wait, let's use a day that makes only DMMYYYY valid.
        9122014: DMMYYYY d=9,m=12,y=2014 ✓; DDMYYYY d=91,m=2,y=2014 invalid
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

    def test_7digit_explicit_dmy_locale_uses_dmmyyyy(self) -> None:
        """With explicit DMY locale, 7-digit uses DMMYYYY unconditionally."""
        result = parse_date(
            "1052014",
            field_name="IC_VISDAT",  # DMY allowlist
        )
        assert result is not None
        assert result.dt.day == 1
        assert result.dt.month == 5
        assert result.dt.year == 2014

    def test_7digit_explicit_dmy_via_date_locales(self) -> None:
        """Explicit DMY in date_locales → d=1, m=5, y=2014 from 1052014."""
        result = parse_date(
            "1052014",
            field_name="MY_DATE",
            date_locales={"MY_DATE": "DMY"},
        )
        assert result is not None
        assert result.dt.day == 1
        assert result.dt.month == 5
        assert result.dt.year == 2014


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
        result = parse_date(
            "050614", field_name="SOME_COL", date_locales={"SOME_COL": "DMY"}
        )
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
