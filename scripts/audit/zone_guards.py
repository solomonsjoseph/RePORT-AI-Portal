"""Phase 4 audit-zone deny helper (+ W1 snapshot-root deny).

Audit-zone deny — two checks (defense in depth):
1. realpath escape check - denies any path that resolves into
   ``output/*/audit/<...>``.
2. .gitattributes audit-attr check - denies any path tagged with
   ``report-ai-portal-no-llm=true`` per repo .gitattributes.

Either signal triggers ``PermissionError``. Both must pass for allow.

Snapshot-root deny (W1) — :func:`deny_if_snapshot_root` denies any path under
``output/*/snapshots/<id>/`` that is NOT inside that snapshot's
``<id>/llm_source/`` subtree. The snapshot root holds the run's approval,
verifier report, and manifest (all off-limits to the LLM); only a selected
``<id>/llm_source/`` subtree may be exposed.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import config
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_AUDIT_SEGMENT = "audit"
_OUTPUT_SEGMENT = "output"
_SNAPSHOTS_SEGMENT = "snapshots"
_LLM_SOURCE_SEGMENT = "llm_source"


class AuditZoneViolation(PermissionError):  # noqa: N818
    """Raised when a path is rejected for being in the audit zone."""


class SnapshotZoneViolation(PermissionError):  # noqa: N818
    """Raised when a path is rejected for being in the snapshot root zone.

    The snapshot ROOT (``output/<study>/snapshots/<id>/``) holds the run's
    approval report, verifier report, and manifest — all OFF-LIMITS to the LLM.
    Only the ``<id>/llm_source/`` subtree may ever be exposed, and only when a
    maintainer explicitly selects that snapshot (which repoints the agent read
    root). This guard is defence-in-depth: it denies the snapshot root and all
    non-``llm_source`` children regardless of how read roots are configured.
    """


def _is_inside_audit_zone_by_path(path: Path) -> bool:
    real = Path(str(path)).resolve()
    parts = real.parts
    # Look for ``output/<study>/audit/`` anywhere in the resolved path.
    for i, part in enumerate(parts):
        if part == _OUTPUT_SEGMENT and i + 2 < len(parts) and parts[i + 2] == _AUDIT_SEGMENT:
            return True
    return False


def _is_denied_snapshot_root_path(path: Path) -> bool:
    """True iff *path* is under ``output/<study>/snapshots/<id>/`` but NOT under
    that snapshot's ``<id>/llm_source/`` subtree.

    The ``<id>/llm_source/`` subtree is the ONLY LLM-readable part of a snapshot
    (and only once selected). Everything else under the snapshot root — the root
    itself, the approval/verifier/manifest JSON — is denied.
    """
    real = Path(str(path)).resolve()
    parts = real.parts
    for i, part in enumerate(parts):
        # Match ``output/<study>/snapshots/<id>/...`` — snapshots is 2 after output.
        if part == _OUTPUT_SEGMENT and i + 2 < len(parts) and parts[i + 2] == _SNAPSHOTS_SEGMENT:
            snapshots_idx = i + 2
            # Need at least a snapshot id segment after ``snapshots``.
            if snapshots_idx + 1 >= len(parts):
                # ``.../snapshots`` itself — deny.
                return True
            # Segment immediately after ``<id>`` must be ``llm_source`` to be exempt.
            llm_source_idx = snapshots_idx + 2
            return not (
                llm_source_idx < len(parts) and parts[llm_source_idx] == _LLM_SOURCE_SEGMENT
            )
    return False


@lru_cache(maxsize=2048)
def _has_no_llm_attribute(path: Path) -> bool:
    """Return True iff `git check-attr` reports the audit attribute set."""

    git_bin = shutil.which("git")
    if git_bin is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [git_bin, "check-attr", config.AUDIT_NO_LLM_ZONE_ATTRIBUTE, str(path)],
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


def deny_if_snapshot_root(path: str | Path) -> None:
    """Raise ``SnapshotZoneViolation`` if *path* is in the protected snapshot zone.

    Denies the snapshot root and every non-``llm_source`` child under
    ``output/<study>/snapshots/<id>/`` (the approval/verifier/manifest JSON and
    the ``.NO_LLM_ZONE`` sentinel). The ``<id>/llm_source/`` subtree is exempt
    here; its readability is still gated by the agent read-root containment
    check (only reachable when the snapshot has been explicitly selected).
    """
    p = Path(path)
    if _is_denied_snapshot_root_path(p):
        raise SnapshotZoneViolation(f"snapshot root read denied (realpath check): {p}")
