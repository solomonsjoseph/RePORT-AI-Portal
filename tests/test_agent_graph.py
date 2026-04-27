"""Tests for scripts.ai_assistant.agent_graph — ReAct agent creation and streaming.

All tests mock the LLM and LangGraph internals. No real API calls are made.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Module import guard
# ═══════════════════════════════════════════════════════════════════════════

langchain = pytest.importorskip("langchain_core", reason="langchain_core required")


from scripts.ai_assistant.agent_graph import (  # noqa: E402
    get_agent,
    get_checkpointer,
    invoke_query,
    reset_agent,
    stream_query,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_singletons() -> Generator[None, None, None]:
    """Ensure each test starts with clean module-level singletons."""
    reset_agent()
    yield
    reset_agent()


# ═══════════════════════════════════════════════════════════════════════════
# get_checkpointer
# ═══════════════════════════════════════════════════════════════════════════


class TestGetCheckpointer:
    def test_returns_memory_saver(self):
        cp = get_checkpointer()
        from langgraph.checkpoint.memory import MemorySaver

        assert isinstance(cp, MemorySaver)

    def test_singleton(self):
        cp1 = get_checkpointer()
        cp2 = get_checkpointer()
        assert cp1 is cp2


# ═══════════════════════════════════════════════════════════════════════════
# get_agent
# ═══════════════════════════════════════════════════════════════════════════


class TestGetAgent:
    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_creates_agent_with_all_tools(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        get_agent()
        assert mock_create.called
        # Verify all tools were passed
        _, kwargs = mock_create.call_args
        assert "tools" in kwargs
        from scripts.ai_assistant.agent_tools import ALL_TOOLS

        assert kwargs["tools"] is ALL_TOOLS

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_agent_is_singleton(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        a1 = get_agent()
        a2 = get_agent()
        assert a1 is a2
        assert mock_create.call_count == 1

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_agent_receives_system_prompt(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        get_agent()
        _, kwargs = mock_create.call_args
        assert "prompt" in kwargs
        assert isinstance(kwargs["prompt"], str)
        assert len(kwargs["prompt"]) > 100  # non-trivial system prompt

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_agent_receives_checkpointer(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        get_agent()
        _, kwargs = mock_create.call_args
        assert "checkpointer" in kwargs
        from langgraph.checkpoint.memory import MemorySaver

        assert isinstance(kwargs["checkpointer"], MemorySaver)

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    def test_llm_init_failure_propagates(self, mock_llm):
        mock_llm.side_effect = RuntimeError("No provider")
        with pytest.raises(RuntimeError, match="No provider"):
            get_agent()


# ═══════════════════════════════════════════════════════════════════════════
# reset_agent
# ═══════════════════════════════════════════════════════════════════════════


class TestResetAgent:
    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_reset_clears_singleton(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.side_effect = [MagicMock(), MagicMock()]
        a1 = get_agent()
        reset_agent()
        a2 = get_agent()
        assert a1 is not a2
        assert mock_create.call_count == 2

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_reset_clears_checkpointer(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        get_agent()
        cp1 = get_checkpointer()
        reset_agent()
        cp2 = get_checkpointer()
        assert cp1 is not cp2


# ═══════════════════════════════════════════════════════════════════════════
# invoke_query
# ═══════════════════════════════════════════════════════════════════════════


class TestInvokeQuery:
    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_returns_ai_message_content(self, mock_create, mock_llm):
        from langchain_core.messages import AIMessage

        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [AIMessage(content="Test answer")]}
        mock_create.return_value = mock_agent
        result = invoke_query("test question")
        assert result == "Test answer"

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_returns_last_ai_message(self, mock_create, mock_llm):
        from langchain_core.messages import AIMessage, HumanMessage

        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(content="first"),
                AIMessage(content="final answer"),
            ]
        }
        mock_create.return_value = mock_agent
        result = invoke_query("test")
        assert result == "final answer"

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_no_ai_message_returns_fallback(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": []}
        mock_create.return_value = mock_agent
        result = invoke_query("test")
        assert result == "(No response generated.)"

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_thread_id_isolation(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [MagicMock(content="ok", spec=["content"])]}
        # Make the mock look like an AIMessage
        from langchain_core.messages import AIMessage

        mock_agent.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        mock_create.return_value = mock_agent

        invoke_query("q1", thread_id="thread-A")
        invoke_query("q2", thread_id="thread-B")

        calls = mock_agent.invoke.call_args_list
        assert len(calls) == 2
        cfg_a = calls[0][1]["config"]["configurable"]["thread_id"]
        cfg_b = calls[1][1]["config"]["configurable"]["thread_id"]
        assert cfg_a == "thread-A"
        assert cfg_b == "thread-B"


# ═══════════════════════════════════════════════════════════════════════════
# stream_query
# ═══════════════════════════════════════════════════════════════════════════


class TestStreamQuery:
    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_returns_iterator(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([{"messages": []}])
        mock_create.return_value = mock_agent
        result = stream_query("test")
        chunks = list(result)
        assert len(chunks) == 1

    @patch("scripts.ai_assistant.agent_graph._init_llm")
    @patch("scripts.ai_assistant.agent_graph.create_react_agent")
    def test_passes_callbacks(self, mock_create, mock_llm):
        mock_llm.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([])
        mock_create.return_value = mock_agent
        cb = MagicMock()
        list(stream_query("test", callbacks=[cb]))
        call_config = mock_agent.stream.call_args[1]["config"]
        assert cb in call_config["callbacks"]


class TestStreamIdleWatchdog:
    """The watchdog must raise TimeoutError when the underlying stream
    produces no chunk for AGENT_STREAM_IDLE_TIMEOUT seconds — this is the
    fix for the E3 benchmark stall."""

    def test_slow_generator_raises_timeout(self):
        import time

        from scripts.ai_assistant.agent_graph import _with_idle_deadline

        def _hang() -> object:
            time.sleep(5)
            yield {"messages": []}

        gen = _with_idle_deadline(_hang(), idle_timeout=1)
        with pytest.raises(TimeoutError, match="stuck in an internal reasoning loop"):
            next(gen)

    def test_fast_generator_passes_through(self):
        from scripts.ai_assistant.agent_graph import _with_idle_deadline

        chunks = [{"messages": [1]}, {"messages": [2]}]
        result = list(_with_idle_deadline(iter(chunks), idle_timeout=5))
        assert result == chunks

    def test_upstream_error_is_re_raised(self):
        from scripts.ai_assistant.agent_graph import _with_idle_deadline

        def _boom():
            yield {"messages": []}
            raise ValueError("upstream bug")

        gen = _with_idle_deadline(_boom(), idle_timeout=5)
        assert next(gen) == {"messages": []}
        with pytest.raises(ValueError, match="upstream bug"):
            next(gen)


# ═══════════════════════════════════════════════════════════════════════════
# Qwen3 downgrade ladder + _init_llm OOM probe
# ═══════════════════════════════════════════════════════════════════════════


class TestDowngradeLadder:
    """``config.preferred_or_installed_downgrade`` is the shape contract the
    ladder walker in ``_init_llm`` depends on. A regression here would silently
    mis-route qwen3 fallbacks."""

    def test_ladder_from_8b_includes_all_rungs(self):
        import config as _config

        assert _config.preferred_or_installed_downgrade("qwen3:8b") == [
            "qwen3:8b",
            "qwen3:4b",
            "qwen3:1.7b",
        ]

    def test_ladder_from_4b_drops_8b(self):
        import config as _config

        assert _config.preferred_or_installed_downgrade("qwen3:4b") == [
            "qwen3:4b",
            "qwen3:1.7b",
        ]

    def test_ladder_from_smallest_is_single_entry(self):
        import config as _config

        assert _config.preferred_or_installed_downgrade("qwen3:1.7b") == ["qwen3:1.7b"]

    def test_non_qwen3_passes_through_unchanged(self):
        import config as _config

        assert _config.preferred_or_installed_downgrade("llama3:70b") == ["llama3:70b"]
        assert _config.preferred_or_installed_downgrade("claude-sonnet-4-6") == [
            "claude-sonnet-4-6",
        ]


class TestInitLlmOomLadder:
    """``_init_llm`` must probe each ladder rung via ``llm.invoke`` and
    downgrade on Ollama's "requires more system memory" refusal. Without
    this walker, a constrained machine sees silent stream failures (the
    original "LLM does not return any response" bug)."""

    @staticmethod
    def _oom(size_g: float = 5.5, avail_g: float = 2.9) -> Exception:
        return RuntimeError(
            f"model requires more system memory ({size_g} GiB) than "
            f"is available ({avail_g} GiB)"
        )

    def test_first_rung_ok_skips_ladder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config as _config
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(_config, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(_config, "LLM_MODEL", "qwen3:8b")
        calls: list[str] = []

        class _LLM:
            def __init__(self, m: str) -> None:
                self.model = m

            def invoke(self, _prompt: str) -> None:
                calls.append(self.model)

        monkeypatch.setattr(ag, "_build_llm", lambda _prov, m: _LLM(m))

        llm = ag._init_llm()
        assert llm.model == "qwen3:8b"
        assert calls == ["qwen3:8b"]
        assert _config.LLM_MODEL == "qwen3:8b"

    def test_oom_downgrades_to_next_rung(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config as _config
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(_config, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(_config, "LLM_MODEL", "qwen3:8b")
        calls: list[str] = []

        class _LLM:
            def __init__(self, m: str) -> None:
                self.model = m

            def invoke(self, _prompt: str) -> None:
                calls.append(self.model)
                if self.model in ("qwen3:8b", "qwen3:4b"):
                    raise TestInitLlmOomLadder._oom()

        monkeypatch.setattr(ag, "_build_llm", lambda _prov, m: _LLM(m))

        llm = ag._init_llm()
        assert llm.model == "qwen3:1.7b"
        assert calls == ["qwen3:8b", "qwen3:4b", "qwen3:1.7b"]
        # Display state is updated so the UI shows the rung actually in use.
        assert _config.LLM_MODEL == "qwen3:1.7b"

    def test_all_rungs_oom_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config as _config
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(_config, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(_config, "LLM_MODEL", "qwen3:8b")

        class _LLM:
            def __init__(self, _m: str) -> None:
                pass

            def invoke(self, _prompt: str) -> None:
                raise TestInitLlmOomLadder._oom()

        monkeypatch.setattr(ag, "_build_llm", lambda _prov, m: _LLM(m))

        with pytest.raises(RuntimeError, match="refused by Ollama due to insufficient memory"):
            ag._init_llm()

    def test_non_oom_error_is_reraised_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config as _config
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(_config, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(_config, "LLM_MODEL", "qwen3:8b")

        class _LLM:
            def __init__(self, _m: str) -> None:
                pass

            def invoke(self, _prompt: str) -> None:
                raise ConnectionError("connection refused by host")

        monkeypatch.setattr(ag, "_build_llm", lambda _prov, m: _LLM(m))

        # Must NOT walk the ladder — connection failure is unrelated to memory.
        with pytest.raises(ConnectionError, match="connection refused"):
            ag._init_llm()

    def test_non_ollama_provider_skips_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import config as _config
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(_config, "LLM_PROVIDER", "anthropic")
        monkeypatch.setattr(_config, "LLM_MODEL", "claude-sonnet-4-6")
        probed: list[str] = []

        class _LLM:
            def __init__(self, m: str) -> None:
                self.model = m

            def invoke(self, _prompt: str) -> None:
                probed.append(self.model)

        monkeypatch.setattr(ag, "_build_llm", lambda _prov, m: _LLM(m))

        llm = ag._init_llm()
        assert llm.model == "claude-sonnet-4-6"
        # Remote providers charge per token — skip the probe.
        assert probed == []
