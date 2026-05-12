"""Process-role helper - single source of truth for REPORTAL_PROCESS_ROLE."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from scripts.audit import (
    PROCESS_ROLE_ENV_VAR,
    PROCESS_ROLE_LLM_AGENT,
    current_process_role,
    is_llm_agent,
)


@pytest.fixture
def clean_role(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(PROCESS_ROLE_ENV_VAR, raising=False)
    yield


def test_constants_exported(clean_role: None) -> None:
    assert PROCESS_ROLE_ENV_VAR == "REPORTAL_PROCESS_ROLE"
    assert PROCESS_ROLE_LLM_AGENT == "llm-agent"


def test_current_process_role_unset(clean_role: None) -> None:
    assert current_process_role() is None


def test_current_process_role_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "pipeline")
    assert current_process_role() == "pipeline"


def test_is_llm_agent_true_only_for_exact_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")
    assert is_llm_agent()


def test_is_llm_agent_false_when_unset(clean_role: None) -> None:
    assert not is_llm_agent()


def test_is_llm_agent_false_for_other_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "pipeline")
    assert not is_llm_agent()
    monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm_agent")  # underscore, not dash
    assert not is_llm_agent()
