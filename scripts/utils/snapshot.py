"""Immutable study-snapshot subsystem (W1).

A *study snapshot* is a named, versioned record of a fully-clean publish pass.
When a maintainer re-runs the pipeline after resolving every held set, the
resolved state is committed into ``output/{STUDY}/snapshots/{snapshot_id}/``:

* ``llm_source/``            — copy of the PHI-scrubbed tree the LLM reads
* ``phi_handling_approval.json`` — the run's approval report (approved/held)
* ``verifier_report.json``   — the run's verifier assertion report
* ``snapshot_manifest.json`` — content hashes + provenance + form lists
* ``.NO_LLM_ZONE``           — defence-in-depth sentinel at the snapshot root

Snapshots are **immutable**: writing a snapshot whose directory already exists
raises :class:`SnapshotExistsError`. A new clean pass mints a new snapshot id;
it never overwrites a prior one.

Snapshot ids are **deterministic** — minted as a SHA-256 content hash of the
copied ``llm_source/`` manifest combined with the source ``run_id``. There is
no timestamp or randomness in the id, so an identical clean pass on identical
input yields an identical id (which then trips the immutability guard rather
than silently re-writing).

SECURITY
--------
The snapshot ROOT is OUTSIDE the agent read zone (``llm_source/`` + ``agent/``).
``scripts.ai_assistant.file_access.validate_agent_read`` hard-rejects any path
under ``snapshots/`` except a ``snapshots/{id}/llm_source/`` subtree that has
been explicitly selected (``config.STUDY_LLM_SOURCE_DIR`` repointed at it).
The ``phi_handling_approval.json`` / ``verifier_report.json`` /
``snapshot_manifest.json`` files live at the snapshot root, a *sibling/parent*
of the selected ``llm_source/`` — never inside it — so a snapshot selection
never widens the read zone to include approval/manifest bytes.

Fail-closed: every error condition raises; nothing is silently skipped.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import config
from scripts.audit.ledger import ensure_no_llm_sentinel
from scripts.extraction.io import atomic_write_json

__all__ = [
    "MANIFEST_FILENAME",
    "SnapshotError",
    "SnapshotExistsError",
    "SnapshotNotFoundError",
    "list_snapshots",
    "load_snapshot",
    "select_snapshot_llm_source",
    "snapshot_llm_source_path",
    "snapshot_path",
    "snapshots_root",
    "write_snapshot",
]

MANIFEST_FILENAME = "snapshot_manifest.json"
APPROVAL_FILENAME = "phi_handling_approval.json"
VERIFIER_REPORT_FILENAME = "verifier_report.json"
LLM_SOURCE_DIRNAME = "llm_source"
_SNAPSHOT_ID_PREFIX = "snap_"
_SNAPSHOT_ID_HASH_LEN = 32  # hex chars of the sha256 digest kept in the id


class SnapshotError(Exception):
    """Base class for snapshot-subsystem failures."""


class SnapshotExistsError(SnapshotError):
    """Raised when writing a snapshot whose directory already exists.

    Snapshots are immutable; an existing id is never overwritten.
    """


class SnapshotNotFoundError(SnapshotError):
    """Raised when a requested snapshot id does not exist on disk."""


# ---------------------------------------------------------------------------
# Path helpers — all routed through config.* constants.
# ---------------------------------------------------------------------------


def snapshots_root(study: str) -> Path:
    """Return ``output/{study}/snapshots/`` for *study*.

    Derived from ``config.OUTPUT_DIR`` so an explicit *study* argument resolves
    correctly even when ``config.STUDY_NAME`` (set at import time) differs.
    """
    if not study:
        raise SnapshotError("study must not be empty")
    return Path(config.OUTPUT_DIR) / study / "snapshots"


def snapshot_path(study: str, snapshot_id: str | None = None) -> Path:
    """Return the snapshot directory for *study*.

    With *snapshot_id* ``None``, returns the per-study ``snapshots/`` root.
    Otherwise returns ``snapshots/{snapshot_id}/``.
    """
    root = snapshots_root(study)
    if snapshot_id is None:
        return root
    _validate_snapshot_id(snapshot_id)
    return root / snapshot_id


def snapshot_llm_source_path(study: str, snapshot_id: str) -> Path:
    """Return ``snapshots/{snapshot_id}/llm_source/`` — the only LLM-readable
    subtree of a snapshot, and only once explicitly selected."""
    return snapshot_path(study, snapshot_id) / LLM_SOURCE_DIRNAME


def _snapshot_manifest_path(study: str, snapshot_id: str) -> Path:
    return snapshot_path(study, snapshot_id) / MANIFEST_FILENAME


def _validate_snapshot_id(snapshot_id: str) -> None:
    """Reject empty or path-bearing snapshot ids (no traversal, no separators)."""
    if not snapshot_id:
        raise SnapshotError("snapshot_id must not be empty")
    if snapshot_id in (".", ".."):
        raise SnapshotError(f"invalid snapshot_id: {snapshot_id!r}")
    if "/" in snapshot_id or "\\" in snapshot_id or "\x00" in snapshot_id:
        raise SnapshotError(f"snapshot_id must not contain path separators: {snapshot_id!r}")
    if Path(snapshot_id).name != snapshot_id:
        raise SnapshotError(f"snapshot_id must be a bare directory name: {snapshot_id!r}")


# ---------------------------------------------------------------------------
# Content hashing — deterministic, content-only (no mtimes, no randomness).
# ---------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> dict[str, str]:
    """Return ``{relative_posix_path: sha256_hex}`` for every file under *root*.

    Sorted, content-only. Directories are walked; files (including symlinks to
    files) are hashed by their target content. The mapping is deterministic for
    identical content regardless of filesystem mtimes.
    """
    if not root.is_dir():
        raise SnapshotError(f"expected a directory to hash, got: {root}")
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        manifest[rel] = _file_sha256(path)
    return manifest


def _assert_no_escaping_symlinks(root: Path) -> None:
    """Fail-closed if any symlink under *root* resolves OUTSIDE *root*.

    ``write_snapshot`` copies ``llm_source/`` with ``shutil.copytree`` (default
    ``symlinks=False``), which DEREFERENCES symlinks and copies their *target*
    content. A symlink inside a malformed/compromised ``llm_source/`` that
    points at a raw ``.xlsx``/``.jsonl`` outside the tree would otherwise bake
    that out-of-tree (possibly PHI) content into the immutable, re-exposable
    snapshot. Reject any such escape before a single byte is copied.
    """
    root_real = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        target_real = path.resolve()
        if target_real != root_real and root_real not in target_real.parents:
            raise SnapshotError(
                "llm_source contains a symlink that escapes the tree; refusing to "
                f"snapshot (fail-closed): {path.relative_to(root).as_posix()}"
            )


def _mint_snapshot_id(llm_source_manifest: dict[str, str], run_id: str) -> str:
    """Mint a deterministic ``snap_<hash>`` id from the llm_source manifest + run_id.

    The hash input is a canonical JSON encoding of the manifest (sorted keys)
    plus the source ``run_id``. No timestamp, no randomness — identical content
    + run_id always yields the same id.
    """
    if not run_id:
        raise SnapshotError("run_id must not be empty")
    canonical = json.dumps(
        {"run_id": run_id, "llm_source_manifest": llm_source_manifest},
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_SNAPSHOT_ID_PREFIX}{digest[:_SNAPSHOT_ID_HASH_LEN]}"


# ---------------------------------------------------------------------------
# Source-artifact resolution for the active run.
# ---------------------------------------------------------------------------


def _run_dir(study: str, run_id: str) -> Path:
    return Path(config.OUTPUT_DIR) / study / "runs" / run_id


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SnapshotError(f"expected a JSON object at {path}")
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_snapshot(study: str, run_id: str, *, snapshot_id: str | None = None) -> Path:
    """Write an immutable snapshot of the run's clean publish pass.

    Copies the study's ``llm_source/`` tree, the run's
    ``phi_handling_approval.json`` and ``verifier_report.json`` into
    ``snapshots/{snapshot_id}/`` and writes ``snapshot_manifest.json`` plus a
    ``.NO_LLM_ZONE`` sentinel at the snapshot root.

    The *snapshot_id* is minted deterministically (content hash of the
    llm_source manifest + *run_id*) unless supplied explicitly.

    Raises:
        SnapshotError: a required source artifact is missing/unreadable.
        SnapshotExistsError: the target snapshot directory already exists
            (immutability — a clean pass never overwrites a prior snapshot).
    """
    if not study:
        raise SnapshotError("study must not be empty")
    if not run_id:
        raise SnapshotError("run_id must not be empty")

    # Derive the source tree from the explicit *study* arg via config.OUTPUT_DIR
    # — NOT from the module-global config.STUDY_LLM_SOURCE_DIR. That global is
    # repointed when a snapshot is activated in the UI (see snapshot_select), so
    # reading it here would capture the *previously-activated* snapshot's tree
    # instead of the live publish; it would also read the wrong study when the
    # *study* arg differs from config.STUDY_NAME. Every other path in this module
    # already derives from the study arg via config.OUTPUT_DIR.
    llm_source_src = Path(config.OUTPUT_DIR) / study / LLM_SOURCE_DIRNAME
    if not llm_source_src.is_dir():
        raise SnapshotError(f"llm_source tree not found at {llm_source_src}; cannot snapshot")

    # Fail-closed: a symlink under llm_source/ that escapes the tree would be
    # dereferenced by copytree and bake out-of-tree (possibly PHI) content into
    # the immutable snapshot. Reject before hashing or copying anything.
    _assert_no_escaping_symlinks(llm_source_src)

    run_dir = _run_dir(study, run_id)
    approval_src = run_dir / APPROVAL_FILENAME
    verifier_src = run_dir / VERIFIER_REPORT_FILENAME
    if not approval_src.is_file():
        raise SnapshotError(f"approval report not found at {approval_src}; cannot snapshot")
    if not verifier_src.is_file():
        raise SnapshotError(f"verifier report not found at {verifier_src}; cannot snapshot")

    # Content manifest of the source llm_source tree — also the id seed.
    llm_source_manifest = _tree_manifest(llm_source_src)

    if snapshot_id is None:
        snapshot_id = _mint_snapshot_id(llm_source_manifest, run_id)
    else:
        _validate_snapshot_id(snapshot_id)

    dest = snapshot_path(study, snapshot_id)
    # Immutability guard — fail-closed before touching disk.
    if dest.exists():
        raise SnapshotExistsError(
            f"snapshot {snapshot_id!r} already exists at {dest}; snapshots are immutable"
        )

    # Read provenance from the approval report (approved/held form lists) and
    # verifier_passed from the verifier report.
    approval_payload = _read_json(approval_src)
    verifier_payload = _read_json(verifier_src)
    approved_forms = [str(f) for f in approval_payload.get("approved_forms", [])]
    held_forms = [str(f) for f in approval_payload.get("held_forms", [])]
    # verifier_report.json has no "verifier_passed" key — its canonical pass
    # signal is "overall" == "pass" (with "exit_code" == 0 on pass). Accept
    # either positive signal; default to False (fail-closed) when neither says
    # pass.
    verifier_passed = (
        verifier_payload.get("overall") == "pass" or verifier_payload.get("exit_code") == 0
    )

    # ---- Build under a temp dir, then atomically rename into place. ---------
    # A partial copy must never become a visible snapshot. Build beside the
    # final dir and replace() the directory once complete.
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{snapshot_id}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        staging.mkdir(parents=True)
        # Copy the llm_source tree (already PHI-scrubbed).
        shutil.copytree(llm_source_src, staging / LLM_SOURCE_DIRNAME)
        # Copy approval + verifier report.
        shutil.copy2(approval_src, staging / APPROVAL_FILENAME)
        shutil.copy2(verifier_src, staging / VERIFIER_REPORT_FILENAME)

        # Re-hash the COPIED llm_source so the manifest reflects exactly what
        # landed in the snapshot (defence against a mid-copy mutation).
        copied_manifest = _tree_manifest(staging / LLM_SOURCE_DIRNAME)
        if copied_manifest != llm_source_manifest:
            raise SnapshotError(
                "llm_source tree changed during snapshot copy; aborting (fail-closed)"
            )

        manifest = {
            "snapshot_id": snapshot_id,
            "study": study,
            "source_run_id": run_id,
            "verifier_passed": verifier_passed,
            "approved_forms": approved_forms,
            "held_forms": held_forms,
            "llm_source_manifest": copied_manifest,
            "approval_sha256": _file_sha256(staging / APPROVAL_FILENAME),
            "verifier_report_sha256": _file_sha256(staging / VERIFIER_REPORT_FILENAME),
        }
        atomic_write_json(staging / MANIFEST_FILENAME, manifest)

        # Defence-in-depth: .NO_LLM_ZONE sentinel at the snapshot root. The
        # read-zone containment check is the primary control; this is belt-and-
        # suspenders so any future widening still trips a no-LLM signal.
        ensure_no_llm_sentinel(staging)

        # Atomic publish: rename the completed staging dir into place. Re-check
        # existence to close the TOCTOU window against a concurrent writer.
        if dest.exists():
            raise SnapshotExistsError(
                f"snapshot {snapshot_id!r} already exists at {dest}; snapshots are immutable"
            )
        staging.replace(dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    return dest


def list_snapshots(study: str) -> list[str]:
    """Return the sorted list of snapshot ids present for *study*.

    A snapshot is counted only when it has a readable ``snapshot_manifest.json``
    (a bare/partial directory is ignored). Returns ``[]`` when the snapshots
    root does not exist.
    """
    root = snapshots_root(study)
    if not root.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue  # partial / temp dir
        if (child / MANIFEST_FILENAME).is_file():
            ids.append(child.name)
    return ids


def load_snapshot(study: str, snapshot_id: str) -> dict:
    """Return the parsed ``snapshot_manifest.json`` for *snapshot_id*.

    Raises:
        SnapshotNotFoundError: the snapshot dir or its manifest is absent.
        SnapshotError: the manifest is unreadable / not a JSON object.
    """
    _validate_snapshot_id(snapshot_id)
    dest = snapshot_path(study, snapshot_id)
    if not dest.is_dir():
        raise SnapshotNotFoundError(f"snapshot {snapshot_id!r} not found at {dest}")
    manifest_path = _snapshot_manifest_path(study, snapshot_id)
    if not manifest_path.is_file():
        raise SnapshotNotFoundError(f"snapshot {snapshot_id!r} has no manifest at {manifest_path}")
    try:
        return _read_json(manifest_path)
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt/unreadable manifest is a SnapshotError (a subclass of which
        # callers like ui.snapshot_select.available_snapshots already catch) so
        # one bad snapshot is skipped rather than hiding every other snapshot.
        raise SnapshotError(
            f"snapshot {snapshot_id!r} manifest is unreadable at {manifest_path}"
        ) from exc


def select_snapshot_llm_source(study: str, snapshot_id: str) -> Path:
    """Resolve and validate the ``llm_source/`` subtree of a selected snapshot.

    Returns the absolute path the Load Study loader assigns to
    ``config.STUDY_LLM_SOURCE_DIR`` when a maintainer selects this snapshot.
    Exposing the subtree is the caller's responsibility (repoint the config
    constant); this helper only resolves + fail-closed validates that the
    snapshot and its ``llm_source/`` exist.

    Raises:
        SnapshotNotFoundError: snapshot or its ``llm_source/`` is absent.
    """
    _validate_snapshot_id(snapshot_id)
    dest = snapshot_path(study, snapshot_id)
    if not dest.is_dir():
        raise SnapshotNotFoundError(f"snapshot {snapshot_id!r} not found at {dest}")
    llm_source = snapshot_llm_source_path(study, snapshot_id)
    if not llm_source.is_dir():
        raise SnapshotNotFoundError(
            f"snapshot {snapshot_id!r} has no llm_source tree at {llm_source}"
        )
    return llm_source
