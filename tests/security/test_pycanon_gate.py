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


def test_null_qi_values_do_not_raise() -> None:
    """Mixed null and string QI values must not crash pyCANON sort (Indo-VAP N5)."""
    recs = [
        {"IS_AGE": "25-34", "IS_SEX": "M", "outcome": "a"},
        {"IS_AGE": None, "IS_SEX": "M", "outcome": "b"},
        {"IS_AGE": None, "IS_SEX": "M", "outcome": "c"},
        {"IS_AGE": "35-44", "IS_SEX": "F", "outcome": "d"},
        {"IS_AGE": "35-44", "IS_SEX": "F", "outcome": "e"},
    ]
    r = check_publish_anonymity(
        recs,
        quasi_identifiers=["IS_AGE", "IS_SEX"],
        k_threshold=5,
    )
    assert r.k == 1
    assert not r.ok


def test_empty_string_qi_treated_as_null_equivalence_class() -> None:
    recs = [
        {"AGE": "", "SEX": "M"},
        {"AGE": None, "SEX": "M"},
        {"AGE": "25-34", "SEX": "F"},
        {"AGE": "25-34", "SEX": "F"},
        {"AGE": "25-34", "SEX": "F"},
    ]
    r = check_publish_anonymity(recs, quasi_identifiers=["AGE", "SEX"], k_threshold=5)
    assert r.k == 2
    assert not r.ok


def test_numeric_qi_with_nan_coerced_to_string() -> None:
    """Float QI columns with NaN must not crash pyCANON (Indo-VAP IS_AGE/HHC_AGE)."""
    recs = [
        {"IS_AGE": 25.0, "IS_SEX": "M"},
        {"IS_AGE": float("nan"), "IS_SEX": "M"},
        {"IS_AGE": float("nan"), "IS_SEX": "M"},
        {"IS_AGE": 35.0, "IS_SEX": "F"},
        {"IS_AGE": 35.0, "IS_SEX": "F"},
    ]
    r = check_publish_anonymity(
        recs,
        quasi_identifiers=["IS_AGE", "IS_SEX"],
        k_threshold=5,
    )
    assert r.k == 1
    assert not r.ok
