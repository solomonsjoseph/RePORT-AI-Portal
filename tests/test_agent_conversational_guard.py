"""Coverage for the conversational-shortcut guard on fuzzy search tools.

**What.** Tests the guard added in ``scripts.ai_assistant.agent_tools`` that
short-circuits ``search_variables``, ``find_variable_candidates``, and
``search_pdf_context`` when the caller passes a greeting / acknowledgement
/ too-short query. Without this guard, the ReAct agent's default routing
surfaces noisy substring hits for messages like "hi" (fuzzy-matches
"HIV_STATUS", history-related variables, etc.) and paraphrases them as a
name / variable answer — a poor UX even though the PHI safety gates fire
correctly.

**Why.** Documented by the user 2026-04-24 — *"when chatting with the LLM,
even hi is flagged as name"*. Root cause was the LLM's tool-routing
obedience, not the PHI gate. This guard is a UX-hygiene fix, not a
security control; ``phi_safe.guard_user_prompt`` still runs upstream and
refuses blocking-tier PHI.

**How.** Unit tests call the helper directly (no LLM, no I/O) and the
three ``@tool``-wrapped search functions via their underlying callable
(``@phi_safe_return`` preserves ``.func``).
"""

from __future__ import annotations

from typing import Any

from scripts.ai_assistant.agent_tools import (
    _CONVERSATIONAL_REFUSAL_MESSAGE,
    _query_looks_conversational,
)


class TestQueryLooksConversational:
    """The predicate the three search tools use to short-circuit."""

    def test_greeting_lowercase(self) -> None:
        assert _query_looks_conversational("hi") is True
        assert _query_looks_conversational("hello") is True
        assert _query_looks_conversational("hey") is True

    def test_greeting_mixed_case(self) -> None:
        assert _query_looks_conversational("Hi") is True
        assert _query_looks_conversational("Hello!") is True
        assert _query_looks_conversational("HEY") is True

    def test_acknowledgement(self) -> None:
        assert _query_looks_conversational("thanks") is True
        assert _query_looks_conversational("thank you") is True
        assert _query_looks_conversational("ok") is True
        assert _query_looks_conversational("cool") is True
        assert _query_looks_conversational("got it") is True

    def test_meta(self) -> None:
        assert _query_looks_conversational("help") is True
        assert _query_looks_conversational("test") is True

    def test_too_short(self) -> None:
        # Under 3 characters after strip.
        assert _query_looks_conversational("") is True
        assert _query_looks_conversational("  ") is True
        assert _query_looks_conversational("a") is True
        assert _query_looks_conversational("ab") is True

    def test_real_research_queries_pass_through(self) -> None:
        assert _query_looks_conversational("tuberculosis") is False
        assert _query_looks_conversational("HIV status") is False
        assert _query_looks_conversational("age at enrollment") is False
        assert _query_looks_conversational("SUBJID") is False
        assert _query_looks_conversational("chest x-ray") is False
        # Three-letter clinical abbreviations are legitimate research terms.
        assert _query_looks_conversational("INH") is False
        assert _query_looks_conversational("AFB") is False
        assert _query_looks_conversational("PCR") is False

    def test_non_string_input_passes(self) -> None:
        # Non-string input is not short-circuited; the underlying tool
        # would fail with its own type error, which is the right layer.
        assert _query_looks_conversational(None) is False  # type: ignore[arg-type]
        assert _query_looks_conversational(123) is False  # type: ignore[arg-type]


class TestSearchVariablesShortCircuit:
    """search_variables refuses conversational inputs."""

    def test_hi_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import search_variables

        # @tool wraps the function; invoke the underlying callable.
        fn: Any = getattr(search_variables, "func", search_variables)
        out = fn("hi")
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE

    def test_hello_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import search_variables

        fn: Any = getattr(search_variables, "func", search_variables)
        out = fn("hello")
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE

    def test_empty_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import search_variables

        fn: Any = getattr(search_variables, "func", search_variables)
        assert fn("") == _CONVERSATIONAL_REFUSAL_MESSAGE
        assert fn("  ") == _CONVERSATIONAL_REFUSAL_MESSAGE


class TestFindVariableCandidatesShortCircuit:
    """find_variable_candidates refuses conversational inputs."""

    def test_hi_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        fn: Any = getattr(find_variable_candidates, "func", find_variable_candidates)
        out = fn("hi", 3)
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE

    def test_thanks_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        fn: Any = getattr(find_variable_candidates, "func", find_variable_candidates)
        out = fn("thanks", 3)
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE


class TestSearchPdfContextShortCircuit:
    """search_pdf_context refuses conversational inputs."""

    def test_hi_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import search_pdf_context

        fn: Any = getattr(search_pdf_context, "func", search_pdf_context)
        out = fn("hi", 5)
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE

    def test_short_returns_refusal_message(self) -> None:
        from scripts.ai_assistant.agent_tools import search_pdf_context

        fn: Any = getattr(search_pdf_context, "func", search_pdf_context)
        out = fn("a", 5)
        assert out == _CONVERSATIONAL_REFUSAL_MESSAGE


class TestRefusalMessageShape:
    """The refusal message itself."""

    def test_refusal_is_user_friendly(self) -> None:
        # Should be actionable — tells the user what to do next.
        msg = _CONVERSATIONAL_REFUSAL_MESSAGE
        assert "greeting" in msg or "question" in msg
        # Should not contain any raw tool-internals or error-looking text.
        assert "Traceback" not in msg
        assert "Error" not in msg
