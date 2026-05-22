"""AI Assistant subsystem for RePORT AI Portal.

Provides a ReAct agent with structured tools for clinical research question
answering, session-persistent memory, telemetry, and interactive CLI.
"""

from __future__ import annotations

from .agent_graph import invoke_query, stream_query

__all__ = [
    "invoke_query",
    "stream_query",
]
