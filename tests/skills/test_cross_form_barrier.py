"""Tests for the Note 8 cross-form PHI-classification consistency barrier.

The barrier holds ONLY the forms that under-protect a column relative to the
strictest action any peer form applies to the same column name. Equally
protective (but differently named) actions are not conflicts. Column NAMES and
counts only — never row values.
"""

from __future__ import annotations

import pytest

from scripts.security.phi_review import (
    _ACTION_METHOD,
    Action,
    HeaderClassification,
    HeldReason,
)

extract = pytest.importorskip(
    "scripts.skills.extract_to_llm_source",
    reason="dataset-to-llm-source skill module",
)


def _approval(form_name: str, actions: dict[str, str], status: str = "approved"):
    """Build a minimal FormReviewApproval-like object for the barrier."""
    from scripts.security.phi_review import FormReviewApproval

    return FormReviewApproval(
        form_name=form_name,
        status=status,
        attempts=1,
        actions=actions,
        classifications=(),
        reasons=(),
        rule_bundle_sha256="0" * 64,
        source_mode="pinned",
        held_reason=None,
        force_drop_headers=(),
    )


def test_header_classification_carries_method():
    """Note 8: classification names the method (SANT for jitter), None for keep."""
    assert _ACTION_METHOD[Action.JITTER_DATE] == "SANT_date_jitter"
    assert _ACTION_METHOD[Action.PSEUDONYMIZE] == "HMAC_SHA256"
    assert _ACTION_METHOD[Action.KEEP] is None
    hc = HeaderClassification(
        header="VISIT_DATE",
        action=Action.JITTER_DATE,
        matched_rules=("hipaa_dates",),
        jurisdictions=("USA",),
        reasons=("date",),
        method=_ACTION_METHOD[Action.JITTER_DATE],
    )
    assert hc.to_json()["method"] == "SANT_date_jitter"
    keep = HeaderClassification("AGE", Action.KEEP, (), ("USA",), ())
    assert keep.to_json()["method"] is None


def test_cross_form_holds_only_minority():
    """SUBJID pseudonymize in 27 forms, keep in 1 -> only the 1 is held."""
    approvals = [
        _approval(f"form_{i}.xlsx", {"SUBJID": "pseudonymize", "AGE": "keep"}) for i in range(27)
    ]
    approvals.append(_approval("form_bad.xlsx", {"SUBJID": "keep", "AGE": "keep"}))

    rebuilt, info = extract._apply_cross_form_consistency(approvals)

    held = [a.form_name for a in rebuilt if a.status == "held"]
    approved = [a.form_name for a in rebuilt if a.status == "approved"]
    assert held == ["form_bad.xlsx"]
    assert len(approved) == 27
    assert info["conflicts"] == {"form_bad.xlsx": ["SUBJID"]}
    bad = next(a for a in rebuilt if a.form_name == "form_bad.xlsx")
    assert isinstance(bad.held_reason, HeldReason)


def test_cross_form_canonical_is_strictest():
    """keep in A, drop in B -> the weaker (keep) form A is held; B proceeds."""
    a = _approval("a.xlsx", {"NOTE": "keep"})
    b = _approval("b.xlsx", {"NOTE": "drop"})
    rebuilt, info = extract._apply_cross_form_consistency([a, b])
    held = {x.form_name for x in rebuilt if x.status == "held"}
    assert held == {"a.xlsx"}
    assert info["conflicts"] == {"a.xlsx": ["NOTE"]}


def test_cross_form_equal_protection_is_not_a_conflict():
    """generalize vs cap are both rank-1: not a conflict, nothing held."""
    a = _approval("a.xlsx", {"X": "generalize"})
    b = _approval("b.xlsx", {"X": "cap"})
    rebuilt, info = extract._apply_cross_form_consistency([a, b])
    assert all(x.status == "approved" for x in rebuilt)
    assert info["conflicts"] == {}
    assert info["checked_columns"] == 1


def test_cross_form_no_conflict_passthrough():
    """All forms agree on every column -> no changes, empty conflicts."""
    approvals = [
        _approval("a.xlsx", {"SUBJID": "pseudonymize", "AGE": "keep"}),
        _approval("b.xlsx", {"SUBJID": "pseudonymize", "AGE": "keep"}),
    ]
    rebuilt, info = extract._apply_cross_form_consistency(approvals)
    assert rebuilt is approvals  # untouched
    assert info["conflicts"] == {}
    assert info["checked_columns"] == 2


def test_cross_form_record_is_value_free():
    """The audit record carries only column NAMES + counts (no values)."""
    approvals = [
        _approval("a.xlsx", {"SUBJID": "keep"}),
        _approval("b.xlsx", {"SUBJID": "drop"}),
    ]
    _, info = extract._apply_cross_form_consistency(approvals)
    import json

    text = json.dumps(info)
    assert "SUBJID" in text  # column name only
    for marker in ("raw", "sample", "Alice", "555-"):
        assert marker not in text
