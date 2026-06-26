"""Tests for AI-assisted PHI header→rule alignment (Note 9) — no live LLM.

A FakeAligner returns canned proposals so the full verify/align/hold loop runs
deterministically. The guarantees under test: the AI may only map a header to an
EXISTING rulebook action via a verified regex; anything invented, over-broad,
mis-cited, or rulebook-disagreeing is rejected; persistent failure → human review.
"""

from __future__ import annotations

from scripts.security.phi_alignment import (
    AlignedRule,
    align_uncovered_headers,
    verify_aligned_rule,
)

_OFFICIAL = "https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html"

RULEBOOK = {
    "rules": [
        {
            "id": "usa_safe_harbor_dates",
            "jurisdiction": "USA",
            "action": "jitter_date",
            "reason": "date",
        },
        {
            "id": "usa_safe_harbor_direct_identifiers",
            "jurisdiction": "USA",
            "action": "drop",
            "reason": "id",
        },
    ],
    "sources": [{"jurisdiction": "USA", "title": "HHS", "url": _OFFICIAL}],
}


class FakeAligner:
    """Returns a pre-set proposal dict per header (or raises if unset)."""

    def __init__(self, by_header: dict[str, dict]):
        self._by_header = by_header

    def align_one(self, header, rulebook_json, jurisdictions):
        if header not in self._by_header:
            raise KeyError(header)
        return dict(self._by_header[header])


def _good_birthdate(header="b_dat"):
    return {
        "inferred_variable_type": "birth_date",
        "action": "jitter_date",
        "regex_pattern": r"^b_?dat$",
        "matched_rule_id": "usa_safe_harbor_dates",
        "rule_citation": _OFFICIAL,
        "jurisdictions": ["USA"],
        "reason": "birth date variant",
        "confidence": 0.9,
    }


def test_verify_accepts_well_formed_alignment():
    rule = AlignedRule(header="b_dat", **_good_birthdate())
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert ok, errors


def test_align_happy_path_returns_rule():
    aligned, held = align_uncovered_headers(
        ["b_dat"], RULEBOOK, ("USA",), aligner=FakeAligner({"b_dat": _good_birthdate()})
    )
    assert held == []
    assert len(aligned) == 1
    assert aligned[0].action == "jitter_date"
    assert aligned[0].matched_rule_id == "usa_safe_harbor_dates"


def test_verify_rejects_invented_action():
    bad = _good_birthdate()
    bad["action"] = "obliterate"
    rule = AlignedRule(header="b_dat", **bad)
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert not ok
    assert any("not an allowed action" in e for e in errors)


def test_verify_rejects_over_broad_regex():
    bad = _good_birthdate()
    bad["regex_pattern"] = ".*"
    rule = AlignedRule(header="b_dat", **bad)
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert not ok
    assert any("over-broad" in e for e in errors)


def test_verify_rejects_regex_not_matching_own_header():
    bad = _good_birthdate()
    bad["regex_pattern"] = r"^subject_id$"
    rule = AlignedRule(header="b_dat", **bad)
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert not ok
    assert any("does not match its own header" in e for e in errors)


def test_verify_rejects_non_official_citation():
    bad = _good_birthdate()
    bad["rule_citation"] = "https://example.com/made-up"
    rule = AlignedRule(header="b_dat", **bad)
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert not ok
    assert any("official source" in e for e in errors)


def test_verify_rejects_action_disagreeing_with_rulebook():
    # cites the dates rule (jitter_date) but claims action=drop
    bad = _good_birthdate()
    bad["action"] = "drop"
    rule = AlignedRule(header="b_dat", **bad)
    ok, errors = verify_aligned_rule(rule, RULEBOOK)
    assert not ok
    assert any("disagrees with the cited rulebook rule" in e for e in errors)


def test_persistent_failure_routes_to_human_review():
    aligner = FakeAligner({"weird_col": {**_good_birthdate(), "action": "obliterate"}})
    aligned, held = align_uncovered_headers(["weird_col"], RULEBOOK, ("USA",), aligner=aligner)
    assert aligned == []
    assert len(held) == 1
    assert "could not produce a verifier-passing rule" in held[0].what_was_ambiguous


def test_aligner_error_routes_to_human_review():
    # FakeAligner raises KeyError for an unset header → treated as failed attempts → held
    aligned, held = align_uncovered_headers(
        ["missing"], RULEBOOK, ("USA",), aligner=FakeAligner({})
    )
    assert aligned == []
    assert len(held) == 1


def test_alignment_is_deterministic():
    aligner = FakeAligner({"b_dat": _good_birthdate()})
    a1, _ = align_uncovered_headers(["b_dat"], RULEBOOK, ("USA",), aligner=aligner)
    a2, _ = align_uncovered_headers(["b_dat"], RULEBOOK, ("USA",), aligner=aligner)
    assert [r.to_json() for r in a1] == [r.to_json() for r in a2]


def test_aligned_rule_is_value_free():
    import json

    rule = AlignedRule(header="b_dat", **_good_birthdate())
    text = json.dumps(rule.to_json())
    for marker in ("raw_value", "sample_value", "_phi_scrubbed"):
        assert marker not in text


def test_llm_aligner_prompt_is_header_and_rulebook_only_never_a_value():
    """GR-1 at the LLM boundary: the production aligner sends the column NAME + the
    value-free rulebook to the model and NOTHING else. ``align_one`` has no value
    parameter by construction; this locks that the prompt it builds stays value-free,
    so enabling alignment never exposes a dataset value to the LLM."""
    from scripts.security.phi_alignment import LLMHeaderAligner, _VALUE_MARKERS

    captured: dict[str, str] = {}

    class _CapturingClient:
        def invoke_json(self, system_prompt: str, user_prompt: str):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return _good_birthdate()

    LLMHeaderAligner(client=_CapturingClient()).align_one(
        "patient_aadhaar_no", RULEBOOK, ("INDIA", "USA")
    )

    # The header NAME and the value-free rulebook reach the model …
    assert "patient_aadhaar_no" in captured["user"]
    assert "usa_safe_harbor_direct_identifiers" in captured["user"]
    # … the system prompt commits to never seeing values …
    assert "never see data values" in captured["system"]
    # … and no dataset-value marker appears anywhere in the prompt (defense in depth).
    blob = captured["system"] + captured["user"]
    for marker in _VALUE_MARKERS:
        assert marker not in blob


# ── Integration: review_form_headers alignment wiring (default-off + on) ──


def _privacy():
    from pathlib import Path

    from scripts.security.phi_review import StudyPrivacyConfig

    return StudyPrivacyConfig(
        study_dir=Path("study_n9"),  # metadata only; never read in this test
        jurisdictions=("USA",),
        rule_refresh="pinned_only",
        conflict_policy="strictest_wins",
        max_synthetic_attempts=5,
        approval_mode="hybrid",
        parallelism_mode="auto",
        data_as_of=None,
        kanon_publish_gate={},
    )


# "fec_nac" (Spanish birth-date abbrev) is uncovered by the English pinned rules.
def _fec_nac_alignment():
    return {
        "inferred_variable_type": "birth_date",
        "action": "jitter_date",
        "regex_pattern": r"^fec_nac$",
        "matched_rule_id": "usa_safe_harbor_dates",
        "rule_citation": _OFFICIAL,
        "jurisdictions": ["USA"],
        "reason": "Spanish birth-date abbreviation",
        "confidence": 0.85,
    }


def test_review_form_headers_default_off_is_unchanged():
    from scripts.security.phi_review import refresh_jurisdiction_rules, review_form_headers

    pc = _privacy()
    bundle = refresh_jurisdiction_rules(pc, allow_network=False)
    approval = review_form_headers(
        form_name="f", headers=("fec_nac", "age"), privacy_config=pc, rule_bundle=bundle
    )
    # No aligner → uncovered header stays KEEP; no aligned rules recorded.
    assert approval.actions["fec_nac"] == "keep"
    assert approval.aligned_rules == ()


def test_review_form_headers_alignment_upgrades_uncovered_header():
    from scripts.security.phi_review import refresh_jurisdiction_rules, review_form_headers

    pc = _privacy()
    bundle = refresh_jurisdiction_rules(pc, allow_network=False)
    aligner = FakeAligner({"fec_nac": _fec_nac_alignment()})
    approval = review_form_headers(
        form_name="f",
        headers=("fec_nac",),
        privacy_config=pc,
        rule_bundle=bundle,
        aligner=aligner,
    )
    # The uncovered KEEP header is UPGRADED to the aligned (stronger) action.
    assert approval.actions["fec_nac"] == "jitter_date"
    assert len(approval.aligned_rules) == 1
    assert approval.aligned_rules[0]["matched_rule_id"] == "usa_safe_harbor_dates"
    assert approval.aligned_rules[0]["action"] == "jitter_date"
