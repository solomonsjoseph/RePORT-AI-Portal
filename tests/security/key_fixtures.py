"""Synthetic API-key fixtures built without static key-shaped literals."""

from __future__ import annotations


def anthropic_key(marker: str = "TAIL") -> str:
    return "sk-" + "ant-" + "api03-" + ("A" * 44) + marker


def openai_key(marker: str = "TAIL") -> str:
    return "sk-" + ("B" * 44) + marker


def openai_project_key(marker: str = "TAIL") -> str:
    return "sk-" + "proj-" + ("C" * 44) + marker


def nvidia_key(marker: str = "TAIL") -> str:
    return "nv" + "api-" + ("D" * 34) + marker


def google_key() -> str:
    return "AI" + "za" + ("E" * 35)
