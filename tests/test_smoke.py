"""Smoke tests for core RePORT AI Portal modules.

These tests verify that the main modules import correctly and that their
core functions behave as expected without requiring external files or services.
"""

from __future__ import annotations

import re
from datetime import datetime

# ---------------------------------------------------------------------------
# __version__ smoke tests
# ---------------------------------------------------------------------------


def test_version_is_valid_semver() -> None:
    """__version__ must be a valid MAJOR.MINOR.PATCH string."""
    from __version__ import __version__, __version_info__

    semver_re = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    assert semver_re.fullmatch(__version__), f"Invalid semver: {__version__!r}"
    assert isinstance(__version_info__, tuple)
    assert len(__version_info__) == 3
    assert all(isinstance(n, int) for n in __version_info__)


# ---------------------------------------------------------------------------
# clinical_dates smoke tests
# ---------------------------------------------------------------------------


def test_parse_date_iso() -> None:
    """ISO-format dates are parsed correctly."""
    from scripts.extraction.io.clinical_dates import parse_date

    result = parse_date("2014-07-28")
    assert result is not None
    assert result.dt == datetime(2014, 7, 28)
    assert result.format == "iso"
    assert not result.has_time


def test_parse_date_mdy_slash() -> None:
    """M/D/YYYY slash dates are parsed as month-first by default."""
    from scripts.extraction.io.clinical_dates import parse_date

    result = parse_date("7/28/2014")
    assert result is not None
    assert result.dt == datetime(2014, 7, 28)
    assert result.format == "mdy"


def test_parse_date_dmy_slash() -> None:
    """D/M/YYYY slash dates are parsed day-first for known DMY variables."""
    from scripts.extraction.io.clinical_dates import parse_date

    result = parse_date("28/05/2014", field_name="IC_VISDAT")
    assert result is not None
    assert result.dt == datetime(2014, 5, 28)
    assert result.format == "dmy"


def test_parse_date_invalid_returns_none() -> None:
    """Non-date strings return None."""
    from scripts.extraction.io.clinical_dates import parse_date

    assert parse_date("not-a-date") is None
    assert parse_date("") is None
    assert parse_date("   ") is None


def test_value_looks_like_date() -> None:
    """value_looks_like_date correctly identifies date-like strings."""
    from scripts.extraction.io.clinical_dates import value_looks_like_date

    assert value_looks_like_date("2014-07-28")
    assert value_looks_like_date("7/28/2014")
    assert not value_looks_like_date("hello world")
    assert not value_looks_like_date("123")


def test_dmy_variables_set() -> None:
    """DMY_VARIABLES is a non-empty frozenset of known variable names."""
    from scripts.extraction.io.clinical_dates import DMY_VARIABLES

    assert isinstance(DMY_VARIABLES, frozenset)
    assert "IC_VISDAT" in DMY_VARIABLES
    assert "IT_IGRADAT" in DMY_VARIABLES
