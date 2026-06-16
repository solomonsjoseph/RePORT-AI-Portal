"""Tests for the pyCANON publish-gate anonymity engine (Wave 3 C3)."""

from __future__ import annotations

import pytest

from scripts.security.pycanon_gate import check_publish_anonymity

# Smallest QI equivalence class has 2 members → k = 2.
_RECORDS = [
    {"age_band": "40-50", "sex": "M", "outcome": "cured"},
    {"age_band": "40-50", "sex": "M", "outcome": "cured"},
    {"age_band": "40-50", "sex": "M", "outcome": "died"},
    {"age_band": "40-50", "sex": "M", "outcome": "cured"},
    {"age_band": "50-60", "sex": "F", "outcome": "cured"},
    {"age_band": "50-60", "sex": "F", "outcome": "died"},
]
_QI = ["age_band", "sex"]


def test_computes_k_and_blocks_below_threshold() -> None:
    r = check_publish_anonymity(_RECORDS, quasi_identifiers=_QI, k_threshold=5)
    assert r.k == 2
    assert not r.ok
    assert r.n_records == 6
    assert r.quasi_identifiers == ("age_band", "sex")


def test_passes_when_k_meets_threshold() -> None:
    r = check_publish_anonymity(_RECORDS, quasi_identifiers=_QI, k_threshold=2)
    assert r.k == 2
    assert r.ok


def test_l_diversity_measured_when_requested() -> None:
    r = check_publish_anonymity(
        _RECORDS,
        quasi_identifiers=_QI,
        k_threshold=2,
        sensitive_attributes=["outcome"],
        l_threshold=2,
    )
    assert r.l_value == 2
    assert r.l_threshold == 2
    assert r.ok


def test_l_diversity_blocks_homogeneous_class() -> None:
    # Every class is homogeneous on outcome → l = 1 < 2.
    recs = [
        {"age_band": "40-50", "sex": "M", "outcome": "cured"},
        {"age_band": "40-50", "sex": "M", "outcome": "cured"},
    ]
    r = check_publish_anonymity(
        recs,
        quasi_identifiers=_QI,
        k_threshold=2,
        sensitive_attributes=["outcome"],
        l_threshold=2,
    )
    assert r.l_value == 1
    assert not r.ok


def test_empty_records_vacuously_ok() -> None:
    r = check_publish_anonymity([], quasi_identifiers=_QI, k_threshold=5)
    assert r.ok
    assert r.k == 0
    assert r.n_records == 0


def test_value_free_result_has_no_row_values() -> None:
    r = check_publish_anonymity(_RECORDS, quasi_identifiers=_QI, k_threshold=5)
    # Only counts + names + thresholds, never a row value or class key.
    assert not hasattr(r, "violating_keys")
    assert not hasattr(r, "rows")
    assert set(r.quasi_identifiers) == set(_QI)


def test_rejects_empty_qi() -> None:
    with pytest.raises(ValueError, match="quasi_identifiers"):
        check_publish_anonymity(_RECORDS, quasi_identifiers=[], k_threshold=5)


def test_rejects_bad_threshold() -> None:
    with pytest.raises(ValueError, match="k_threshold"):
        check_publish_anonymity(_RECORDS, quasi_identifiers=_QI, k_threshold=0)


def test_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="absent"):
        check_publish_anonymity(_RECORDS, quasi_identifiers=["nonexistent"], k_threshold=2)
