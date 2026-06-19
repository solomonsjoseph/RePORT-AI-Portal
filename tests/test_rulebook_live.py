"""Tests for the N7 live rulebook (fetch latest → AI-extract → verify → merge).

No network, no live model: a fake fetcher returns canned (body, sha) and a fake
client returns canned extracted rules. The guarantees under test: extracted rules
are deterministically verified, merged OVER the pinned floor (additive, never
weakening), reused-if-unchanged, and fall back to pinned offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.security.phi_review import StudyPrivacyConfig, refresh_jurisdiction_rules
from scripts.security.phi_rulebook import (
    RulebookUnavailableError,
    _merge_over_pinned,
    detect_protection_weakening,
    resolve_live_rulebook,
    resolve_rulebook,
    verify_extracted_rules,
)

_USA_SRC = "https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html"


def _privacy(refresh="online_preferred"):
    return StudyPrivacyConfig(
        study_dir=Path("study_n7"),
        jurisdictions=("USA",),
        rule_refresh=refresh,
        conflict_policy="strictest_wins",
        max_synthetic_attempts=5,
        approval_mode="hybrid",
        parallelism_mode="auto",
        data_as_of=None,
        kanon_publish_gate={},
    )


class _Fetcher:
    """Callable returning canned (body, sha) per url; None,None when offline."""

    def __init__(self, body: bytes | None, sha: str | None):
        self._body, self._sha = body, sha

    def __call__(self, url):
        return self._body, self._sha


class _Client:
    """Fake LLM client: invoke_json returns a canned extracted-rule list."""

    def __init__(self, rules: list[dict], *, raise_if_called=False):
        self._rules = rules
        self._raise = raise_if_called
        self.calls = 0

    def invoke_json(self, system_prompt, user_prompt):
        self.calls += 1
        if self._raise:
            raise AssertionError("client should not be called on a cache hit")
        return list(self._rules)


_GOOD_RULE = {
    "id": "live_usa_birthdate_fr",
    "action": "jitter_date",
    "patterns": [r"\bnaissance\b"],
    "reason": "HIPAA Safe Harbor date element (French label).",
}


# ── verify_extracted_rules ──────────────────────────────────────────────────


def test_verify_accepts_good_rule():
    rules = verify_extracted_rules([_GOOD_RULE], jurisdiction="USA", source_url=_USA_SRC)
    assert len(rules) == 1
    assert rules[0].action.value == "jitter_date"


def test_verify_rejects_non_namespaced_id():
    bad = {**_GOOD_RULE, "id": "birthdate"}
    assert verify_extracted_rules([bad], jurisdiction="USA", source_url=_USA_SRC) == ()


def test_verify_rejects_unknown_action():
    bad = {**_GOOD_RULE, "action": "obliterate"}
    assert verify_extracted_rules([bad], jurisdiction="USA", source_url=_USA_SRC) == ()


def test_verify_rejects_over_broad_pattern():
    bad = {**_GOOD_RULE, "patterns": [".*"]}
    assert verify_extracted_rules([bad], jurisdiction="USA", source_url=_USA_SRC) == ()


def test_verify_rejects_pattern_matching_benign_header():
    # unanchored "date" matches "update_flag" (a benign probe) → rejected
    bad = {**_GOOD_RULE, "patterns": ["date"]}
    assert verify_extracted_rules([bad], jurisdiction="USA", source_url=_USA_SRC) == ()


def test_verify_rejects_non_official_source():
    assert (
        verify_extracted_rules([_GOOD_RULE], jurisdiction="USA", source_url="http://evil.com") == ()
    )


# ── merge + weakening ───────────────────────────────────────────────────────


def test_merge_is_additive_and_never_weakens():
    pinned = refresh_jurisdiction_rules(_privacy(), allow_network=False)
    extracted = verify_extracted_rules([_GOOD_RULE], jurisdiction="USA", source_url=_USA_SRC)
    merged = _merge_over_pinned(pinned, extracted, sources=[dict(s) for s in pinned.sources])
    assert len(merged.rules) == len(pinned.rules) + 1
    assert detect_protection_weakening(pinned, merged, _privacy()) == ()


# ── resolve_live_rulebook ───────────────────────────────────────────────────


def test_live_extract_adds_rule(tmp_path):
    from scripts.security.phi_review import classify_headers

    fetcher = _Fetcher(b"<official regulation text>", "sha_v1")
    client = _Client([_GOOD_RULE])
    res = resolve_live_rulebook(
        _privacy(), allow_network=True, fetcher=fetcher, client=client, cache_dir=tmp_path
    )
    assert res.cache_status == "live_fetch"
    assert res.bundle.source_mode == "latest_official_ai"
    # the new header is now classified by the extracted rule
    cls = classify_headers(["naissance"], _privacy(), res.bundle)
    assert cls["naissance"].action.value == "jitter_date"


def test_reuse_if_unchanged_skips_llm(tmp_path):
    fetcher = _Fetcher(b"<text>", "sha_stable")
    # First run extracts + writes the v2 cache.
    resolve_live_rulebook(
        _privacy(),
        allow_network=True,
        fetcher=fetcher,
        client=_Client([_GOOD_RULE]),
        cache_dir=tmp_path,
    )
    # Second run: same source hash → cache hit, client must NOT be called.
    res = resolve_live_rulebook(
        _privacy(),
        allow_network=True,
        fetcher=fetcher,
        client=_Client([_GOOD_RULE], raise_if_called=True),
        cache_dir=tmp_path,
    )
    assert res.cache_status == "cache_hit_live"


def test_offline_falls_back_to_pinned(tmp_path):
    # No prior v2 live cache → offline drops to the pinned floor.
    res = resolve_live_rulebook(
        _privacy(),
        allow_network=True,
        fetcher=_Fetcher(None, None),
        client=_Client([]),
        cache_dir=tmp_path,
    )
    assert res.bundle.source_mode != "latest_official_ai"
    assert res.offline_warning is not None


def test_offline_reuses_saved_live_cache(tmp_path):
    # First an online run extracts + saves the v2 live cache.
    resolve_live_rulebook(
        _privacy(),
        allow_network=True,
        fetcher=_Fetcher(b"<text>", "sha1"),
        client=_Client([_GOOD_RULE]),
        cache_dir=tmp_path,
    )
    # Then fully offline → reuse the SAVED live rules (not pinned-only); no LLM call.
    res = resolve_live_rulebook(
        _privacy(),
        allow_network=False,
        fetcher=_Fetcher(None, None),
        client=_Client([], raise_if_called=True),
        cache_dir=tmp_path,
    )
    assert res.cache_status == "cache_hit_live_offline"
    assert res.bundle.source_mode == "latest_official_ai"
    assert res.offline_warning is not None


def test_require_live_hard_fails_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RULEBOOK_REQUIRE_LIVE", True)
    with pytest.raises(RulebookUnavailableError):
        resolve_live_rulebook(
            _privacy(),
            allow_network=True,
            fetcher=_Fetcher(None, None),
            client=_Client([]),
            cache_dir=tmp_path,
        )


def test_router_default_off_uses_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RULEBOOK_AI_EXTRACT", False)
    res = resolve_rulebook(_privacy(), allow_network=True, cache_dir=tmp_path, seed_dir=tmp_path)
    # Default-off → pinned path; never the AI source_mode.
    assert res.bundle.source_mode != "latest_official_ai"
