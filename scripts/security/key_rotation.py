"""PHI HMAC key-rotation detection (Wave 3 C1.3).

Rotating the HMAC key changes every pseudonym :func:`scripts.security.phi_scrub.pseudo_id`
produces, so subjects published under the old key no longer link to subjects
published under the new one. That is a legitimate but consequential operator
action (it requires full re-ingestion to restore cross-run linkage), and it must
never happen *silently*. This module records the key fingerprint a study was last
published with and compares it on the next run.

Design notes
------------
* **Pure core.** :func:`detect_rotation` is a side-effect-free comparison so the
  decision logic is trivially unit-testable; :func:`check_and_record` is the
  stateful wrapper that reads the prior record, classifies, and persists the new
  one.
* **First run is never a rotation** (plan risk #4). When no prior fingerprint is
  recorded — every pre-C1 study, and every brand-new study — the result is
  :data:`RotationStatus.FIRST_RUN`, not ``ROTATED``. Otherwise the very first
  run after this feature shipped would false-alarm on every existing study.
* **Value-free.** The state file holds only the SHA-256 fingerprint (already a
  one-way hash of the key, never the key itself) plus run-id provenance. It lives
  in the audit zone (no-LLM) like other IRB evidence.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path

from scripts.utils.logging_system import get_logger

__all__ = [
    "RotationStatus",
    "check_and_record",
    "detect_rotation",
    "key_state_path",
    "read_recorded_fingerprint",
    "record_fingerprint",
]

_logger = get_logger(__name__)

#: Filename of the per-study key-state record under the audit directory.
KEY_STATE_FILENAME = "phi_key_state.json"


class RotationStatus(enum.Enum):
    """Outcome of comparing the current key fingerprint to the recorded one."""

    FIRST_RUN = "first_run"  # no prior fingerprint recorded — not a rotation
    UNCHANGED = "unchanged"  # current == recorded
    ROTATED = "rotated"  # current != recorded — pseudonyms will not link


def detect_rotation(recorded: str | None, current: str) -> RotationStatus:
    """Classify a fingerprint comparison (pure, no I/O).

    Args:
        recorded: the fingerprint a prior run persisted, or ``None`` if none.
        current: the fingerprint of the key the current run is using.
    """
    if recorded is None:
        return RotationStatus.FIRST_RUN
    return RotationStatus.UNCHANGED if recorded == current else RotationStatus.ROTATED


def key_state_path(audit_dir: Path) -> Path:
    """Return the canonical key-state record path under an audit directory."""
    return Path(audit_dir) / KEY_STATE_FILENAME


def read_recorded_fingerprint(audit_dir: Path) -> str | None:
    """Read the previously-recorded key fingerprint, or ``None`` if absent.

    Fail-soft: a missing or unparseable record is treated as "no prior record"
    (→ ``FIRST_RUN``) rather than raising, so a corrupt state file degrades to a
    first-run classification instead of blocking a publish.
    """
    path = key_state_path(audit_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _logger.warning("phi_key_state.json unreadable at %s; treating as first run", path)
        return None
    fp = data.get("fingerprint")
    return fp if isinstance(fp, str) and fp else None


def record_fingerprint(
    audit_dir: Path,
    fingerprint: str,
    *,
    run_id: str | None = None,
    status: RotationStatus,
    prior: dict | None = None,
) -> None:
    """Persist the current key fingerprint + provenance to the state record.

    ``rotation_count`` increments only on an actual ``ROTATED`` transition;
    ``first_seen_run_id`` is preserved across runs so the audit trail shows when
    the *current* key was first adopted.
    """
    path = key_state_path(audit_dir)
    prior = prior or {}
    rotation_count = int(prior.get("rotation_count", 0))
    first_seen = prior.get("first_seen_run_id")
    if status is RotationStatus.ROTATED:
        rotation_count += 1
        first_seen = run_id  # a new key was adopted this run
    elif status is RotationStatus.FIRST_RUN:
        first_seen = run_id
    record = {
        "fingerprint": fingerprint,
        "first_seen_run_id": first_seen,
        "last_seen_run_id": run_id,
        "rotation_count": rotation_count,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_and_record(
    audit_dir: Path,
    current_fingerprint: str,
    *,
    run_id: str | None = None,
) -> RotationStatus:
    """Compare against the recorded fingerprint, log on rotation, and persist.

    Returns the :class:`RotationStatus`. A ``ROTATED`` result is logged at
    WARNING (cross-run pseudonym linkage is now broken); the caller decides
    whether that is fatal — by default the pipeline continues, since the current
    run is internally consistent and aborting would block a deliberate re-key.
    """
    path = key_state_path(audit_dir)
    prior: dict | None = None
    if path.is_file():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = None
    recorded = (prior or {}).get("fingerprint") if prior else None
    recorded = recorded if isinstance(recorded, str) and recorded else None

    status = detect_rotation(recorded, current_fingerprint)
    if status is RotationStatus.ROTATED:
        _logger.warning(
            "PHI HMAC key ROTATED since the last publish of this study "
            "(fingerprint changed). Pseudonyms generated now will NOT link to "
            "previously published data; full re-ingestion is required to restore "
            "cross-run linkage."
        )
    record_fingerprint(
        audit_dir,
        current_fingerprint,
        run_id=run_id,
        status=status,
        prior=prior,
    )
    return status
