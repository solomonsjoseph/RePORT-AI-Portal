"""Regression guard for the all-9 date-sentinel PHI leak.

`_is_date_sentinel` used to classify any value whose digits are all-9 as an
"unknown date" placeholder and SKIP jitter — but ``"9/9/99"`` is the real date
1999-09-09 (digits "9999"). Skipping it published a real, un-shifted date: a
PHI leak (a date more specific than year). The fix: an all-9/all-0 value is a
sentinel ONLY when it does not parse as a real date.
"""
from __future__ import annotations

import pytest

from scripts.security.phi_scrub import _is_date_sentinel


class TestAll9SentinelDoesNotSwallowRealDates:
    @pytest.mark.parametrize(
        "value",
        [
            "9/9/99",   # 1999-09-09, digits "9999" — the leak case
            "9-9-99",   # same date, hyphen separator
            "9.9.99",   # same date, dot separator
            "09/09/99",  # has 0s, plainly a real date
            "2014-07-28",
            "28072014",  # compact DDMMYYYY
        ],
    )
    def test_real_date_is_not_a_sentinel(self, value: str) -> None:
        """A value that parses as a real date must NOT be treated as a sentinel
        (otherwise it is published un-jittered)."""
        assert _is_date_sentinel(value) is False

    @pytest.mark.parametrize(
        "value",
        [
            "99999999",   # 8-digit unknown-date sentinel
            "9999-99-99",  # ISO-shaped sentinel
            "999999",     # 6-digit sentinel
            "9",          # bare-9 sentinel
            "0",          # zero placeholder
            "00000000",   # zero-date sentinel
            99999999,     # integer form (bypasses string token list)
            0,
        ],
    )
    def test_genuine_unparseable_placeholder_is_a_sentinel(self, value: object) -> None:
        """All-9/all-0 values that do NOT parse as a date stay classified as
        sentinels (skipped from jitter, never quarantined)."""
        assert _is_date_sentinel(value) is True

    def test_non_all9_value_is_not_a_sentinel(self) -> None:
        assert _is_date_sentinel("12/25/2015") is False
        assert _is_date_sentinel("") is False
