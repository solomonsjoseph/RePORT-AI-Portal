"""API-key redaction patterns for ``scripts.utils.log_hygiene``.

Companion to ``test_log_hygiene.py`` (which covers PHI patterns). After
PR #3 the keystore keeps keys out of ``os.environ`` entirely, so keys
never reach the logger via env-var dump. But defense in depth: if a key
ever lands in a log message — through a stack trace, a tool call, or a
copy-paste — the redactor must scrub it before the message is written
to ``.logs/``.
"""

from __future__ import annotations

from scripts.utils.log_hygiene import API_KEY_PATTERNS, PHIRedactingFilter, _redact

# A 32-byte dummy key — these tests only exercise the API-key patterns,
# never the subject-ID HMAC pass.
_DUMMY_HMAC_KEY = b"\x00" * 32


def _filter() -> PHIRedactingFilter:
    return PHIRedactingFilter(hmac_key=_DUMMY_HMAC_KEY)


# ── Provider-specific redaction ─────────────────────────────────────────────


def test_redacts_anthropic_key() -> None:
    raw = "auth failed for sk-ant-api03-A1b2C3d4E5f6G7h8I9j0K1L2M3N4O5P6Q7R8S9T0_USER123"
    out = _redact(raw, _filter())
    assert "sk-ant-api03" not in out
    assert "<ANTHROPIC_KEY>" in out


def test_redacts_openai_key() -> None:
    raw = "request failed: sk-proj-A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2"
    out = _redact(raw, _filter())
    assert "sk-proj-A1B2" not in out
    assert "<OPENAI_KEY>" in out


def test_redacts_openai_key_no_proj_prefix() -> None:
    raw = "Bearer sk-A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0"
    out = _redact(raw, _filter())
    assert "sk-A1B2C3D4" not in out
    assert "<OPENAI_KEY>" in out


def test_redacts_nvidia_key() -> None:
    raw = "Authorization: Bearer nvapi-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    out = _redact(raw, _filter())
    assert "nvapi-aBcDeFgHi" not in out
    assert "<NVIDIA_KEY>" in out


def test_redacts_google_key() -> None:
    raw = "Failed: AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"
    out = _redact(raw, _filter())
    assert "AIzaSyA1B2" not in out
    assert "<GOOGLE_KEY>" in out


# ── False-positive avoidance ────────────────────────────────────────────────


def test_short_sk_prefix_not_redacted() -> None:
    """A bare ``sk-`` reference without the full key length is NOT redacted —
    short flags / config keys / docs must pass through unchanged."""
    raw = "config field 'sk-flag' enabled"
    out = _redact(raw, _filter())
    assert "sk-flag" in out


def test_short_aiza_prefix_not_redacted() -> None:
    raw = "see helper AIzaShortName"  # too short for the AIza-key pattern
    out = _redact(raw, _filter())
    assert "AIzaShortName" in out


def test_normal_text_unchanged() -> None:
    raw = "Pipeline complete; 1234 records processed."
    out = _redact(raw, _filter())
    assert out == raw  # no false positives on plain prose


# ── Multi-line / embedded contexts ──────────────────────────────────────────


def test_redacts_key_in_stack_trace() -> None:
    """A key embedded in a multi-line traceback should still be scrubbed."""
    raw = (
        "Traceback:\n"
        '  File "x.py", line 1\n'
        "    response = client.completions(api_key='sk-ant-api03-LONGKEYBODYABCDEFGHIJKLMNOPQRSTUVWXYZ_ABC')\n"
        "AuthenticationError: bad key\n"
    )
    out = _redact(raw, _filter())
    assert "sk-ant-api03-LONGKEYBODY" not in out
    assert "<ANTHROPIC_KEY>" in out


def test_redacts_multiple_keys_in_one_line() -> None:
    raw = (
        "primary=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
        "fallback=sk-A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0"
    )
    out = _redact(raw, _filter())
    assert "sk-ant-api03-AAAA" not in out
    assert "sk-A1B2C3D4" not in out
    assert out.count("<ANTHROPIC_KEY>") == 1
    assert out.count("<OPENAI_KEY>") == 1


# ── Pattern catalog completeness ────────────────────────────────────────────


def test_pattern_catalog_covers_supported_providers() -> None:
    labels = {label for label, _ in API_KEY_PATTERNS}
    assert labels == {"ANTHROPIC_KEY", "OPENAI_KEY", "NVIDIA_KEY", "GOOGLE_KEY"}
