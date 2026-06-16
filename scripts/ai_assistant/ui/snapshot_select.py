"""Snapshot selection for the Load Study UI (W2).

A maintainer who has resolved every held set may select a previously written,
immutable *study snapshot* (see :mod:`scripts.utils.snapshot`) instead of the
live pipeline output. Selecting a snapshot repoints the assistant read zone at
the snapshot's PHI-scrubbed ``llm_source/`` subtree — and ONLY that subtree.

SECURITY CONTRACT
-----------------
* Only ``snapshots/{id}/llm_source/`` is ever exposed. The snapshot ROOT and the
  sibling ``phi_handling_approval.json`` / ``verifier_report.json`` /
  ``snapshot_manifest.json`` are NEVER added to the read zone — they live a
  parent level above the selected subtree and stay denied by
  ``deny_if_snapshot_root`` + read-root containment.
* Fail-closed: selection RE-RUNS the PHI-residual leak gate against the chosen
  ``llm_source/`` subtree before repointing the read zone. A residual match
  refuses the selection (the read zone is left untouched).
* An unknown / malformed snapshot id is rejected before anything is exposed.
* This module NEVER triggers a scrub / retry / resume. It only reads metadata
  and repoints the llm_source path constants at an already-published,
  already-scrubbed subtree.

PROCESS-GLOBAL ACTIVATION (single-active-snapshot-per-process)
-------------------------------------------------------------
:func:`activate_snapshot` mutates PROCESS-GLOBAL ``config.*`` state via
:func:`config.repoint_llm_source_base`. There is exactly ONE active snapshot
per Python process. Streamlit shares a single process across browser sessions,
so concurrent sessions selecting different snapshots RACE (last writer wins) —
every session in the process then reads the most-recently-activated snapshot's
``llm_source/``. This module assumes a single-session / single-active-snapshot
deployment. A session-scoped config (per-session read zone) would remove the
race but is a larger architectural change and is intentionally out of scope
here; the limitation is documented rather than worked around.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config
from scripts.security.phi_guard_gate import run_phi_guard_gate
from scripts.utils import snapshot

__all__ = [
    "SnapshotActivationError",
    "activate_snapshot",
    "available_snapshots",
]


class SnapshotActivationError(Exception):
    """Raised when a snapshot cannot be activated (unknown id or failed gate).

    Fail-closed: the assistant read zone is left untouched when this is raised.
    """


def available_snapshots(study: str | None = None) -> list[dict[str, Any]]:
    """Return advisory metadata for every written snapshot of *study*.

    Each entry is a dict the UI can render in a selector:

    * ``id`` — snapshot id (``snap_<hash>``)
    * ``source_run_id`` — the run that produced the snapshot
    * ``approved_count`` / ``held_count`` — form counts from the manifest
    * ``verifier_passed`` — whether the source run passed the verifier

    Reads ``snapshot_manifest.json`` metadata only (form NAMES / counts — never
    row values). A snapshot whose manifest is unreadable is skipped rather than
    raising, so one corrupt snapshot never hides the others from the selector.
    """
    if study is None:
        study = getattr(config, "STUDY_NAME", "") or ""
    if not study:
        return []

    entries: list[dict[str, Any]] = []
    for snapshot_id in snapshot.list_snapshots(study):
        try:
            manifest = snapshot.load_snapshot(study, snapshot_id)
        except snapshot.SnapshotError:
            # A corrupt/unreadable snapshot is omitted, never fatal.
            continue
        approved = manifest.get("approved_forms", [])
        held = manifest.get("held_forms", [])
        entries.append(
            {
                "id": snapshot_id,
                "source_run_id": str(manifest.get("source_run_id", "")),
                "approved_count": len(approved) if isinstance(approved, list) else 0,
                "held_count": len(held) if isinstance(held, list) else 0,
                "verifier_passed": bool(manifest.get("verifier_passed", False)),
            }
        )
    return entries


def activate_snapshot(study: str | None, snapshot_id: str) -> Path:
    """Expose a snapshot's ``llm_source/`` to the assistant — fail-closed.

    Steps (all must pass before the read zone moves):

    1. Resolve + validate the snapshot and its ``llm_source/`` subtree via
       :func:`snapshot.select_snapshot_llm_source` (rejects an unknown id and
       any path-bearing id before touching disk).
    2. RE-RUN the PHI-residual leak gate (:func:`scan_tree_for_phi`) against that
       subtree. A residual match refuses activation — the read zone is untouched.
    3. Only then repoint ``STUDY_LLM_SOURCE_DIR`` AND every llm_source-derived
       constant at ``snapshots/{id}/llm_source/`` via
       :func:`config.repoint_llm_source_base`. Repointing the base alone would
       leave dataset-query / SoT-citation tools reading the LIVE output tree, so
       the rebase MUST be atomic across all derived constants. ``file_access``
       reads ``STUDY_LLM_SOURCE_DIR`` at call time, so the new read root takes
       effect immediately and the snapshot ROOT / approval / manifest stay
       OUTSIDE it.

    PROCESS-GLOBAL: this mutates shared ``config.*`` state — see the module
    docstring. There is one active snapshot per process; concurrent Streamlit
    sessions race (last writer wins).

    Returns the absolute ``llm_source/`` path now exposed.

    Raises:
        SnapshotActivationError: unknown id, missing subtree, or a PHI residual
            was found by the gate. The read zone is left untouched in every case.
    """
    if study is None:
        study = getattr(config, "STUDY_NAME", "") or ""
    if not study:
        raise SnapshotActivationError("study must not be empty")
    if not snapshot_id:
        raise SnapshotActivationError("snapshot_id must not be empty")

    # 1. Resolve + fail-closed validate (rejects unknown / path-bearing ids).
    try:
        llm_source = snapshot.select_snapshot_llm_source(study, snapshot_id)
    except snapshot.SnapshotError as exc:
        raise SnapshotActivationError(f"cannot activate snapshot {snapshot_id!r}: {exc}") from exc

    # 2. Re-gate the selected subtree for PHI residuals BEFORE exposing it
    #    (OR-combined Presidio + legacy scanner — fails if either finds PHI).
    result = run_phi_guard_gate(llm_source)
    if not result.ok:
        raise SnapshotActivationError(
            f"snapshot {snapshot_id!r} failed the PHI residual gate; refusing to "
            f"expose it: {result.detail}"
        )

    # 3. Atomically repoint the assistant read zone AND every llm_source-derived
    #    constant at the snapshot's llm_source. A single setattr on
    #    STUDY_LLM_SOURCE_DIR would leave dataset-query / SoT-citation tools
    #    reading the LIVE tree; repoint_llm_source_base rebases all of them.
    config.repoint_llm_source_base(llm_source)
    return llm_source
