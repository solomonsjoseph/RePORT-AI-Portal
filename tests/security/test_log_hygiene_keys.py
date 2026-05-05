"""API-key redaction patterns for ``scripts.utils.log_hygiene``."""

from __future__ import annotations

from scripts.utils.log_hygiene import API_KEY_PATTERNS, PHIRedactingFilter, _redact
from tests.security.key_fixtures import (
    anthropic_key,
    google_key,
    nvidia_key,
    openai_key,
    openai_project_key,
)

# A 32-byte dummy key; these tests exercise API-key patterns only.
_DUMMY_HMAC_KEY = b"\x00" * 32


def _filter() -> PHIRedactingFilter:
    return PHIRedactingFilter(hmac_key=_DUMMY_HMAC_KEY)


def test_redacts_anthropic_key() -> None:
    key = anthropic_key("R123")
    out = _redact(f"auth failed for {key}", _filter())

    assert key not in out
    assert "<ANTHROPIC_KEY>" in out


def test_redacts_openai_key() -> None:
    key = openai_project_key("U1V2")
    out = _redact(f"request failed: {key}", _filter())

    assert key not in out
    assert "<OPENAI_KEY>" in out


def test_redacts_openai_key_no_proj_prefix() -> None:
    key = openai_key("S9T0")
    out = _redact(f"Bearer {key}", _filter())

    assert key not in out
    assert "<OPENAI_KEY>" in out


def test_redacts_nvidia_key() -> None:
    key = nvidia_key("6789")
    out = _redact(f"Authorization: Bearer {key}", _filter())

    assert key not in out
    assert "<NVIDIA_KEY>" in out


def test_redacts_google_key() -> None:
    key = google_key()
    out = _redact(f"Failed: {key}", _filter())

    assert key not in out
    assert "<GOOGLE_KEY>" in out


def test_short_sk_prefix_not_redacted() -> None:
    """A bare ``sk-`` reference without full key length is not redacted."""
    raw = "config field 'sk-flag' enabled"
    out = _redact(raw, _filter())
    assert "sk-flag" in out


def test_short_aiza_prefix_not_redacted() -> None:
    raw = "see helper AIzaShortName"
    out = _redact(raw, _filter())
    assert "AIzaShortName" in out


def test_normal_text_unchanged() -> None:
    raw = "Pipeline complete; 1234 records processed."
    out = _redact(raw, _filter())
    assert out == raw


def test_redacts_key_in_stack_trace() -> None:
    """A key embedded in a multi-line traceback should still be scrubbed."""
    key = anthropic_key("Z_ABC")
    raw = (
        "Traceback:\n"
        '  File "x.py", line 1\n'
        f"    response = client.completions(api_key='{key}')\n"
        "AuthenticationError: bad key\n"
    )
    out = _redact(raw, _filter())

    assert key not in out
    assert "<ANTHROPIC_KEY>" in out


def test_redacts_multiple_keys_in_one_line() -> None:
    anthropic = anthropic_key("AAAA")
    openai = openai_key("S9T0")
    out = _redact(f"primary={anthropic} fallback={openai}", _filter())

    assert anthropic not in out
    assert openai not in out
    assert out.count("<ANTHROPIC_KEY>") == 1
    assert out.count("<OPENAI_KEY>") == 1


def test_pattern_catalog_covers_supported_providers() -> None:
    labels = {label for label, _ in API_KEY_PATTERNS}
    assert labels == {"ANTHROPIC_KEY", "OPENAI_KEY", "NVIDIA_KEY", "GOOGLE_KEY"}
