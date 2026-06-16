"""ReAct agent for RePORT AI Portal AI Assistant.

Uses LangChain's ``create_agent`` (built on LangGraph) with ``MemorySaver``
for session persistence. The agent autonomously decides which tools to call
and how to compose answers.

LLM provider is controlled by ``config.LLM_PROVIDER`` / ``config.LLM_MODEL``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

import config
from scripts.ai_assistant.agent_prompts import (
    SYSTEM_PROMPT,
)
from scripts.ai_assistant.agent_tools import ALL_TOOLS
from scripts.ai_assistant.ollama_config import get_ollama_base_url
from scripts.ai_assistant.phi_safe import redact_phi_in_text
from scripts.ai_assistant.tool_cache import tool_cache
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)

__all__ = [
    "get_agent",
    "get_checkpointer",
    "invoke_query",
    "reset_agent",
    "stream_query",
]


# Truthy env-var tokens (used by the test-only fake-local provider gate).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


# Module-level singletons (lazy-initialised)
_agent: CompiledStateGraph | None = None
_checkpointer: MemorySaver | None = None

# UP7: Session-start PHI rescan gate.
# Cached per-process to run once per agent session (reset clears it).
# WP-E is scoping scan_tree_for_phi so dictionary prose (e.g. "Use 1900-01-01…")
# no longer false-positives — this call relies on that tuning landing first.
_phi_rescan_passed: bool | None = None


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
    from scripts.ai_assistant.keystore import (
        get_keystore,
        provider_slug_for,
    )

    logger.debug("Initialising LLM: provider=%s, model=%s", provider, model)

    slug = provider_slug_for(provider)
    api_key = get_keystore().get(slug) if slug else None

    if provider == "fake-local":
        if os.environ.get("REPORTAL_TEST_FAKE_LLM", "").strip().lower() not in _TRUTHY:
            raise RuntimeError(
                "fake-local provider is test-only. Set REPORTAL_TEST_FAKE_LLM=1 "
                "to enable the no-API-key local test double."
            )
        return _FakeLocalChatModel()

    # NVIDIA AI Endpoints requires langchain_nvidia_ai_endpoints.ChatNVIDIA.
    # init_chat_model does not support the NVIDIA provider directly, so we
    # instantiate ChatNVIDIA explicitly.
    if provider == "nvidia-ai-endpoints":
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "langchain-nvidia-ai-endpoints is not installed. "
                "Run: uv add langchain-nvidia-ai-endpoints"
            ) from exc
        # NOTE: ChatNVIDIA does not expose a ``max_retries`` constructor param
        # (it leaks into model_kwargs and is forwarded to the API), so retry
        # tuning is intentionally omitted here. The OpenAI/Anthropic/Google
        # path below threads config.AGENT_MAX_RETRIES through init_chat_model.
        kwargs: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": config.AGENT_MAX_TOKENS,
            "temperature": 1,
            "top_p": 1,
        }
        if api_key:
            kwargs["api_key"] = api_key
        return ChatNVIDIA(**kwargs)

    from langchain.chat_models import init_chat_model  # type: ignore[import-untyped]

    try:
        kwargs = {
            "model": model,
            "model_provider": provider,
            "max_tokens": config.AGENT_MAX_TOKENS,
            "timeout": config.AGENT_TIMEOUT,
            # Absorb transient 429/5xx via the SDK's exponential backoff
            # (honours Retry-After) instead of erroring on the first throttle.
            "max_retries": config.AGENT_MAX_RETRIES,
        }
        if provider == "ollama":
            kwargs["base_url"] = get_ollama_base_url()
        if api_key:
            kwargs["api_key"] = api_key
        return init_chat_model(**kwargs)
    except Exception as exc:
        # Wrap with context so callers get a clear actionable message.
        raise RuntimeError(
            f"Failed to initialise LLM (provider={provider!r}, model={model!r}): {exc}"
        ) from exc


class _FakeLocalChatModel(BaseChatModel):
    """No-network chat model for integration tests.

    It supports LangChain tool binding and deterministically calls the same
    tools a small LLM would use for the common smoke queries. The provider is
    env-gated in :func:`_build_llm` so it cannot be selected accidentally in
    normal operator runs.
    """

    @property
    def _llm_type(self) -> str:
        return "report-ai-fake-local"

    def bind_tools(
        self, tools: Any, *, tool_choice: Any = None, **kwargs: Any
    ) -> _FakeLocalChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        last_tool = next((msg for msg in reversed(messages) if isinstance(msg, ToolMessage)), None)
        if last_tool is not None:
            content = (
                "Fake local LLM final answer after tool use. "
                f"Tool `{last_tool.name}` returned the study evidence needed for this query."
            )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

        question = _last_human_text(messages)
        lowered = question.lower()
        tool_call: dict[str, Any] | None = None
        if "dataset" in lowered and any(term in lowered for term in ("list", "available", "show")):
            tool_call = {"name": "list_available_datasets", "args": {}, "id": "fake_list_datasets"}
        elif "stat" in lowered or "record" in lowered or "row" in lowered:
            tool_call = {"name": "get_dataset_stats", "args": {}, "id": "fake_dataset_stats"}
        elif question.strip():
            tool_call = {
                "name": "answer_catalog_question",
                "args": {"question": question},
                "id": "fake_answer_catalog_question",
            }

        if tool_call is None:
            message = AIMessage(content="Fake local LLM response: no study tool was needed.")
        else:
            message = AIMessage(content="", tool_calls=[tool_call])
        return ChatResult(generations=[ChatGeneration(message=message)])


def _last_human_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


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
            logger.warning("Ollama OOM on %s: %s — trying next rung in the ladder", rung, exc)

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


def _is_test_context() -> bool:
    """Return True when running inside the test suite.

    Checks config.is_test_context() when available (added by WP-E);
    falls back to detecting pytest in the module registry.
    """
    try:
        return bool(config.is_test_context())  # type: ignore[attr-defined]
    except AttributeError:
        return "pytest" in sys.modules


def _run_phi_rescan() -> None:
    """UP7: Run a session-start PHI residual scan of the live llm_source tree.

    Fail-closed: if the scan finds PHI patterns, raise RuntimeError with a
    PHI-safe message (relative_path + pattern_name only, never the matched
    value). Skipped under test contexts so the suite is not gated on a
    published bundle.

    WP-E is tuning scan_tree_for_phi so dictionary prose ("Use 1900-01-01…")
    no longer false-positives — this call relies on that scoping landing.
    """
    global _phi_rescan_passed
    if _phi_rescan_passed is True:
        return  # already scanned and passed this session

    if _is_test_context():
        logger.debug("UP7: phi rescan skipped (test context)")
        _phi_rescan_passed = True
        return

    from scripts.security.phi_guard_gate import run_phi_guard_gate

    llm_source_dir = config.STUDY_LLM_SOURCE_DIR
    logger.info("UP7: scanning llm_source tree for PHI residuals: %s", llm_source_dir)
    scan_result = run_phi_guard_gate(llm_source_dir)
    if not scan_result.ok:
        raise RuntimeError(
            f"PHI residual detected in llm_source tree — agent start blocked. "
            f"Triggered by: {', '.join(scan_result.triggered_by)}. "
            f"{scan_result.detail}. "
            "Re-run the PHI scrub pipeline and resolve the finding before "
            "starting an agent session. (Matched value deliberately omitted.)"
        )

    _phi_rescan_passed = True
    logger.info("UP7: llm_source PHI rescan passed")


def get_agent() -> CompiledStateGraph:
    """Return the compiled ReAct agent (create on first call).

    Uses single-agent mode with the full tool set.  The agent resolves
    variables through the ``llm_source`` retrieval tools and performs
    statistical analysis through the sandboxed ``run_python_analysis`` tool.

    UP7: Runs a session-start PHI rescan of the live llm_source tree before
    the agent is constructed. Fail-closed: if the scan finds PHI residuals,
    raises RuntimeError and refuses to build the agent. The scan is cached
    for the session lifetime (reset_agent() clears it).
    """
    global _agent
    if _agent is None:
        # UP7: PHI rescan must pass before the agent is handed to any caller.
        _run_phi_rescan()

        llm = _init_llm()
        prompt = SYSTEM_PROMPT.format(study_name=config.STUDY_NAME)

        _agent = create_agent(
            model=llm,
            tools=ALL_TOOLS,
            system_prompt=prompt,
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
    global _agent, _checkpointer, _phi_rescan_passed
    _agent = None
    _checkpointer = None
    _phi_rescan_passed = None  # UP7: force rescan on next session start
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
