"""Reconciliation engine: compare SoT columns vs scrubbed-dataset columns.

The locked rule (Plan B Phase 4):

    set(sot_columns(form))
     − set(phi_dropped_aswritten(form))
     − set(cleanup_dropped_aswritten(form))
     == set(scrubbed_columns(form))

Strict name-set equality. In-place transforms (jitter, pseudonymize,
generalize, suppress, cap) keep columns present and therefore do NOT
subtract. Only column drops do.

The result dataclass surfaces both *unexplained* discrepancies (which fail
the gate) and *explained* drops (which inform the human reviewer when one
ledger explains a missing column the other did not).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ReconciliationResult",
    "load_scrubbed_columns",
    "load_sot_columns",
    "reconcile",
]


# Provenance/marker keys that pipeline writers attach to every row but which
# are NOT columns. Mirrors ``scripts.extraction.cleanup_propagation.PROVENANCE_FIELDS``
# plus the PHI-scrub idempotency marker.
_NON_COLUMN_KEYS: frozenset[str] = frozenset(
    {"_phi_scrubbed", "source_file", "_provenance", "_metadata"}
)


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of reconciling one form's SoT columns against scrubbed JSONL.

    Attributes:
        form: Form name (e.g. ``"10_TST"``).
        ok: True iff ``missing_unexplained`` and ``extra_in_scrubbed`` are
            both empty. The verify-and-promote gate fails on any False.
        missing_unexplained: Columns in SoT but not in scrubbed AND not
            explained by either drop ledger. These are the gate failures.
        extra_in_scrubbed: Columns in scrubbed but not in SoT. Indicates
            the SoT is out of sync with the data and is also a gate failure.
        explained_by_phi: Columns in SoT, not in scrubbed, and present in
            the PHI-scrub drop ledger. Informational.
        explained_by_cleanup: Columns in SoT, not in scrubbed, and present
            in the dataset-cleanup drop ledger. Informational.
    """

    form: str
    ok: bool
    missing_unexplained: frozenset[str]
    extra_in_scrubbed: frozenset[str]
    explained_by_phi: frozenset[str]
    explained_by_cleanup: frozenset[str]


def reconcile(
    form: str,
    sot_cols: frozenset[str],
    scrubbed_cols: frozenset[str],
    phi_drop: frozenset[str],
    cleanup_drop: frozenset[str],
) -> ReconciliationResult:
    """Compute the reconciliation result for one form.

    Set arithmetic:
        - missing  = sot_cols − scrubbed_cols
        - extra    = scrubbed_cols − sot_cols
        - explained_by_phi     = missing ∩ phi_drop
        - explained_by_cleanup = missing ∩ cleanup_drop
        - missing_unexplained  = missing − phi_drop − cleanup_drop
        - ok = (no missing_unexplained AND no extra)
    """
    missing = sot_cols - scrubbed_cols
    extra = scrubbed_cols - sot_cols

    explained_by_phi = missing & phi_drop
    explained_by_cleanup = missing & cleanup_drop
    missing_unexplained = missing - phi_drop - cleanup_drop

    ok = not missing_unexplained and not extra

    return ReconciliationResult(
        form=form,
        ok=ok,
        missing_unexplained=frozenset(missing_unexplained),
        extra_in_scrubbed=frozenset(extra),
        explained_by_phi=frozenset(explained_by_phi),
        explained_by_cleanup=frozenset(explained_by_cleanup),
    )


def load_sot_columns(policy_artifact: dict[str, Any]) -> frozenset[str]:
    """Return the set of SoT column names from a policy artifact.

    A policy artifact is the parsed YAML loaded from
    ``data/{study}/SoT/{form}_policy.yaml`` (or its in-memory equivalent
    after ``policy_loader.load_policy_yaml``). Column names are the keys of
    the top-level ``variables`` dict whose entries' ``record_type`` is
    ``"variable"``. Records missing ``record_type`` are treated as
    variables for backward compatibility.
    """
    variables = policy_artifact.get("variables") if isinstance(policy_artifact, dict) else None
    if not isinstance(variables, dict):
        return frozenset()

    cols: set[str] = set()
    for name, body in variables.items():
        if not isinstance(name, str) or not name:
            continue
        record_type: str | None = None
        if isinstance(body, dict):
            rt = body.get("record_type")
            if isinstance(rt, str):
                record_type = rt
        if record_type is None or record_type == "variable":
            cols.add(name)
    return frozenset(cols)


def load_scrubbed_columns(form: str, staging_root: Path) -> frozenset[str] | None:
    """Return the set of column names observed in
    ``staging_root/datasets/{form}.jsonl``.

    Returns:
        - ``frozenset`` of keys from the first non-empty row, with
          provenance/marker fields stripped (``_phi_scrubbed``,
          ``source_file``, ``_provenance``, ``_metadata``).
        - ``frozenset()`` if the file exists but contains no JSON rows.
        - ``None`` if the file does not exist (the form has not been
          scrubbed yet — the gate uses this to skip reconciliation).
    """
    jsonl_path = Path(staging_root) / "datasets" / f"{form}.jsonl"
    if not jsonl_path.is_file():
        return None

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                # Surface malformed lines as warnings — silent malformation
                # is too lenient for a verification gate. We still continue
                # scanning so the first parseable row supplies the column
                # set (a single bad line should not prevent reconciliation).
                logger.warning(
                    "malformed JSON in %s line %d: %s",
                    jsonl_path,
                    lineno,
                    exc,
                )
                continue
            if not isinstance(row, dict):
                continue
            keys = {k for k in row.keys() if isinstance(k, str)}
            return frozenset(keys - _NON_COLUMN_KEYS)

    # File exists but no parseable row — return empty frozenset, not None,
    # so the form still participates in reconciliation as a degenerate case.
    return frozenset()
