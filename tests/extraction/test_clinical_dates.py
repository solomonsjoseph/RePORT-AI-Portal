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
        with pytest.raises(ValueError, match="_forms_manifest.yaml"):
            parse_date("07/05/2014", field_name="MY_DATE_COL")

    def test_ambiguous_raises_interpolates_value(self) -> None:
        # Regression: third f-string fragment was a plain string literal,
        # so {value!r} appeared verbatim instead of being interpolated.
        # Assert the repr of the actual value appears in the message.
        with pytest.raises(ValueError) as exc_info:
            parse_date("07/05/2014", field_name="SOME_UNKNOWN_COL")
        assert "'07/05/2014'" in str(exc_info.value), (
            "Error message must interpolate the offending value; "
            f"got: {exc_info.value}"
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
