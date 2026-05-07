"""PHI handling and dataset cleanup ledgers derived from Source Truth."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from scripts.source_truth.builder import DERIVATION_CLEANUP_LEDGER, DERIVATION_PHI_LEDGER
from scripts.source_truth.record import FORBIDDEN_ARTIFACT_VERSION_KEYS, FORBIDDEN_RAW_VALUE_KEYS

__all__ = [
    "SourceTruthLedgerError",
    "build_dataset_cleanup_ledger",
    "build_phi_handling_ledger",
]


class SourceTruthLedgerError(ValueError):
    """Raised when a ledger input would leak raw data or cannot be read."""


_FORBIDDEN_LEDGER_KEYS = (
    FORBIDDEN_RAW_VALUE_KEYS
    | FORBIDDEN_ARTIFACT_VERSION_KEYS
    | frozenset(
        {
            "after",
            "after_value",
            "before",
            "before_after",
            "before_value",
            "cell",
            "cells",
            "contents",
            "raw_identifier",
            "raw_identifiers",
            "row",
            "values",
        }
    )
)

_RUNTIME_NAME_KEYS = ("name", "variable_id", "removed", "candidate", "candidate_a", "left")
_RUNTIME_KEPT_KEYS = ("kept", "canonical", "candidate_b", "right")


def _forbidden_key_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) in _FORBIDDEN_LEDGER_KEYS:
                found.append(child_path)
            found.extend(_forbidden_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_key_paths(child, f"{path}[{index}]"))
    return found


def _reject_forbidden_keys(name: str, value: Mapping[str, Any]) -> None:
    paths = _forbidden_key_paths(value)
    if paths:
        raise SourceTruthLedgerError(
            f"{name} contains ledger-forbidden key(s): " + ", ".join(sorted(paths))
        )


def _records(source_truth_artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = source_truth_artifact.get("records")
    if not isinstance(records, list):
        raise SourceTruthLedgerError("source_truth_artifact.records must be a list")
    if not all(isinstance(record, Mapping) for record in records):
        raise SourceTruthLedgerError("source_truth_artifact.records entries must be mappings")
    return records


def _record_index(source_truth_artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        variable_id: record
        for record in _records(source_truth_artifact)
        if isinstance(variable_id := record.get("variable_id"), str) and variable_id
    }


def _source_truth_ref(variable_id: str) -> dict[str, str]:
    return {
        "artifact_type": "study_variable_source_truth",
        "variable_id": variable_id,
    }


def _normalized(record: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = record.get("normalized")
    if not isinstance(normalized, Mapping):
        raise SourceTruthLedgerError(f"{record.get('variable_id')} normalized must be a mapping")
    return normalized


def _decision_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    variable_id = record.get("variable_id")
    if not isinstance(variable_id, str) or not variable_id:
        raise SourceTruthLedgerError("source-truth records must carry a string variable_id")
    normalized = _normalized(record)
    reason = normalized.get("handling_reason")
    sensitivity_flags = normalized.get("sensitivity_flags")
    field_class = normalized.get("field_class")
    return {
        "source_truth_ref": _source_truth_ref(variable_id),
        "action": normalized.get("handling_action"),
        "reason": reason if isinstance(reason, str) else None,
        "field_class": field_class if isinstance(field_class, str) else None,
        "sensitivity_flags": (
            list(sensitivity_flags)
            if isinstance(sensitivity_flags, list)
            and all(isinstance(flag, str) for flag in sensitivity_flags)
            else []
        ),
        "review_state": record.get("review_state"),
        "source_kind": record.get("source_kind"),
    }


def _targeted_records(
    source_truth_artifact: Mapping[str, Any],
    target: str,
) -> list[Mapping[str, Any]]:
    return [
        record
        for record in _records(source_truth_artifact)
        if target in record.get("derivation_targets", [])
    ]


def _phi_ledger_records(source_truth_artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for record in _records(source_truth_artifact):
        targets = record.get("derivation_targets", [])
        if isinstance(targets, list) and DERIVATION_PHI_LEDGER in targets:
            records.append(record)
            continue

        normalized = _normalized(record)
        action = normalized.get("handling_action")
        flags = normalized.get("sensitivity_flags")
        if (
            action in {"keep", "review_required"}
            and isinstance(flags, list)
            and any(isinstance(flag, str) for flag in flags)
        ):
            records.append(record)
    return records


def _string_from(event: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _ref_from_event(
    event: Mapping[str, Any],
    records_by_id: Mapping[str, Mapping[str, Any]],
    keys: Iterable[str],
) -> dict[str, str] | None:
    candidate = _string_from(event, keys)
    if candidate is None or candidate not in records_by_id:
        return None
    return _source_truth_ref(candidate)


def _reason(event: Mapping[str, Any]) -> str | None:
    value = event.get("reason")
    return value if isinstance(value, str) else None


def _scope(event: Mapping[str, Any]) -> str:
    value = event.get("scope")
    return value if isinstance(value, str) else "runtime-event"


def _runtime_events(
    runtime_metadata: Mapping[str, Any] | None, key: str
) -> list[Mapping[str, Any]]:
    if runtime_metadata is None:
        return []
    events = runtime_metadata.get(key, [])
    if not isinstance(events, list):
        raise SourceTruthLedgerError(f"runtime_metadata.{key} must be a list")
    if not all(isinstance(event, Mapping) for event in events):
        raise SourceTruthLedgerError(f"runtime_metadata.{key} entries must be mappings")
    # Scope the forbidden-key scan to the runtime-event subtree the ledger
    # actually consumes (e.g. catches ``before_value`` leaking into a removed-
    # event mapping) without rejecting unrelated top-level runtime keys.
    _reject_forbidden_keys(f"runtime_metadata.{key}", {key: events})
    return events


def _duplicate_drops(
    runtime_metadata: Mapping[str, Any] | None,
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    drops: list[dict[str, Any]] = []
    for event in _runtime_events(runtime_metadata, "removed"):
        scope = _scope(event)
        if "duplicate" not in scope:
            continue
        dropped_ref = _ref_from_event(event, records_by_id, _RUNTIME_NAME_KEYS)
        canonical_ref = _ref_from_event(event, records_by_id, _RUNTIME_KEPT_KEYS)
        if dropped_ref is None:
            continue
        drops.append(
            {
                "scope": scope,
                "dropped_ref": dropped_ref,
                "canonical_ref": canonical_ref,
                "reason": _reason(event),
                "outcome": "dropped_exact_duplicate",
            }
        )
    return drops


def _runtime_cleanup_drops(
    runtime_metadata: Mapping[str, Any] | None,
    cleanup_record_ids: set[str],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    drops: list[dict[str, Any]] = []
    for event in _runtime_events(runtime_metadata, "removed"):
        scope = _scope(event)
        if "duplicate" in scope:
            continue
        ref = _ref_from_event(event, records_by_id, _RUNTIME_NAME_KEYS)
        if ref is None or ref["variable_id"] not in cleanup_record_ids:
            continue
        drops.append(
            {
                "scope": scope,
                "source_truth_ref": ref,
                "reason": _reason(event),
                "outcome": "runtime_cleanup_drop",
            }
        )
    return drops


def _duplicate_candidates_preserved(
    runtime_metadata: Mapping[str, Any] | None,
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    preserved: list[dict[str, Any]] = []
    for key in ("duplicate_candidates_preserved", "skipped"):
        for event in _runtime_events(runtime_metadata, key):
            left_ref = _ref_from_event(event, records_by_id, _RUNTIME_NAME_KEYS)
            right_ref = _ref_from_event(event, records_by_id, _RUNTIME_KEPT_KEYS)
            if left_ref is None and right_ref is None:
                continue
            preserved.append(
                {
                    "scope": _scope(event),
                    "candidate_refs": [ref for ref in (left_ref, right_ref) if ref is not None],
                    "reason": _reason(event),
                    "outcome": "preserved_candidate",
                }
            )
    return preserved


def _canonical_duplicate_counts(duplicate_drops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        drop["canonical_ref"]["variable_id"]
        for drop in duplicate_drops
        if isinstance(drop.get("canonical_ref"), Mapping)
        and isinstance(drop["canonical_ref"].get("variable_id"), str)
    )
    return [
        {
            "canonical_ref": _source_truth_ref(variable_id),
            "dropped_duplicate_count": count,
        }
        for variable_id, count in sorted(counts.items())
    ]


def _policy_runtime_mismatches(
    runtime_metadata: Mapping[str, Any] | None,
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for event in _runtime_events(runtime_metadata, "policy_runtime_mismatches"):
        ref = _ref_from_event(event, records_by_id, _RUNTIME_NAME_KEYS)
        if ref is None:
            continue
        mismatch_type = event.get("type")
        mismatches.append(
            {
                "source_truth_ref": ref,
                "type": mismatch_type
                if isinstance(mismatch_type, str)
                else "policy_runtime_mismatch",
                "policy_action": (
                    event["policy_action"] if isinstance(event.get("policy_action"), str) else None
                ),
                "runtime_action": (
                    event["runtime_action"]
                    if isinstance(event.get("runtime_action"), str)
                    else None
                ),
                "reason": _reason(event),
            }
        )
    return mismatches


def build_phi_handling_ledger(
    source_truth_artifact: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a metadata-only PHI handling ledger from Source Truth decisions.

    Forbidden-key scanning is scoped to the subtrees the ledger actually
    consumes (records, and runtime events when present). Unrelated
    top-level keys on the policy artifact (e.g. ``option_sets``,
    ``pdf_sections``, ``coverage``) are ignored — they are not part of the
    ledger contract.
    """
    if not isinstance(source_truth_artifact, Mapping):
        raise SourceTruthLedgerError("source_truth_artifact must be a mapping")
    if runtime_metadata is not None and not isinstance(runtime_metadata, Mapping):
        raise SourceTruthLedgerError("runtime_metadata must be a mapping when provided")
    _reject_forbidden_keys(
        "source_truth_artifact.records", {"records": _records(source_truth_artifact)}
    )

    decisions = [_decision_entry(record) for record in _phi_ledger_records(source_truth_artifact)]
    return {
        "artifact_type": "phi_handling_ledger",
        "study": source_truth_artifact.get("study"),
        "source_truth_ref": {
            "artifact_type": source_truth_artifact.get("artifact_type"),
            "study": source_truth_artifact.get("study"),
            "source_file": source_truth_artifact.get("source_file"),
        },
        "entries": decisions,
        "summary": {
            "decision_count": len(decisions),
            "actions": dict(sorted(Counter(entry["action"] for entry in decisions).items())),
        },
    }


def build_dataset_cleanup_ledger(
    source_truth_artifact: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a metadata-only dataset cleanup ledger from policy and runtime facts.

    Forbidden-key scanning is scoped to the subtrees the ledger actually
    consumes (records, and runtime events when present). Unrelated
    top-level keys on the policy artifact (e.g. ``option_sets``) are
    ignored — they are not part of the ledger contract.
    """
    if not isinstance(source_truth_artifact, Mapping):
        raise SourceTruthLedgerError("source_truth_artifact must be a mapping")
    if runtime_metadata is not None and not isinstance(runtime_metadata, Mapping):
        raise SourceTruthLedgerError("runtime_metadata must be a mapping when provided")
    _reject_forbidden_keys(
        "source_truth_artifact.records", {"records": _records(source_truth_artifact)}
    )

    records_by_id = _record_index(source_truth_artifact)
    cleanup_records = _targeted_records(source_truth_artifact, DERIVATION_CLEANUP_LEDGER)
    cleanup_record_ids = {
        variable_id
        for record in cleanup_records
        if isinstance(variable_id := record.get("variable_id"), str)
    }
    policy_drops = [_decision_entry(record) for record in cleanup_records]
    runtime_cleanup_drops = _runtime_cleanup_drops(
        runtime_metadata, cleanup_record_ids, records_by_id
    )
    duplicate_drops = _duplicate_drops(runtime_metadata, records_by_id)
    preserved = _duplicate_candidates_preserved(runtime_metadata, records_by_id)
    mismatches = _policy_runtime_mismatches(runtime_metadata, records_by_id)
    return {
        "artifact_type": "dataset_cleanup_ledger",
        "study": source_truth_artifact.get("study"),
        "source_truth_ref": {
            "artifact_type": source_truth_artifact.get("artifact_type"),
            "study": source_truth_artifact.get("study"),
            "source_file": source_truth_artifact.get("source_file"),
        },
        "policy_drops": policy_drops,
        "runtime_cleanup_drops": runtime_cleanup_drops,
        "duplicate_drops": duplicate_drops,
        "duplicate_candidates_preserved": preserved,
        "canonical_duplicate_counts": _canonical_duplicate_counts(duplicate_drops),
        "policy_runtime_mismatches": mismatches,
        "summary": {
            "policy_drop_count": len(policy_drops),
            "runtime_cleanup_drop_count": len(runtime_cleanup_drops),
            "exact_duplicate_drop_count": len(duplicate_drops),
            "duplicate_candidate_preserved_count": len(preserved),
            "policy_runtime_mismatch_count": len(mismatches),
        },
    }
