"""Streamlit authentication boundary checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address

import streamlit as st

_LOCAL_USERS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    user: str = ""
    reason: str = ""


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value.strip()
    return ""


def _local_ip(value: object | None) -> bool:
    if not value:
        return True
    if not isinstance(value, str):
        return True
    host = value.strip().split(",", 1)[0].strip()
    if host in _LOCAL_USERS:
        return True
    try:
        parsed = ip_address(host)
        return parsed.is_loopback or parsed.is_unspecified
    except ValueError:
        return False


def evaluate_auth(
    *,
    headers: Mapping[str, str],
    ip: object | None,
    environ: Mapping[str, str] = os.environ,
) -> AuthResult:
    """Return whether the current request satisfies the configured auth boundary."""

    mode = environ.get("REPORT_AI_AUTH_MODE", "local").strip().lower()
    if mode == "local":
        if _local_ip(ip):
            return AuthResult(ok=True, user="local")
        return AuthResult(False, reason=f"Local mode only accepts loopback clients ({ip!r}).")

    if mode != "proxy":
        return AuthResult(False, reason=f"Unsupported REPORT_AI_AUTH_MODE={mode!r}.")

    user_header = environ.get("REPORT_AI_AUTH_USER_HEADER", "X-Forwarded-User")
    secret_header = environ.get("REPORT_AI_AUTH_SECRET_HEADER", "X-Report-AI-Proxy-Secret")
    expected_secret = environ.get("REPORT_AI_PROXY_SHARED_SECRET", "").strip()
    if not expected_secret:
        return AuthResult(False, reason="REPORT_AI_PROXY_SHARED_SECRET is required.")

    if _header(headers, secret_header) != expected_secret:
        return AuthResult(False, reason="Proxy shared secret is missing or invalid.")

    user = _header(headers, user_header)
    if not user:
        return AuthResult(False, reason=f"{user_header} is missing.")
    return AuthResult(ok=True, user=user)


def enforce_auth_boundary() -> str:
    """Fail closed before any PHI-capable UI renders."""

    headers = getattr(st.context, "headers", {}) or {}
    ip = getattr(st.context, "ip_address", None)
    result = evaluate_auth(headers=headers, ip=ip)
    if result.ok:
        st.session_state["authenticated_user"] = result.user
        return result.user

    st.error("Authentication boundary failed. Contact the deployment operator.")
    st.caption(result.reason)
    st.stop()
