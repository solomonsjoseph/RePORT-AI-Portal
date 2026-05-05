"""Small helpers for log-safe diagnostics."""

from __future__ import annotations

from typing import Literal

__all__ = [
    "safe_provider_model_diagnostic",
]

ValueState = Literal["missing", "blank", "present"]


def _value_state(value: str | None) -> ValueState:
    if value is None:
        return "missing"
    if not value.strip():
        return "blank"
    return "present"


def safe_provider_model_diagnostic(*, provider: str | None, model: str | None) -> str:
    """Return a log-safe summary for operator-supplied provider/model values."""
    return f"provider={_value_state(provider)} model={_value_state(model)}"
