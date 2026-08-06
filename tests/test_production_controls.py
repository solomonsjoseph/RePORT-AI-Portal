"""Production-only fail-closed control tests."""

from __future__ import annotations

import pytest

import config
import main
from scripts.ai_assistant.ui.chat import _rate_limit_status
from scripts.security.phi_scrub import PHIKeyMissingError


def test_phi_log_redactor_missing_key_fails_closed_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPORT_AI_PRODUCTION", "1")

    def _missing_key() -> bytes:
        raise PHIKeyMissingError("missing")

    monkeypatch.setattr(main, "_load_phi_key", _missing_key)

    with pytest.raises(RuntimeError, match="Production startup refused"):
        main._install_log_redactor_best_effort()


def test_production_mode_is_enabled_by_proxy_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPORT_AI_PRODUCTION", raising=False)
    monkeypatch.setenv("REPORT_AI_AUTH_MODE", "proxy")

    assert config.production_mode_enabled()


def test_local_streamlit_launch_uses_next_free_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPORT_AI_PRODUCTION", raising=False)
    monkeypatch.delenv("REPORT_AI_AUTH_MODE", raising=False)
    monkeypatch.delenv("REPORT_AI_REQUIRE_PHI_LOG_REDACTOR", raising=False)
    monkeypatch.delenv("STREAMLIT_SERVER_PORT", raising=False)
    monkeypatch.setenv("STREAMLIT_SERVER_ADDRESS", "127.0.0.1")

    def _available(host: str, port: int) -> bool:
        return host == "127.0.0.1" and port == 8502

    monkeypatch.setattr(main, "_streamlit_port_available", _available)

    cmd = main._streamlit_launch_command()

    assert cmd[-2:] == ["--server.port", "8502"]


def test_production_streamlit_launch_keeps_fixed_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPORT_AI_PRODUCTION", "1")
    monkeypatch.delenv("STREAMLIT_SERVER_PORT", raising=False)

    cmd = main._streamlit_launch_command()

    assert "--server.port" not in cmd


def test_chat_rate_limit_blocks_after_configured_turn_count() -> None:
    allowed, retained, retry_after = _rate_limit_status(
        [100.0, 110.0],
        now=120.0,
        window_seconds=60,
        max_turns=2,
    )

    assert not allowed
    assert retained == [100.0, 110.0]
    assert retry_after == 40


def test_chat_rate_limit_drops_old_timestamps() -> None:
    allowed, retained, retry_after = _rate_limit_status(
        [1.0, 50.0],
        now=80.0,
        window_seconds=60,
        max_turns=2,
    )

    assert allowed
    assert retained == [50.0, 80.0]
    assert retry_after == 0
