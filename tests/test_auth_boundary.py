"""Authentication-boundary regression tests."""

from __future__ import annotations

from scripts.ai_assistant.ui.auth import evaluate_auth


def test_local_mode_allows_loopback() -> None:
    result = evaluate_auth(headers={}, ip="127.0.0.1", environ={})

    assert result.ok
    assert result.user == "local"


def test_local_mode_allows_streamlit_test_context_ip() -> None:
    result = evaluate_auth(headers={}, ip=object(), environ={})

    assert result.ok


def test_local_mode_rejects_remote_client() -> None:
    result = evaluate_auth(headers={}, ip="203.0.113.5", environ={})

    assert not result.ok
    assert "loopback" in result.reason


def test_proxy_mode_requires_shared_secret() -> None:
    result = evaluate_auth(
        headers={"X-Forwarded-User": "operator@example.org"},
        ip="127.0.0.1",
        environ={"REPORT_AI_AUTH_MODE": "proxy"},
    )

    assert not result.ok
    assert "SHARED_SECRET" in result.reason


def test_proxy_mode_requires_matching_secret_and_user() -> None:
    env = {
        "REPORT_AI_AUTH_MODE": "proxy",
        "REPORT_AI_PROXY_SHARED_SECRET": "s3cret",
    }

    assert not evaluate_auth(
        headers={"X-Report-AI-Proxy-Secret": "wrong", "X-Forwarded-User": "op"},
        ip="127.0.0.1",
        environ=env,
    ).ok

    result = evaluate_auth(
        headers={"X-Report-AI-Proxy-Secret": "s3cret", "X-Forwarded-User": "op"},
        ip="127.0.0.1",
        environ=env,
    )
    assert result.ok
    assert result.user == "op"
