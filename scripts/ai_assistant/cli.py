"""Interactive CLI (REPL) for the RePORT AI Portal AI Assistant system.

Commands:
    :quit / :exit -- End the session.
    :reset -- Clear conversation history and start a new thread.
    :thread -- Show current thread ID.
    :model -- Change LLM provider/model interactively.
    :good / :bad -- Rate the last response.
    :debug -- Toggle verbose stream tracing.
"""

from __future__ import annotations

import getpass
import logging
import os
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

import config
from scripts.ai_assistant.agent_graph import reset_agent, stream_query

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM provider / model selection
# ---------------------------------------------------------------------------

_PROVIDER_CHOICES: dict[str, tuple[str, str, str | None, str]] = {
    "1": ("ollama", "Ollama (local)", None, "qwen3:8b"),
    "2": ("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-4-6"),
    "3": ("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4.1"),
    "4": ("google-genai", "Google Gemini", "GOOGLE_API_KEY", "gemini-2.5-flash"),
}


def _select_llm() -> None:
    """Interactive LLM provider/model selection for the CLI."""
    print("\nSelect LLM provider:")
    print(f"  Current: {config.LLM_PROVIDER} / {config.LLM_MODEL}\n")

    from scripts.ai_assistant.keystore import get_keystore, provider_slug_for

    keystore = get_keystore()

    for num, (provider_id, label, env_key, _default_model) in _PROVIDER_CHOICES.items():
        marker = " ←" if provider_id == config.LLM_PROVIDER else ""
        key_status = ""
        if env_key:
            slug = provider_slug_for(provider_id)
            # "Set" means: KeyStore has it OR the user pre-exported it in their
            # shell. We never write to ``os.environ`` ourselves anymore.
            has_key = (slug is not None and keystore.has(slug)) or bool(
                os.environ.get(env_key, "")
            )
            key_status = " (key set)" if has_key else " (key needed)"
        print(f"  {num}. {label}{key_status}{marker}")

    print(f"  s. Skip (keep {config.LLM_PROVIDER}/{config.LLM_MODEL})\n")

    try:
        choice = input("Provider [s]: ").strip().lower() or "s"
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "s":
        return
    if choice not in _PROVIDER_CHOICES:
        print(f"  Invalid choice: {choice}")
        return

    provider_id, label, env_key, default_model = _PROVIDER_CHOICES[choice]

    # API key — stored in the KeyStore (in-process memory), never in os.environ.
    if env_key:
        slug = provider_slug_for(provider_id)
        existing_key = (
            (slug is not None and keystore.get(slug))
            or os.environ.get(env_key, "")
        )
        if existing_key:
            print(f"  {env_key} is already set.")
            try:
                update = input("  Update API key? [n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if update in ("y", "yes"):
                new_key = getpass.getpass(f"  {env_key}: ").strip()
                if new_key and slug is not None:
                    keystore.set(slug, new_key)
        else:
            try:
                new_key = getpass.getpass(f"  {env_key}: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not new_key:
                print(f"  ⚠ No API key provided for {label}. Queries may fail.")
            elif slug is not None:
                keystore.set(slug, new_key)

    # Model
    current_model = config.LLM_MODEL if provider_id == config.LLM_PROVIDER else default_model
    try:
        model = input(f"  Model [{current_model}]: ").strip() or current_model
    except (EOFError, KeyboardInterrupt):
        model = current_model
        print()

    # Apply settings
    config.LLM_PROVIDER = provider_id  # type: ignore[attr-defined]
    config.LLM_MODEL = model  # type: ignore[attr-defined]
    os.environ["LLM_PROVIDER"] = provider_id
    os.environ["LLM_MODEL"] = model

    reset_agent()
    print(f"\n  ✓ Using {label} / {model}\n")


_debug_mode: bool = False


def _print_answer(text: str) -> None:
    """Print the assistant answer with visual formatting."""
    text = _format_analysis_summary(text)
    print(f"\nassistant> {text}\n")


def _format_analysis_summary(text: str) -> str:
    """Add analysis output path note if analytical results detected."""
    if "<RPLN_FIGURE:" in text or "## Univariate" in text or "## Multivariate" in text:
        analysis_dir = config.STUDY_OUTPUT_DIR / "analysis"
        text += f"\n\n💾 Full results saved to: {analysis_dir}"
    return text


def _handle_command(
    cmd: str,
    *,
    thread_id: str,
) -> tuple[str, bool]:
    """Handle REPL commands. Returns (thread_id, should_continue)."""
    global _debug_mode

    if cmd in (":quit", ":exit"):
        print("Goodbye!")
        return thread_id, False

    if cmd == ":reset":
        new_thread = str(uuid.uuid4())
        reset_agent()
        print(f"Conversation reset. New thread: {new_thread[:8]}…")
        return new_thread, True

    if cmd == ":thread":
        print(f"Thread: {thread_id}")
        return thread_id, True

    if cmd == ":model":
        _select_llm()
        return thread_id, True

    if cmd in (":debug on", ":debug"):
        _debug_mode = True
        print("Debug mode ON — showing tool call stream.")
        return thread_id, True

    if cmd == ":debug off":
        _debug_mode = False
        print("Debug mode OFF.")
        return thread_id, True

    if cmd == ":good":
        logger.info("Positive feedback for thread %s", thread_id)
        print("Thanks for the feedback!")
        return thread_id, True

    if cmd == ":bad":
        logger.info("Negative feedback for thread %s", thread_id)
        print("Sorry about that. Feedback recorded.")
        return thread_id, True

    print(f"Unknown command: {cmd}")
    print("Available: :quit, :exit, :reset, :thread, :model, :debug on|off, :good, :bad")
    return thread_id, True


def run_repl() -> None:
    """Start the interactive REPL loop."""
    # Interactive LLM selection at startup
    _select_llm()

    print("\nRePORT AI Portal — study loaded")
    print(f"Model: {config.LLM_PROVIDER} / {config.LLM_MODEL}")
    print("Commands: :quit, :reset, :thread, :model, :debug on|off, :good, :bad\n")

    thread_id = str(uuid.uuid4())
    short_id = thread_id[:7]

    # Optional telemetry
    callbacks: list[Any] = []
    try:
        from scripts.utils.telemetry import TelemetryLogger

        callbacks.append(TelemetryLogger())
        logger.debug("Telemetry logger attached")
    except ImportError:
        logger.debug("Telemetry not available")

    while True:
        try:
            user_input = input(f"[{short_id}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.startswith(":"):
            thread_id, should_continue = _handle_command(
                user_input,
                thread_id=thread_id,
            )
            short_id = thread_id[:7]
            if not should_continue:
                break
            continue

        from scripts.ai_assistant.phi_safe import guard_user_prompt

        guard = guard_user_prompt(user_input)
        if not guard.ok:
            print(guard.refusal_message or "Prompt refused (PHI detected).")
            continue

        # Stream through the ReAct agent
        try:
            answer = ""
            tools_called: list[str] = []

            for state_update in stream_query(
                user_input,
                thread_id=thread_id,
                callbacks=callbacks if callbacks else None,
            ):
                for node_output in state_update.values():
                    if not isinstance(node_output, dict):
                        continue
                    messages: list[BaseMessage] = node_output.get("messages", [])
                    for msg in messages:
                        # Track tool calls for debug display
                        if isinstance(msg, ToolMessage):
                            name = getattr(msg, "name", "tool")
                            tools_called.append(name)
                            if _debug_mode:
                                print(f"  [tool] {name} → {str(msg.content)[:120]}…")
                        # Only accept the FINAL AIMessage (no pending tool_calls)
                        elif isinstance(msg, AIMessage):
                            has_tool_calls = bool(getattr(msg, "tool_calls", []))
                            if _debug_mode:
                                print(
                                    f"  [agent] tool_calls={has_tool_calls} "
                                    f"content={str(msg.content)[:80]!r}"
                                )
                            if not has_tool_calls and msg.content:
                                # This is the synthesized final answer
                                content = msg.content
                                if isinstance(content, list):
                                    # Anthropic returns list of content blocks
                                    answer = " ".join(
                                        b.get("text", "")
                                        for b in content
                                        if isinstance(b, dict) and b.get("type") == "text"
                                    ).strip()
                                else:
                                    answer = str(content)

            if tools_called and not _debug_mode:
                print(f"  🔍 Tools: {', '.join(tools_called)}")

            if answer:
                _print_answer(answer)
            else:
                print("\n(No answer generated. Try rephrasing your question.)\n")

        except KeyboardInterrupt:
            print("\n(Interrupted)")
        except Exception as exc:
            logger.exception("Agent error")
            err = str(exc)
            _e = err.lower()
            if "connection" in _e or "refused" in _e or "connecterror" in _e:
                print(
                    f"\nError: Cannot reach the LLM server "
                    f"(provider={config.LLM_PROVIDER!r}, model={config.LLM_MODEL!r}).\n"
                    "  — If using Ollama, make sure it is running:  ollama serve\n"
                    f"  — Then pull the model if needed:           ollama pull {config.LLM_MODEL}\n"
                )
            elif "api key" in _e or "authentication" in _e or "401" in err or "403" in err:
                print(
                    f"\nError: Authentication failed for provider={config.LLM_PROVIDER!r}.\n"
                    "  Set your API key environment variable and retry.\n"
                )
            elif "model not found" in _e or "no such model" in _e or "404" in err:
                print(
                    f"\nError: Model {config.LLM_MODEL!r} not found on "
                    f"provider={config.LLM_PROVIDER!r}.\n"
                    f"  — Ollama: ollama pull {config.LLM_MODEL}\n"
                    "  — Or check the model name is correct.\n"
                )
            elif "rate limit" in _e or "429" in err or "quota" in _e:
                print("\nError: Rate limit hit. Wait a moment and try again.\n")
            else:
                print(f"\nError: {exc}\n")


def main() -> None:
    """Entry point for the CLI."""
    from scripts.security.phi_scrub import (
        PHIKeyMissingError,
        PHIKeyPermissionError,
        PHIScrubError,
    )
    from scripts.security.phi_scrub import load_key as _load_phi_key
    from scripts.utils.log_hygiene import install_phi_redactor
    from scripts.utils.logging_system import setup_logging

    setup_logging()

    # Best-effort install of the PHI log redactor. Silently no-op when the
    # sidecar key is absent so fresh checkouts can still start a REPL;
    # operators will see the fallback warning and know to bootstrap.
    try:
        install_phi_redactor(hmac_key=_load_phi_key())
    except (PHIKeyMissingError, PHIKeyPermissionError, PHIScrubError) as exc:
        logger.warning(
            "PHI log redactor NOT installed (%s). Run "
            "`python -m scripts.security.phi_scrub bootstrap-key` "
            "to enable log redaction.",
            type(exc).__name__,
        )

    run_repl()


if __name__ == "__main__":
    main()
