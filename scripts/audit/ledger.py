from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import config
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
        self._iso_timestamp: str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._events: list[dict] = []
        self._sentinel_seen: bool = False

    # ------------------------------------------------------------------
    # Phase 4 runtime guard + sentinel
    # ------------------------------------------------------------------

    def _phase4_guard(self) -> None:
        """Phase 4: refuse writes when LLM-agent role; ensure sentinel."""
        from scripts.audit import is_llm_agent

        if is_llm_agent():
            raise PermissionError("audit ledger write refused: REPORTAL_PROCESS_ROLE=llm-agent")
        self._ensure_sentinel()

    def _ensure_sentinel(self) -> None:
        """Ensure the .NO_LLM_ZONE sentinel exists in the audit dir.

        First call (per LedgerWriter instance): if sentinel missing, create it.
        No tampering alarm — co-tenant writers (phi_scrub, dataset_cleanup)
        legitimately populate the audit dir before the first ledger write.

        Subsequent calls: if the sentinel was previously confirmed and is now
        missing, treat it as tampering — alarm + refuse the write.
        """
        audit_dir = self._output_path.parent
        sentinel = audit_dir / config.AUDIT_NO_LLM_SENTINEL_NAME
        if sentinel.is_file():
            self._sentinel_seen = True
            return
        if self._sentinel_seen:
            # Sentinel disappeared after we saw it — tampering.
            self._emit_sentinel_alarm()
            raise PermissionError(f"audit sentinel missing at {sentinel}; ledger write refused")
        # First time we look and sentinel is missing — create it. Idempotent.
        audit_dir.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("")  # presence is the signal
        self._sentinel_seen = True

    def _emit_sentinel_alarm(self) -> None:
        alarm = {
            "event": "sentinel_missing",
            "path": str(self._output_path),
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        config.AUDIT_SENTINEL_ALARM_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.AUDIT_SENTINEL_ALARM_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(alarm, sort_keys=True) + "\n")

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
        self._phase4_guard()
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
        self._phase4_guard()
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
        self._phase4_guard()
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
