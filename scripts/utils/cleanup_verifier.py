"""Post-cleanup consistency verifier for the dataset-cleanup leg (Wave 3 C4.2).

The dataset-cleanup leg (``dataset_cleanup.clean_trio_datasets`` + the upstream
``dedup`` column drops) removes junk files, merges provably-safe duplicate files,
and drops provably-duplicate columns, recording every decision in the per-dataset
``dataset_cleanup_ledger.as_written.json``. This module independently checks that
what the ledger *says* happened matches what is actually in the published tree —
the audit analogue of "trust, but verify".

Three phases
------------
* **must-gone** — every file/column the ledger recorded as removed must be
  absent from the published datasets tree (a junk file still present, a dropped
  duplicate file still present, or a dropped column still in its form's header is
  a removal that did not take effect).
* **must-remain** — a form that was processed but *not* removed wholesale (it had
  only column drops, or no events) must still have a published dataset (cleanup
  must not have silently deleted surviving data).
* **anomaly** — states that should be impossible after a clean run: both members
  of a known suspected-duplicate pair published side by side, a junk-pattern file
  in the published tree, or a zero-byte published dataset (Step 1.9 should have
  pruned a fully-quarantined form before promotion).

Metadata only
-------------
The verifier reads ledger JSON (counts + names), file existence, and the
**first-line JSON keys** of each published ``.jsonl`` (column names only — never a
value, the same metadata-safe read the retrieval eval uses). Findings carry only
file/column *names* + a reason string, so a verifier report can never become a
PHI side-channel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from scripts.audit.ledger import CLEANUP_LEDGER_FILENAME, DATASET_LEDGER_DIRNAME
from scripts.utils.logging_system import get_logger

__all__ = [
    "CleanupFinding",
    "CleanupVerifyReport",
    "verify_cleanup",
]

_logger = get_logger(__name__)

#: Cleanup-ledger actions that mean a *file* was removed from the published tree.
_FILE_REMOVAL_ACTIONS = frozenset({"dataset_junk_file", "dataset_duplicate_file"})
#: The action that means a *column* was dropped from a surviving file.
_COLUMN_DROP_ACTION = "dataset_column_drop"

_PHASE_MUST_GONE = "must_gone"
_PHASE_MUST_REMAIN = "must_remain"
_PHASE_ANOMALY = "anomaly"


@dataclass(frozen=True)
class CleanupFinding:
    """One cleanup-consistency problem (names + reason only — never values)."""

    phase: str  # must_gone | must_remain | anomaly
    kind: str  # machine-readable problem code
    target: str  # offending file or column NAME
    detail: str  # human-readable reason


@dataclass(frozen=True)
class CleanupVerifyReport:
    """Aggregate verifier outcome."""

    ok: bool
    findings: tuple[CleanupFinding, ...]
    checked_ledgers: int
    checked_datasets: int

    @property
    def findings_by_phase(self) -> dict[str, list[CleanupFinding]]:
        out: dict[str, list[CleanupFinding]] = {
            _PHASE_MUST_GONE: [],
            _PHASE_MUST_REMAIN: [],
            _PHASE_ANOMALY: [],
        }
        for f in self.findings:
            out.setdefault(f.phase, []).append(f)
        return out


@dataclass
class _LedgerEntry:
    """Parsed view of one per-dataset cleanup ledger."""

    stem: str
    file_removed: bool = False
    removal_action: str | None = None
    dropped_columns: dict[str, str] = field(default_factory=dict)  # column -> source file stem


def _published_columns(path: Path) -> set[str]:
    """Return the first-line JSON keys of a published ``.jsonl`` (names only).

    Reads at most the first line and decodes only its *keys*; never inspects a
    value. Returns an empty set for an empty file or a parse failure.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return set()
    first = first.strip()
    if not first:
        return set()
    try:
        obj = json.loads(first)
    except ValueError:
        return set()
    return set(obj.keys()) if isinstance(obj, dict) else set()


def _is_empty_jsonl(path: Path) -> bool:
    """True if the file is zero-byte or its first line is blank."""
    try:
        if path.stat().st_size == 0:
            return True
        with path.open("r", encoding="utf-8") as fh:
            return not fh.readline().strip()
    except OSError:
        return True


def _load_cleanup_ledgers(audit_dir: Path) -> list[_LedgerEntry]:
    """Parse every per-dataset cleanup ledger under ``audit_dir``."""
    ledger_root = Path(audit_dir) / DATASET_LEDGER_DIRNAME
    entries: list[_LedgerEntry] = []
    for ledger_path in sorted(ledger_root.glob(f"*/{CLEANUP_LEDGER_FILENAME}")):
        stem = ledger_path.parent.name
        try:
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _logger.warning("cleanup ledger unreadable: %s (skipped)", ledger_path)
            continue
        entry = _LedgerEntry(stem=stem)
        for event in data.get("events", []):
            action = event.get("action")
            var = event.get("variable_id", "")
            if action in _FILE_REMOVAL_ACTIONS:
                entry.file_removed = True
                entry.removal_action = action
            elif action == _COLUMN_DROP_ACTION:
                src = Path((event.get("where") or {}).get("dataset_file") or "").stem or stem
                entry.dropped_columns[var] = src
        entries.append(entry)
    return entries


def verify_cleanup(
    audit_dir: Path,
    published_datasets_dir: Path,
    *,
    junk_patterns: frozenset[str] | None = None,
    duplicate_pairs: list[tuple[str, str]] | None = None,
) -> CleanupVerifyReport:
    """Verify the published tree against the recorded cleanup decisions.

    Args:
        audit_dir: the study audit dir holding ``datasets/<stem>/`` ledgers.
        published_datasets_dir: the published JSONL tree (``llm_source/.../files``).
        junk_patterns: known junk stems (defaults to the dataset-cleanup set);
            any present in the published tree is an anomaly.
        duplicate_pairs: known suspected-duplicate stem pairs; both-present is an
            anomaly.
    """
    audit_dir = Path(audit_dir)
    pub = Path(published_datasets_dir)
    findings: list[CleanupFinding] = []

    published_files = {p.stem: p for p in pub.glob("*.jsonl")} if pub.is_dir() else {}
    entries = _load_cleanup_ledgers(audit_dir)

    # ── Phase 1: must-gone ────────────────────────────────────────────────────
    for entry in entries:
        if entry.file_removed and entry.stem in published_files:
            findings.append(
                CleanupFinding(
                    phase=_PHASE_MUST_GONE,
                    kind=f"removed_file_present:{entry.removal_action}",
                    target=f"{entry.stem}.jsonl",
                    detail=(
                        f"ledger recorded {entry.removal_action} for '{entry.stem}' but the "
                        "file is still in the published tree"
                    ),
                )
            )
        for column, src_stem in entry.dropped_columns.items():
            target_file = published_files.get(src_stem)
            if target_file is not None and column in _published_columns(target_file):
                findings.append(
                    CleanupFinding(
                        phase=_PHASE_MUST_GONE,
                        kind="dropped_column_present",
                        target=column,
                        detail=(
                            f"column '{column}' was recorded as dropped from "
                            f"'{src_stem}' but still appears in its published header"
                        ),
                    )
                )

    # ── Phase 2: must-remain ──────────────────────────────────────────────────
    # A form that was not removed wholesale must still be published; if its file
    # is gone, cleanup may have removed surviving data.
    findings.extend(
        CleanupFinding(
            phase=_PHASE_MUST_REMAIN,
            kind="surviving_dataset_missing",
            target=f"{entry.stem}.jsonl",
            detail=(
                f"'{entry.stem}' was processed (no file-removal event) but has no "
                "published dataset — cleanup may have removed surviving data"
            ),
        )
        for entry in entries
        if not entry.file_removed and entry.stem not in published_files
    )

    # ── Phase 3: anomaly ──────────────────────────────────────────────────────
    junk = junk_patterns if junk_patterns is not None else _default_junk_patterns()
    for stem, path in published_files.items():
        if stem in junk:
            findings.append(
                CleanupFinding(
                    phase=_PHASE_ANOMALY,
                    kind="junk_file_published",
                    target=f"{stem}.jsonl",
                    detail=f"junk-pattern file '{stem}' present in the published tree",
                )
            )
        if _is_empty_jsonl(path):
            findings.append(
                CleanupFinding(
                    phase=_PHASE_ANOMALY,
                    kind="empty_published_dataset",
                    target=f"{stem}.jsonl",
                    detail=(
                        f"published dataset '{stem}' is empty — a fully-quarantined form "
                        "should have been pruned (Step 1.9) before promotion"
                    ),
                )
            )

    pairs = duplicate_pairs if duplicate_pairs is not None else _default_duplicate_pairs()
    for a, b in pairs:
        if a in published_files and b in published_files:
            findings.append(
                CleanupFinding(
                    phase=_PHASE_ANOMALY,
                    kind="duplicate_pair_both_published",
                    target=f"{a}.jsonl|{b}.jsonl",
                    detail=(
                        f"both members of suspected-duplicate pair ('{a}', '{b}') are "
                        "published — duplicate handling did not resolve or hold the pair"
                    ),
                )
            )

    return CleanupVerifyReport(
        ok=not findings,
        findings=tuple(findings),
        checked_ledgers=len(entries),
        checked_datasets=len(published_files),
    )


def _default_junk_patterns() -> frozenset[str]:
    """Lazy import the canonical junk set from the dataset-cleanup skill module.

    Imported by canonical name (``scripts.extraction.dataset_cleanup``, resolved
    through the Note-19 migration bridge) and lazily so this shared utility has no
    import-time dependency on the plugin module; falls back to an empty set.
    """
    try:
        from scripts.extraction.dataset_cleanup import JUNK_PATTERNS

        return frozenset(JUNK_PATTERNS)
    except Exception:  # pragma: no cover - defensive: cleanup module always present
        return frozenset()


def _default_duplicate_pairs() -> list[tuple[str, str]]:
    """Lazy import the canonical suspected-duplicate pairs (see above)."""
    try:
        from scripts.extraction.dataset_cleanup import SUSPECTED_DUPLICATE_PAIRS

        return list(SUSPECTED_DUPLICATE_PAIRS)
    except Exception:  # pragma: no cover - defensive
        return []
