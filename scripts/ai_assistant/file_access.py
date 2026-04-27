"""Agent-world file-access boundary enforcement.

The production LLM agent's permitted zones are (2026-04-24 boundary design):

* **Read** — ``TRIO_BUNDLE_DIR`` (scrubbed, k-anon-gated trio outputs) **or**
  ``AGENT_STATE_DIR`` (its own analysis outputs, conversations, snapshots).
  A small allowlist admits read-only source-tree config files
  (``config/study_knowledge.yaml``) that tool implementations need.
* **Write** — ``AGENT_STATE_DIR`` only.

Everything else — ``STUDY_AUDIT_DIR`` (incl. telemetry), ``RAW_DATA_DIR``,
``LOGS_DIR``, ``STUDY_STAGING_DIR``, arbitrary filesystem paths — is
hard-rejected with :class:`ZoneViolationError` (a ``PermissionError``
subclass from ``scripts.security.secure_env``).

This module is the chokepoint: every agent-tool file read or write should
call :func:`validate_agent_read` or :func:`validate_agent_write` before
touching disk. The existing ``assert_trio_bundle_zone`` and
``assert_output_zone`` in ``scripts.security.secure_env`` remain valid
narrower checks — this module layers the expanded agent-runtime zone on
top without changing pipeline-side enforcement.
"""

from __future__ import annotations

import os
from pathlib import Path

import config
from scripts.security.secure_env import ZoneViolationError

__all__ = [
    "ZoneViolationError",
    "is_agent_readable",
    "validate_agent_read",
    "validate_agent_write",
    "validate_sandbox_write",
]


def _resolve(p: str | Path) -> str:
    return os.path.realpath(str(p))


def _is_within(path_realpath: str, base_realpath: str) -> bool:
    """Return True when *path_realpath* is the same as or under *base_realpath*.

    Both arguments must already be ``os.path.realpath``-resolved.
    """
    try:
        return os.path.commonpath([path_realpath, base_realpath]) == base_realpath
    except ValueError:
        # ValueError: paths on different drives (Windows) — never the same zone.
        return False


def _zones() -> tuple[list[str], list[str], frozenset[str]]:
    """Recompute permitted zones from current config.

    Called per-validation so that ``conftest.py`` monkeypatches of
    ``config.TRIO_BUNDLE_DIR`` / ``config.AGENT_STATE_DIR`` take effect.
    Cost is trivial (two ``realpath`` calls).
    """
    read_roots = [
        _resolve(config.TRIO_BUNDLE_DIR),
        _resolve(config.AGENT_STATE_DIR),
    ]
    write_roots = [
        _resolve(config.AGENT_STATE_DIR),
    ]
    # Repo-tracked config that StudyKnowledge + similar helpers load at
    # tool-invocation time. This is the "how" surface (per the hard PHI
    # rule), not the "what" — still inside the source tree.
    project_root = Path(__file__).resolve().parents[2]
    read_allowlist = frozenset(
        {
            _resolve(project_root / "config" / "study_knowledge.yaml"),
        }
    )
    return read_roots, write_roots, read_allowlist


def validate_agent_read(path: str | Path) -> Path:
    """Return the resolved :class:`~pathlib.Path` if the agent may read it.

    Raises:
        ZoneViolationError: *path* is outside the agent's permitted read zones.
    """
    read_roots, _, allowlist = _zones()
    resolved = _resolve(path)
    if resolved in allowlist:
        return Path(resolved)
    for root in read_roots:
        if _is_within(resolved, root):
            return Path(resolved)
    raise ZoneViolationError(
        f"Agent read rejected — path is outside the permitted zones "
        f"(trio_bundle/ or agent/): {path}"
    )


def validate_agent_write(path: str | Path) -> Path:
    """Return the resolved :class:`~pathlib.Path` if the agent may write it.

    Raises:
        ZoneViolationError: *path* is outside ``AGENT_STATE_DIR``.
    """
    _, write_roots, _ = _zones()
    resolved = _resolve(path)
    for root in write_roots:
        if _is_within(resolved, root):
            return Path(resolved)
    raise ZoneViolationError(
        f"Agent write rejected — path is outside the agent zone "
        f"(only output/{{STUDY}}/agent/** is writable): {path}"
    )


def is_agent_readable(path: str | Path) -> bool:
    """Non-raising variant of :func:`validate_agent_read` for sentinel checks."""
    try:
        validate_agent_read(path)
    except ZoneViolationError:
        return False
    return True


def validate_sandbox_write(path: str | Path) -> Path:
    """Return the resolved :class:`~pathlib.Path` if the exec_python sandbox
    may write to *path*.

    The sandbox runs LLM-generated code — a strictly narrower threat model
    than tool-code. Writes are scoped to ``AGENT_OUTPUT_DIR`` (``agent/analysis/``)
    rather than the full ``AGENT_STATE_DIR``.

    Uses ``os.path.commonpath`` (via :func:`_is_within`) so that sibling
    prefixes like ``agent/analysis_exfil`` cannot masquerade as ``analysis/``.

    Raises:
        ZoneViolationError: *path* is outside ``AGENT_OUTPUT_DIR``.
    """
    sandbox_root = _resolve(config.AGENT_OUTPUT_DIR)
    resolved = _resolve(path)
    if _is_within(resolved, sandbox_root):
        return Path(resolved)
    raise ZoneViolationError(
        f"Sandbox write denied — exec_python may only write inside "
        f"agent/analysis/: {path}"
    )
