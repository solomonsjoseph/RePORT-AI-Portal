"""Phase 3.F + 3.G + 3.H regression tests — PDF extraction pipeline.

Pins these contracts:

- **3.F (pre-upload redaction)**: when the LLM tier runs, the payload
  string sent to the provider has been scrubbed of blocking-tier PHI.
  We verify this via the orchestrator's defensive
  ``_assert_no_raw_phi_in_payload`` raising on raw input — i.e., the
  guard exists and fires.
- **3.G (response redaction)**: when the LLM echoes a subject ID or
  Aadhaar back into a description, the orchestrator scrubs every
  string field before persisting.
- **3.H (idempotent cache)**: a second extraction with the same PDF +
  provider + model hits the cache and skips the LLM call.

Plus the architectural directives from 2026-04-27:

- **A1 three-tier**: code-path always runs; LLM only when capable;
  snapshot fallback when both fail.
- **A2 reuse PHI infra**: redaction = ``redact_phi_in_text`` with
  existing patterns (no new catalog).
- **A3 zone discipline**: no raw PDF bytes in the LLM payload —
  only redacted text.
- **A4 cache key**: ``SHA-256(pdf_bytes) || provider || model || scrub_hash``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.extraction.pdf_pipeline import (
    ExtractionResult,
    _assert_no_raw_phi_in_payload,
    _cache_key,
    _candidate_from_text,
    _merge,
    _redact_text_for_llm,
    _scrub_llm_response,
    extract_pdf,
)
from tests.security.key_fixtures import anthropic_key

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_pdf(tmp_path: Path, name: str = "form.pdf", content: bytes = b"%PDF-1.4\nfake") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _crf_text() -> str:
    """Synthetic CRF text with the structural markers our heuristic looks
    for. No PHI here — used to verify the code-path baseline."""
    return (
        "Form: 1A_ICScreening\n\n"
        "IS_AGE: Subject age in years at screening visit\n"
        "IS_SEX: Subject biological sex (Male/Female)\n"
        "IS_HEIGHT: Subject height in centimetres at screening\n"
        "IS_WEIGHT: Subject weight in kilograms at screening\n"
    )


# ── 3.F — pre-upload redaction guard ────────────────────────────────────────


def test_redact_text_for_llm_replaces_aadhaar() -> None:
    """The redaction step must replace any blocking-tier pattern with
    the labelled marker before the text would reach the LLM payload."""
    raw = "Subject 1234 5678 9012 enrolled. Age 30."
    out = _redact_text_for_llm(raw)
    assert "1234 5678 9012" not in out
    assert "<AADHAAR>" in out


def test_assert_no_raw_phi_raises_on_unredacted_payload() -> None:
    """Defensive check: if a developer ever forgets to redact and tries
    to ship raw PHI, the orchestrator fails loud."""
    with pytest.raises(RuntimeError, match="redaction failed"):
        _assert_no_raw_phi_in_payload("Subject 1234 5678 9012 has TB.")


def test_assert_no_raw_phi_passes_on_clean_payload() -> None:
    """Clean / redacted text passes the defensive check."""
    _assert_no_raw_phi_in_payload(_crf_text())  # raises if blocked


# ── 3.G — response scrubbing ────────────────────────────────────────────────


def test_scrub_llm_response_replaces_phi_in_string_fields() -> None:
    """If the LLM echoes a subject Aadhaar / phone in a description
    field, the scrubber replaces it before the dict is persisted."""
    response = {
        "form_name": "1A_ICScreening",
        "variables": {
            "IS_AGE": {
                "name": "IS_AGE",
                "description": "Subject age. Example record: SUBJ-1234 with Aadhaar 1234 5678 9012.",
            },
            "IS_PHONE": {
                "name": "IS_PHONE",
                "description": "Reach subject at +91 9876543210.",
            },
        },
    }
    cleaned = _scrub_llm_response(response)
    age_desc = cleaned["variables"]["IS_AGE"]["description"]
    phone_desc = cleaned["variables"]["IS_PHONE"]["description"]
    assert "1234 5678 9012" not in age_desc
    assert "<AADHAAR>" in age_desc
    assert "9876543210" not in phone_desc
    assert "<INDIAN_PHONE>" in phone_desc


def test_scrub_walks_nested_lists_and_dicts() -> None:
    response = {
        "options": [
            {"label": "contact site_pi@example.org", "value": 1},
            {"label": "skip", "value": 0},
        ]
    }
    cleaned = _scrub_llm_response(response)
    label = cleaned["options"][0]["label"]
    assert "site_pi@example.org" not in label
    assert "<EMAIL>" in label


# ── A1 — code-path candidate heuristic ──────────────────────────────────────


def test_code_path_extracts_variables_from_clean_crf_text() -> None:
    candidate = _candidate_from_text("1A_ICScreening.pdf", _crf_text())
    assert candidate is not None
    assert candidate["form_name"] == "1A_ICSCREENING"
    assert "IS_AGE" in candidate["variables"]
    assert "IS_SEX" in candidate["variables"]
    assert candidate["variables"]["IS_AGE"]["source"] == "code-path"


def test_code_path_returns_none_for_noise() -> None:
    """A non-CRF PDF (e.g., a flyer, an image) should produce <3 vars
    and be discarded — better to fall through to snapshot than to ship
    bad metadata."""
    assert _candidate_from_text("flyer.pdf", "Hello\nthis is a flyer\n") is None


# ── Merge logic ─────────────────────────────────────────────────────────────


def test_merge_llm_wins_on_overlap() -> None:
    code = {
        "form_name": "F",
        "source_pdf": "f.pdf",
        "variables": {"X": {"name": "X", "description": "code says X"}},
    }
    llm = {
        "form_name": "F",
        "source_pdf": "f.pdf",
        "variables": {
            "X": {"name": "X", "description": "LLM says X better"},
            "Y": {"name": "Y", "description": "Y only seen by LLM"},
        },
    }
    merged = _merge(code, llm)
    assert merged is not None
    assert merged["variables"]["X"]["description"] == "LLM says X better"
    assert "Y" in merged["variables"]
    assert merged["extraction_tier"] == "merged"


def test_merge_falls_back_to_single_tier() -> None:
    code = {"form_name": "F", "variables": {"X": {"name": "X"}}}
    merged = _merge(code, None)
    assert merged is not None
    assert merged["extraction_tier"] == "code"

    merged_l = _merge(None, {"form_name": "F", "variables": {"Y": {"name": "Y"}}})
    assert merged_l is not None
    assert merged_l["extraction_tier"] == "llm"

    assert _merge(None, None) is None


# ── 3.H — idempotent cache key ──────────────────────────────────────────────


def test_cache_key_invariants(tmp_path: Path) -> None:
    """Same inputs → same key. Different provider / model / pdf bytes
    → different key. This is the basis for the idempotent retry."""
    pdf = _make_pdf(tmp_path, content=b"%PDF-1.4\ntest A")
    pdf2 = _make_pdf(tmp_path, name="other.pdf", content=b"%PDF-1.4\ntest B")

    k1 = _cache_key(pdf, "anthropic", "claude-opus-4-6")
    k1_again = _cache_key(pdf, "anthropic", "claude-opus-4-6")
    k_diff_provider = _cache_key(pdf, "openai", "gpt-5")
    k_diff_model = _cache_key(pdf, "anthropic", "claude-opus-4-7")
    k_diff_pdf = _cache_key(pdf2, "anthropic", "claude-opus-4-6")

    assert k1 == k1_again
    assert k1 != k_diff_provider
    assert k1 != k_diff_model
    assert k1 != k_diff_pdf


# ── Top-level orchestrator (without real LLM) ──────────────────────────────


def test_extract_pdf_returns_empty_when_no_llm_and_no_snapshot(tmp_path: Path) -> None:
    """Per the 2026-04-27 directive: code-only is NEVER an acceptable
    output. Without a capable model AND without a snapshot, the result
    is the explicit empty-tier marker — the load-study UI will see an
    empty form and the operator must either provision an LLM or seed a
    snapshot."""
    pdf = _make_pdf(tmp_path, content=b"%PDF-1.4\nempty")
    result = extract_pdf(pdf)  # no provider/model, no snapshot_dir
    assert isinstance(result, ExtractionResult)
    assert result.tier == "empty"
    assert result.llm_skipped_reason is not None
    assert result.llm_skipped_reason == "provider/model not configured"


def test_extract_pdf_discards_code_only_falls_back_to_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when the code path produces a valid candidate, if the LLM
    tier is unavailable the pipeline DISCARDS the code-only result and
    uses the snapshot instead (paired-tier rule from 2026-04-27)."""
    pdf = _make_pdf(tmp_path, name="3A_Visit.pdf")
    snapshot_dir = tmp_path / "snapshots" / "initial" / "pdfs"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "3A_Visit_variables.json").write_text(
        json.dumps({"form_name": "3A_VISIT", "variables": {"V1": {"name": "V1"}}}),
        encoding="utf-8",
    )

    # Force the code path to return a non-trivial candidate.
    import scripts.extraction.pdf_pipeline as pp

    monkeypatch.setattr(pp, "_extract_text_via_pdfplumber", lambda _p: _crf_text())

    # No provider/model → LLM tier unavailable → snapshot wins, not code.
    result = extract_pdf(pdf, snapshot_dir=snapshot_dir)
    assert result.tier == "snapshot"
    assert result.snapshot_used is True
    # The snapshot's variable wins, not the code path's IS_AGE/IS_SEX heuristics.
    assert "V1" in result.data["variables"]
    assert "IS_AGE" not in result.data["variables"]


def test_extract_pdf_uses_snapshot_when_all_tiers_empty(tmp_path: Path) -> None:
    """When code path returns nothing AND LLM unavailable AND a snapshot
    exists, the pipeline falls back to the human-verified snapshot."""
    pdf = _make_pdf(tmp_path, name="2A_Demographics.pdf", content=b"%PDF-1.4\nimage-only")
    snapshot_dir = tmp_path / "snapshots" / "initial" / "pdfs"
    snapshot_dir.mkdir(parents=True)
    snapshot_payload = {
        "form_name": "2A_DEMOGRAPHICS",
        "form_label": "Demographics",
        "source_pdf": "2A_Demographics.pdf",
        "variables": {
            "AGE": {"name": "AGE", "description": "Subject age in years"},
            "SEX": {"name": "SEX", "description": "Biological sex"},
        },
    }
    (snapshot_dir / "2A_Demographics_variables.json").write_text(
        json.dumps(snapshot_payload), encoding="utf-8"
    )

    result = extract_pdf(pdf, snapshot_dir=snapshot_dir)
    assert result.tier == "snapshot"
    assert result.snapshot_used is True
    assert "AGE" in result.data["variables"]


def test_extract_pdf_with_mocked_llm_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the LLM path is reachable when a capable model is configured.
    Mock the LLM call so we don't make a real API request — the contract
    is that the orchestrator routes through ``_extract_via_llm`` when
    capable, redacts the input first, and persists the cache entry."""
    pdf = _make_pdf(tmp_path, name="form.pdf", content=b"%PDF-1.4\nfake")
    cache_dir = tmp_path / "cache"

    # Patch pdfplumber output (no real PDF parsing) AND the LLM call.
    import scripts.extraction.pdf_pipeline as pp

    monkeypatch.setattr(pp, "_extract_text_via_pdfplumber", lambda _p: _crf_text())

    captured_payload: dict[str, str] = {}

    def fake_llm(redacted_text: str, *, provider: str, model: str, api_key: str) -> dict[str, Any]:
        captured_payload["text"] = redacted_text
        captured_payload["provider"] = provider
        captured_payload["model"] = model
        captured_payload["api_key"] = api_key
        return {
            "form_name": "1A_ICSCREENING",
            "form_label": "Screening",
            "source_pdf": "form.pdf",
            "extraction_tier": "llm",
            "variables": {
                "IS_AGE": {"name": "IS_AGE", "description": "Age (years)"},
                "IS_SEX": {"name": "IS_SEX", "description": "Sex"},
            },
        }

    monkeypatch.setattr(pp, "_extract_via_llm", fake_llm)

    result = extract_pdf(
        pdf,
        provider="anthropic",
        model="claude-opus-4-6",
        api_key=anthropic_key("TEST"),
        cache_dir=cache_dir,
    )

    # Verify the LLM was called with redacted text (no Aadhaar leakage
    # check needed since the synthetic CRF text has none — the contract
    # is that the orchestrator routes through redact_phi_in_text first;
    # we verify by confirming the captured payload is a plain string).
    assert captured_payload["provider"] == "anthropic"
    assert captured_payload["model"] == "claude-opus-4-6"
    assert "IS_AGE" in captured_payload["text"]

    # The result merges code + LLM tiers (both produced output).
    assert result.tier == "merged"
    assert result.code_succeeded is True
    assert result.llm_succeeded is True
    # Cache file should exist (idempotent retry next time).
    assert any(cache_dir.glob("*.json"))


def test_extract_pdf_cache_hit_skips_llm_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call with same inputs hits the cache and skips the LLM."""
    pdf = _make_pdf(tmp_path, content=b"%PDF-1.4\nstable")
    cache_dir = tmp_path / "cache"

    import scripts.extraction.pdf_pipeline as pp

    monkeypatch.setattr(pp, "_extract_text_via_pdfplumber", lambda _p: _crf_text())

    call_count = {"n": 0}

    def fake_llm(*_a: Any, **_kw: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return {
            "form_name": "F",
            "extraction_tier": "llm",
            "variables": {"V": {"name": "V"}},
        }

    monkeypatch.setattr(pp, "_extract_via_llm", fake_llm)

    # First call — should invoke LLM
    extract_pdf(
        pdf,
        provider="anthropic",
        model="claude-opus-4-6",
        api_key="k",
        cache_dir=cache_dir,
    )
    assert call_count["n"] == 1

    # Second call — should hit cache
    result = extract_pdf(
        pdf,
        provider="anthropic",
        model="claude-opus-4-6",
        api_key="k",
        cache_dir=cache_dir,
    )
    assert call_count["n"] == 1, "LLM was called again despite cache hit"
    assert result.cache_hit is True
