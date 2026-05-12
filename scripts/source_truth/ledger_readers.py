"""Read-only consumers of the as-written PHI scrub and dataset cleanup audits.

These pure functions consume the parsed JSON envelopes already emitted by:

    - :mod:`scripts.security.phi_scrub` (writes ``phi_scrub_report.json``)
    - :mod:`scripts.extraction.dataset_cleanup` (writes
      ``dataset_cleanup_report.json``)

and surface the per-form set of *dropped columns* — the only mutations that
shrink a form's column set. In-place transforms (jitter, pseudonymize,
generalize, suppress, cap) keep the column present and therefore must NOT be
counted here. File-level removals (junk, duplicate-pair) drop whole forms,
not columns inside a surviving form, so they are likewise excluded.

Authoritative emitter shapes (verified against the production code):

PHI scrub event::

    {"scope": str, "field": str, "file": str, "count": int}

Drop scopes are exactly ``"phi-scrub-drop"`` and
``"phi-scrub-birthdate-drop"``. The emitter prefixes every internal
action with ``"phi-scrub-"`` (see ``scripts/security/phi_scrub.py``
``_bump`` helper, where ``k = f"phi-scrub-{scope}:{field}"``). The
unprefixed ``_ACTION_DROP``/``_ACTION_BIRTHDATE_DROP`` constants in that
module are *internal* action labels — they are never written to the
report. ``file`` is the staging JSONL filename (e.g. ``"10_TST.jsonl"``).

Dataset cleanup event (within ``"removed"``)::

    {"scope": str, "name": str, "file": str, "sheet": str | None,
     "reason": str, "kept": str | None}

Column-drop scope is ``"dataset-column"`` (see
``scripts/extraction/dedup.py`` line 108, propagated by
``scripts/extraction/dataset_cleanup.py::_serialize_audit``). ``file`` is
the *source* filename — typically ``<stem>.xlsx``, NOT the produced JSONL —
because those events come from upstream extraction
(``scripts/extraction/dataset_pipeline.py:533``). Other scopes
(``dataset-junk-file``, ``dataset-duplicate-file``) describe whole-file
removals and are intentionally ignored here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "load_cleanup_dropped_columns",
    "load_phi_dropped_columns",
]


_PHI_DROP_SCOPES: frozenset[str] = frozenset({"phi-scrub-drop", "phi-scrub-birthdate-drop"})
_CLEANUP_COLUMN_SCOPE: str = "dataset-column"


def _form_from_jsonl_filename(filename: str) -> str:
    """Strip a ``.jsonl`` suffix to recover a form name. Other suffixes pass
    through unchanged so we never silently rewrite something else."""
    if filename.endswith(".jsonl"):
        return filename[: -len(".jsonl")]
    return filename


def load_phi_dropped_columns(report: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Return ``{form_name: frozenset(field_names)}`` for PHI-scrub drops.

    Only events with
    ``scope in {"phi-scrub-drop", "phi-scrub-birthdate-drop"}`` contribute.
    Form name is derived by stripping the ``.jsonl`` suffix from the event's
    ``file``. An empty/partial report yields an empty dict.
    """
    grouped: dict[str, set[str]] = {}
    events = report.get("scrubbed") if isinstance(report, Mapping) else None
    if not isinstance(events, list):
        return {}

    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("scope") not in _PHI_DROP_SCOPES:
            continue
        field = event.get("field")
        file_name = event.get("file")
        if not isinstance(field, str) or not field:
            continue
        if not isinstance(file_name, str) or not file_name:
            continue
        form = _form_from_jsonl_filename(file_name)
        grouped.setdefault(form, set()).add(field)

    return {form: frozenset(cols) for form, cols in grouped.items()}


def load_cleanup_dropped_columns(
    report: Mapping[str, Any],
    *,
    source_to_form: Mapping[str, str],
) -> dict[str, frozenset[str]]:
    """Return ``{form_name: frozenset(column_names)}`` for cleanup column drops.

    Only ``scope == "dataset-column"`` events contribute — junk-file and
    duplicate-pair file removals describe whole-file events and are not
    column drops on a surviving form.

    ``source_to_form`` maps the source dataset filename (e.g.
    ``"2A_ICBaseline.xlsx"``) to the form name (``"2A_ICBaseline"``). The
    caller — typically the verify-and-promote orchestrator — builds the
    mapping from ``policy_artifact["source"]["dataset_file"]`` for each
    policy YAML. If a drop event's ``file`` is not in the mapping, we fall
    back to the file stem so the caller can detect mismatches downstream.
    """
    grouped: dict[str, set[str]] = {}
    events = report.get("removed") if isinstance(report, Mapping) else None
    if not isinstance(events, list):
        return {}

    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("scope") != _CLEANUP_COLUMN_SCOPE:
            continue
        name = event.get("name")
        file_name = event.get("file")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(file_name, str) or not file_name:
            continue
        form = source_to_form.get(file_name)
        if form is None:
            # Fallback: strip extension so the caller still gets a usable
            # key. Reconciliation will surface a "form not in SoT" mismatch
            # if the fallback name does not correspond to any policy.
            form = Path(file_name).stem
        grouped.setdefault(form, set()).add(name)

    return {form: frozenset(cols) for form, cols in grouped.items()}
