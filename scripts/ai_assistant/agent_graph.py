"""ReAct agent for RePORT AI Portal AI Assistant.

Uses ``create_react_agent`` from LangGraph prebuilt with ``MemorySaver``
for session persistence.  The agent autonomously decides which tools to
call and how to compose answers.

LLM provider is controlled by ``config.LLM_PROVIDER`` / ``config.LLM_MODEL``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

import config
from scripts.ai_assistant.agent_prompts import SYSTEM_PROMPT
from scripts.ai_assistant.agent_tools import ALL_TOOLS
from scripts.ai_assistant.phi_safe import redact_phi_in_text
from scripts.ai_assistant.tool_cache import tool_cache

logger = logging.getLogger(__name__)

__all__ = [
    "get_agent",
    "get_checkpointer",
    "invoke_query",
    "reset_agent",
    "stream_query",
]

# Module-level singletons (lazy-initialised)
_agent: CompiledStateGraph | None = None
_checkpointer: MemorySaver | None = None


# Ollama OOM signals. Substring match on ``str(exc).lower()`` — see
# langchain_ollama/_client.py where ``ollama._types.ResponseError`` wraps the
# 500 body verbatim.
_OLLAMA_OOM_SIGNALS: tuple[str, ...] = (
    "requires more system memory",
    "out of memory",
    "insufficient memory",
)


def _build_llm(provider: str, model: str) -> Any:
    """Construct (but don't probe) a chat model for ``(provider, model)``.

    Factored out of :func:`_init_llm` so the ladder walker can re-construct
    the client with different model names without duplicating the NVIDIA
    / init_chat_model fork.

    The API key is passed as an explicit ``api_key=`` kwarg from the
    KeyStore — the SDK auto-pickup from ``os.environ`` is no longer
    relied on, because PR #3 keeps keys out of the parent's env.
    """
    from langchain.chat_models import init_chat_model  # type: ignore[import-untyped]

    from scripts.ai_assistant.keystore import (
        get_keystore,
        provider_slug_for,
    )

    logger.debug("Initialising LLM: provider=%s, model=%s", provider, model)

    slug = provider_slug_for(provider)
    api_key = get_keystore().get(slug) if slug else None

    # NVIDIA AI Endpoints requires langchain_nvidia_ai_endpoints.ChatNVIDIA.
    # init_chat_model does not support the NVIDIA provider directly and the
    # NVIDIA API uses max_tokens (not max_completion_tokens) in the LangChain
    # integration, so we instantiate ChatNVIDIA explicitly.
    if provider == "nvidia-ai-endpoints":
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "langchain-nvidia-ai-endpoints is not installed. "
                "Run: uv add langchain-nvidia-ai-endpoints"
            ) from exc
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": config.AGENT_MAX_TOKENS,
            "temperature": 1,
            "top_p": 1,
        }
        if api_key:
            kwargs["api_key"] = api_key
        return ChatNVIDIA(**kwargs)

    try:
        kwargs = {
            "model": model,
            "model_provider": provider,
            "max_tokens": config.AGENT_MAX_TOKENS,
            "timeout": config.AGENT_TIMEOUT,
        }
        if api_key:
            kwargs["api_key"] = api_key
        return init_chat_model(**kwargs)
    except Exception as exc:
        # Wrap with context so callers get a clear actionable message.
        raise RuntimeError(
            f"Failed to initialise LLM (provider={provider!r}, model={model!r}): {exc}"
        ) from exc


def _init_llm() -> Any:
    """Initialise the chat model from config.LLM_PROVIDER / LLM_MODEL.

    For the ``ollama`` provider on a qwen3 model, we walk
    :func:`config.preferred_or_installed_downgrade` and probe each rung with
    a one-token ``invoke("ok")``. LangChain's ChatOllama does not trigger an
    Ollama model-load during construction — OOM only surfaces on the first
    real request — so we issue a tiny probe to catch it here, before the
    agent is bound to a model Ollama cannot serve.

    On probe OOM: log a warning, move to the next rung, retry.
    On probe success: if we stepped down, update ``config.LLM_MODEL`` so the
    wizard / error cards / telemetry show the rung we actually resolved to.
    """
    provider = config.LLM_PROVIDER
    model = config.LLM_MODEL

    if not provider:
        logger.error("LLM_PROVIDER is not set for model='%s'", model)
        raise RuntimeError(
            f"LLM provider is not configured for model='{model}'. "
            "The provider should have been auto-detected — this is a bug. "
            "Set the LLM_PROVIDER environment variable to fix it manually "
            "(e.g. export LLM_PROVIDER=ollama)."
        )

    # Only Ollama emits the "requires more system memory" error — remote
    # providers (Anthropic, OpenAI, Gemini, NVIDIA) don't have host-side
    # memory pressure from the caller's perspective.
    if provider != "ollama":
        return _build_llm(provider, model)

    ladder = config.preferred_or_installed_downgrade(model)
    last_exc: Exception | None = None
    for rung in ladder:
        try:
            llm = _build_llm(provider, rung)
            # Probe: triggers Ollama's model-load without committing to a
            # long generation. Ollama refuses to serve if the weights can't
            # fit in available RAM, and the refusal comes back as a 500 on
            # this call. Successful probes leave the model warm for the
            # first real query.
            llm.invoke("ok")
            if rung != model:
                logger.warning(
                    "Ollama refused %s due to memory pressure; downgraded to %s",
                    model,
                    rung,
                )
                config.LLM_MODEL = rung  # type: ignore[misc]
            return llm
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if not any(sig in err for sig in _OLLAMA_OOM_SIGNALS):
                raise  # Not an OOM error — surface to the caller unchanged.
            logger.warning(
                "Ollama OOM on %s: %s — trying next rung in the ladder", rung, exc
            )

    raise RuntimeError(
        f"All {len(ladder)} qwen3 ladder rungs ({', '.join(ladder)}) were refused "
        f"by Ollama due to insufficient memory. Close some apps to free RAM, "
        f"or set LLM_MODEL to a smaller model manually. Last error: {last_exc}"
    ) from last_exc


def get_checkpointer() -> MemorySaver:
    """Return the module-level MemorySaver (create on first call)."""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


def get_agent() -> CompiledStateGraph:
    """Return the compiled ReAct agent (create on first call).

    Uses single-agent mode with the full tool set.  The deterministic
    ``run_study_analysis`` tool handles multi-step analytical pipelines
    internally, so even small models only need to make one tool call.
    """
    global _agent
    if _agent is None:
        llm = _init_llm()
        prompt = SYSTEM_PROMPT.format(study_name=config.STUDY_NAME)

        _agent = create_react_agent(
            model=llm,
            tools=ALL_TOOLS,
            prompt=prompt,
            checkpointer=get_checkpointer(),
        )

        logger.info(
            "Agent initialised (provider=%s, model=%s, tools=%d)",
            config.LLM_PROVIDER,
            config.LLM_MODEL,
            len(ALL_TOOLS),
        )
    return _agent


def reset_agent() -> None:
    """Reset the agent and checkpointer (clears all sessions + tool cache)."""
    global _agent, _checkpointer
    _agent = None
    _checkpointer = None
    tool_cache.clear()
    logger.info("Agent and checkpointer reset")


_STREAM_SENTINEL: object = object()


@dataclass
class _StreamError:
    exc: BaseException


def _with_idle_deadline(
    source: Iterator[dict[str, Any]],
    idle_timeout: int,
) -> Iterator[dict[str, Any]]:
    """Re-yield stream chunks; raise ``TimeoutError`` after ``idle_timeout``
    seconds without a chunk.

    ``agent.stream()`` is a blocking generator that offers no poll API, so we
    drain it in a daemon thread through a queue. The idle deadline measures
    inter-chunk gap, not total wall clock — slow-but-steady streams (a
    long-running tool call that still emits step updates) stay alive, but a
    genuine stall in Sonnet's routing layer (the E3 benchmark case) is
    caught and surfaced as a user-visible error instead of silently waiting
    forever.
    """
    import queue
    import threading

    q: queue.Queue[Any] = queue.Queue()

    def _pump() -> None:
        try:
            for chunk in source:
                q.put(chunk)
        except BaseException as exc:
            q.put(_StreamError(exc))
        finally:
            q.put(_STREAM_SENTINEL)

    threading.Thread(target=_pump, daemon=True).start()

    while True:
        try:
            item = q.get(timeout=idle_timeout)
        except queue.Empty as empty:
            raise TimeoutError(
                f"Agent produced no output for {idle_timeout}s — the model "
                "appears stuck in an internal reasoning loop. Retry your "
                "question; if it keeps happening, try a different model."
            ) from empty
        if item is _STREAM_SENTINEL:
            return
        if isinstance(item, _StreamError):
            raise item.exc
        yield item


def _build_runnable_config(
    thread_id: str,
    callbacks: list[Any] | None,
) -> RunnableConfig:
    cfg = RunnableConfig(
        configurable={"thread_id": thread_id},
        recursion_limit=200,  # cap tool call loops to prevent runaway costs
    )
    if callbacks:
        cfg["callbacks"] = callbacks
    return cfg


def stream_query(
    query: str,
    *,
    thread_id: str = "default",
    callbacks: list[Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a query through the ReAct agent.

    Args:
        query: User question.
        thread_id: Conversation thread ID for session persistence.
        callbacks: LangChain callbacks (e.g. TelemetryLogger).

    Note:
        ``query`` must be pre-screened by
        :func:`scripts.ai_assistant.phi_safe.guard_user_prompt` before calling
        this function. Callers that bypass the guard risk sending raw PHI to the
        LLM.

    Yields:
        State updates from the agent (contains ``messages`` with the response).
    """
    agent = get_agent()
    runnable_config = _build_runnable_config(thread_id, callbacks)
    input_msg = {"messages": [HumanMessage(content=query)]}
    logger.info("Agent query [thread=%s]: %.80s", thread_id, redact_phi_in_text(query))
    raw_stream = cast(
        Iterator[dict[str, Any]],
        agent.stream(input_msg, config=runnable_config),
    )
    return _with_idle_deadline(raw_stream, config.AGENT_STREAM_IDLE_TIMEOUT)


def invoke_query(
    query: str,
    *,
    thread_id: str = "default",
    callbacks: list[Any] | None = None,
) -> str:
    """Invoke the agent and return the final answer text.

    Convenience wrapper over :func:`stream_query` that collects the full
    response.

    Args:
        query: User question.
        thread_id: Conversation thread ID for session persistence.
        callbacks: LangChain callbacks (e.g. TelemetryLogger).

    Note:
        ``query`` must be pre-screened by
        :func:`scripts.ai_assistant.phi_safe.guard_user_prompt` before calling
        this function. Callers that bypass the guard risk sending raw PHI to the
        LLM.

    Returns:
        The agent's final answer as a string.
    """
    agent = get_agent()
    runnable_config = _build_runnable_config(thread_id, callbacks)
    input_msg = {"messages": [HumanMessage(content=query)]}
    logger.info("Agent invoke [thread=%s]: %.80s", thread_id, redact_phi_in_text(query))
    result = agent.invoke(input_msg, config=runnable_config)
    messages: list[BaseMessage] = result.get("messages", [])

    # Extract the last AI message content
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return "(No response generated.)"
