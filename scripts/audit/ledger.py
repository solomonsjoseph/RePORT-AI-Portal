from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scripts.extraction.io import atomic_write_json

__all__ = ["LedgerWriter"]

_PHI_ACTIONS: frozenset[str] = frozenset(
    {
        "drop",
        "pseudonymize",
        "jitter_date",
        "generalize",
        "suppress_small_cell",
        "cap",
        "birthdate_drop",
    }
)

_CLEANUP_ACTIONS: frozenset[str] = frozenset(
    {
        "dataset_column_drop",
        "dataset_junk_file",
        "dataset_duplicate_file",
    }
)


class LedgerWriter:
    """Collects audit events and writes them atomically to a JSON ledger file."""

    def __init__(
        self,
        *,
        output_path: Path,
        run_id: str | None = None,
        scrub_config_hash: str | None = None,
        input_dataset_hash: str | None = None,
    ) -> None:
        self._output_path = Path(output_path)
        self._run_id: str = run_id if run_id is not None else f"run_{uuid4().hex}"
        self._scrub_config_hash = scrub_config_hash
        self._input_dataset_hash = input_dataset_hash
        self._iso_timestamp: str = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        self._events: list[dict] = []

    def add_phi_event(
        self,
        *,
        form: str,
        variable_id: str,
        action: str,
        rule_taxonomy: str | None,
        rule_project_category: str | None,
        rationale: str,
        dataset_file: str | None,
        pdf_source: str | None,
        count: int | None,
    ) -> None:
        """Append one PHI handling event. Raises ValueError on unknown action."""
        if not form:
            raise ValueError("form must not be empty")
        if not variable_id:
            raise ValueError("variable_id must not be empty")
        if action not in _PHI_ACTIONS:
            raise ValueError(f"Unknown action: {action!r}")
        if count is not None and count < 0:
            raise ValueError(f"count must be >= 0, got {count}")
        self._events.append(
            {
                "form": form,
                "variable_id": variable_id,
                "action": action,
                "rule": {
                    "taxonomy": rule_taxonomy,
                    "project_category": rule_project_category,
                },
                "rationale": rationale,
                "where": {
                    "dataset_file": dataset_file,
                    "pdf_source": pdf_source,
                },
                "count": count,
            }
        )

    def add_cleanup_event(
        self,
        *,
        form: str,
        variable_id: str,
        action: str,
        rule_project_category: str | None,
        rationale: str,
        dataset_file: str | None,
        count: int | None,
    ) -> None:
        """Append one dataset cleanup event. Raises ValueError on unknown action."""
        if not form:
            raise ValueError("form must not be empty")
        if not variable_id:
            raise ValueError("variable_id must not be empty")
        if action not in _CLEANUP_ACTIONS:
            raise ValueError(f"Unknown action: {action!r}")
        if count is not None and count < 0:
            raise ValueError(f"count must be >= 0, got {count}")
        self._events.append(
            {
                "form": form,
                "variable_id": variable_id,
                "action": action,
                "rule": {
                    "taxonomy": None,
                    "project_category": rule_project_category,
                },
                "rationale": rationale,
                "where": {
                    "dataset_file": dataset_file,
                    "pdf_source": None,
                },
                "count": count,
            }
        )

    def flush(self) -> None:
        """Write events to output_path atomically. Safe to call multiple times (overwrites)."""
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        envelope: dict = {
            "run_id": self._run_id,
            "iso_timestamp": self._iso_timestamp,
            "scrub_config_hash": self._scrub_config_hash,
            "input_dataset_hash": self._input_dataset_hash,
            "events": self._events,
        }
        atomic_write_json(self._output_path, envelope)

    def event_count(self) -> int:
        """Return number of events collected so far."""
        return len(self._events)
