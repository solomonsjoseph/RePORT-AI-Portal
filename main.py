#!/usr/bin/env python3
"""AI Assistant launcher for the RePORT AI Portal.

After the Wave 6 re-architecture this file is a *thin launcher* for the AI
assistant only:

* ``--chat`` — interactive AI Assistant chat REPL
* ``--web``  — Streamlit web UI for the AI Assistant
* ``--version`` — print the version and exit

The clinical-data *publish* path (Dictionary → Datasets → PHI scrub → cleanup →
``llm_source/``) no longer lives here. It moved verbatim to
:mod:`scripts.pipeline.host_pipeline` and is driven by the
``report-ai-study-pipeline`` plugin orchestrator. Build a study with
``make study STUDY=<name>`` (which delegates to the orchestrator), not with a
flag on this launcher.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys

import config
from __version__ import __version__
from scripts.utils import logging_system as log
from scripts.utils.log_hygiene import install_phi_redactor_best_effort

__all__ = [
    "main",
]

_STREAMLIT_DEFAULT_PORT = 8501
_STREAMLIT_MAX_LOCAL_PORT = 8599


def _streamlit_port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        return False
    return True


def _local_streamlit_port(host: str) -> int:
    for port in range(_STREAMLIT_DEFAULT_PORT, _STREAMLIT_MAX_LOCAL_PORT + 1):
        if _streamlit_port_available(host, port):
            return port
    raise RuntimeError(
        f"No free Streamlit port found in {_STREAMLIT_DEFAULT_PORT}-{_STREAMLIT_MAX_LOCAL_PORT}."
    )


def _streamlit_launch_command() -> list[str]:
    cmd = [sys.executable, "-m", "streamlit", "run", "scripts/ai_assistant/web_ui.py"]
    if config.production_mode_enabled() or os.environ.get("STREAMLIT_SERVER_PORT"):
        return cmd

    host = os.environ.get("STREAMLIT_SERVER_ADDRESS", "127.0.0.1").strip() or "127.0.0.1"
    port = _local_streamlit_port(host)
    cmd.extend(["--server.port", str(port)])
    if port != _STREAMLIT_DEFAULT_PORT:
        log.warning(
            "Streamlit port %s is busy; launching local web UI on %s:%s.",
            _STREAMLIT_DEFAULT_PORT,
            host,
            port,
        )
    return cmd


def main() -> None:
    """Launch the AI Assistant chat REPL or the Streamlit web UI.

    This is a thin launcher. The clinical-data publish path lives in
    :mod:`scripts.pipeline.host_pipeline` and is driven by the
    ``report-ai-study-pipeline`` orchestrator (``make study STUDY=<name>``).

    Command-line interface:
        --chat                 Start the interactive AI Assistant chat REPL
        --web                  Launch the Streamlit web UI
        --provider NAME        LLM provider override
        --model NAME           LLM model override
        -v, --verbose          DEBUG logging
        --version              Show version and exit

    Returns:
        None.
    """
    parser = argparse.ArgumentParser(
        prog="RePORT AI Portal",
        description="AI Assistant launcher for the RePORT AI Portal.",
        epilog="""
Usage:
  %(prog)s --chat                       # Interactive AI Assistant chat (CLI)
  %(prog)s --web                        # Streamlit web UI

To build/publish a study, use the plugin orchestrator:
  make study STUDY=<name>

For detailed documentation, see the Sphinx docs or README.md
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging with detailed context. "
        "Default: Simple mode (INFO level, minimal console output)",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start the interactive AI Assistant chat REPL",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the Streamlit web UI for the AI Assistant",
    )

    # LLM provider / model overrides (apply to chat + web).
    parser.add_argument(
        "--provider",
        type=str,
        metavar="NAME",
        help=(
            "LLM provider: ollama, anthropic, openai, google-genai, "
            "nvidia-ai-endpoints, fake-local (test only)"
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        metavar="NAME",
        help="LLM model name (e.g. qwen3:8b, claude-opus-4-7, gpt-5.5, gemini-3.1-pro-preview)",
    )

    args = parser.parse_args()

    # Apply LLM overrides early — affects both chat and web.
    if getattr(args, "provider", None):
        config.LLM_PROVIDER = args.provider  # type: ignore[attr-defined]
        os.environ["LLM_PROVIDER"] = args.provider
    if getattr(args, "model", None):
        config.LLM_MODEL = args.model  # type: ignore[attr-defined]
        os.environ["LLM_MODEL"] = args.model

    log.setup_logger(
        name=config.LOG_NAME,
        log_level=logging.DEBUG if args.verbose else logging.INFO,
        simple_mode=not args.verbose,
        verbose=args.verbose,
    )
    install_phi_redactor_best_effort()

    if getattr(args, "web", False):
        import subprocess

        log.info("Launching Streamlit web UI…")
        try:
            subprocess.run(_streamlit_launch_command(), check=True)  # noqa: S603
        except KeyboardInterrupt:
            log.info("Streamlit web UI stopped by operator.")
        return

    if getattr(args, "chat", False):
        from scripts.ai_assistant.cli import run_repl

        run_repl()
        return

    # No actionable flag: show help so the user discovers --chat/--web and
    # the `make study` entry point for the publish path.
    parser.print_help()


if __name__ == "__main__":
    main()
