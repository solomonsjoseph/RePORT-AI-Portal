"""Coverage test: every @tool in agent_tools.py is wrapped by @phi_safe_return.

**What.** Inspects the source of ``scripts.ai_assistant.agent_tools`` and
fails if any ``@tool``-decorated function is not immediately followed by
``@phi_safe_return``, or if the count of the two decorators drifts.

**Why.** The PHI gate at the agent boundary is enforced by the
``@phi_safe_return`` decorator chain. If a future contributor adds a
new ``@tool`` without the gate, the tool response would reach the LLM
unchecked — bypassing the Pillar 2.4 claim in the IRB conformance
matrix. A source-level assertion is the simplest CI check that catches
this regression.

**How.** Reads the source of :mod:`scripts.ai_assistant.agent_tools`,
counts ``@tool`` occurrences, counts ``@phi_safe_return`` occurrences,
asserts they are equal and non-zero, and confirms each ``@tool`` line
is immediately followed by ``@phi_safe_return``.
"""

from __future__ import annotations

import re
from pathlib import Path

AGENT_TOOLS_PATH = Path(__file__).parent.parent / "scripts" / "ai_assistant" / "agent_tools.py"


def test_agent_tools_module_exists() -> None:
    assert AGENT_TOOLS_PATH.is_file(), (
        f"agent_tools.py not found at expected path {AGENT_TOOLS_PATH}"
    )


def test_every_tool_decorator_is_followed_by_phi_safe_return() -> None:
    source = AGENT_TOOLS_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()

    tool_line_indices = [i for i, line in enumerate(lines) if line.strip() == "@tool"]
    assert tool_line_indices, "Expected at least one @tool decorator in agent_tools.py"

    # Each @tool must be followed (on the next line) by @phi_safe_return.
    missing: list[int] = []
    for idx in tool_line_indices:
        next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        if next_line != "@phi_safe_return":
            missing.append(idx + 1)  # human-friendly line number

    assert not missing, (
        f"@tool at lines {missing} is not immediately followed by "
        f"@phi_safe_return; add the decorator so the tool return passes "
        f"through the PHI gate."
    )


def test_tool_and_phi_safe_return_counts_match() -> None:
    source = AGENT_TOOLS_PATH.read_text(encoding="utf-8")
    tool_count = len(re.findall(r"^@tool\s*$", source, flags=re.MULTILINE))
    gate_count = len(re.findall(r"^@phi_safe_return\s*$", source, flags=re.MULTILINE))

    assert tool_count == gate_count, (
        f"@tool count ({tool_count}) does not match @phi_safe_return count "
        f"({gate_count}); every tool must be wrapped by the gate."
    )
    assert tool_count > 0, "agent_tools.py defines no @tool-decorated functions"


def test_phi_safe_return_is_imported() -> None:
    source = AGENT_TOOLS_PATH.read_text(encoding="utf-8")
    # Accept both single-line and parenthesised multi-line import forms.
    single_line = "from scripts.ai_assistant.phi_safe import phi_safe_return" in source
    multi_line = bool(
        re.search(
            r"from scripts\.ai_assistant\.phi_safe import \([^)]*\bphi_safe_return\b[^)]*\)",
            source,
            flags=re.DOTALL,
        )
    )
    assert single_line or multi_line, (
        "phi_safe_return must be imported at the top of agent_tools.py "
        "(either directly or as part of a multi-line import)"
    )
