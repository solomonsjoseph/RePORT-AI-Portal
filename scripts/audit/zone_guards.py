"""Phase 4 audit-zone deny helper.

Two checks (defense in depth):
1. realpath escape check - denies any path that resolves into
   ``output/*/audit/<...>``.
2. .gitattributes audit-attr check - denies any path tagged with
   ``report-ai-portal-no-llm=true`` per repo .gitattributes.

Either signal triggers ``PermissionError``. Both must pass for allow.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import config
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_AUDIT_SEGMENT = "audit"
_OUTPUT_SEGMENT = "output"


class AuditZoneViolation(PermissionError):
    """Raised when a path is rejected for being in the audit zone."""


def _is_inside_audit_zone_by_path(path: Path) -> bool:
    real = Path(str(path)).resolve()
    parts = real.parts
    # Look for ``output/<study>/audit/`` anywhere in the resolved path.
    for i, part in enumerate(parts):
        if part == _OUTPUT_SEGMENT and i + 2 < len(parts) and parts[i + 2] == _AUDIT_SEGMENT:
            return True
    return False


@lru_cache(maxsize=2048)
def _has_no_llm_attribute(path: Path) -> bool:
    """Return True iff `git check-attr` reports the audit attribute set."""

    try:
        result = subprocess.run(
            ["git", "check-attr", config.AUDIT_NO_LLM_ZONE_ATTRIBUTE, str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("zone_guards.attr_check_failed path=%s err=%s", str(path), exc)
        return False
    if result.returncode != 0:
        return False
    # Output: `<path>: <attr>: <value>` - value is `true`, `false`, or `unspecified`.
    line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
    return line.endswith(": true")


def deny_if_audit_zone(path: str | Path) -> None:
    """Raise ``AuditZoneViolation`` if *path* is in the audit zone.

    Two checks; either positive triggers deny.
    """

    p = Path(path)
    if _is_inside_audit_zone_by_path(p):
        raise AuditZoneViolation(f"audit zone read denied (realpath check): {p}")
    if _has_no_llm_attribute(p):
        raise AuditZoneViolation(f"audit zone read denied (gitattributes attr): {p}")
