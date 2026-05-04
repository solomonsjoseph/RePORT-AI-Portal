"""Lineage/version compatibility helpers for Source Truth artifacts.

The Source Truth builders intentionally emit compact metadata artifacts.
This module adds the separate compatibility layer that ties one Source
Truth generation to its derived catalog, evidence packs, dataset schema,
ledgers, and dataset outputs.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "LINEAGE_VERSION",
    "SourceTruthLineageError",
    "artifact_ref",
    "build_lineage_report",
    "derive_generation_id",
    "stamp_generated_artifact",
    "stamp_source_truth",
    "validate_lineage_bundle",
]


LINEAGE_VERSION = "source-truth-lineage/v1"
SOURCE_TRUTH_ARTIFACT_TYPE = "study_variable_source_truth"


class SourceTruthLineageError(ValueError):
    """Raised when artifact lineage is missing or incompatible."""


def _without_lineage(artifact: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(artifact)
    clean.pop("lineage", None)
    return clean


def _stable_payload(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _short_hash(value: Any) -> str:
    return hashlib.sha256(_stable_payload(value)).hexdigest()[:16]


def _string_field(artifact: Mapping[str, Any], key: str) -> str | None:
    value = artifact.get(key)
    return value if isinstance(value, str) and value else None


def _lineage(artifact: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = artifact.get("lineage")
    return value if isinstance(value, Mapping) else None


def _generated_from(lineage: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = lineage.get("generated_from")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def derive_generation_id(
    artifact: Mapping[str, Any],
    *,
    run_id: str | None = None,
    generated_from: Iterable[Mapping[str, Any]] = (),
) -> str:
    """Derive a deterministic generation id from artifact content and parents."""
    if not isinstance(artifact, Mapping):
        raise SourceTruthLineageError("artifact must be a mapping")
    payload = {
        "artifact": _without_lineage(artifact),
        "generated_from": list(generated_from),
        "run_id": run_id,
        "lineage_version": LINEAGE_VERSION,
    }
    return f"stg-{_short_hash(payload)}"


def artifact_ref(artifact: Mapping[str, Any]) -> dict[str, str]:
    """Return the stable lineage reference for a stamped artifact."""
    if not isinstance(artifact, Mapping):
        raise SourceTruthLineageError("artifact must be a mapping")
    lineage = _lineage(artifact)
    generation_id = lineage.get("generation_id") if lineage is not None else None
    run_id = lineage.get("run_id") if lineage is not None else None
    artifact_type = _string_field(artifact, "artifact_type")
    if artifact_type is None:
        raise SourceTruthLineageError("artifact_type is required for lineage refs")

    ref = {"artifact_type": artifact_type}
    for key in ("study", "source_file"):
        value = _string_field(artifact, key)
        if value is not None:
            ref[key] = value
    if isinstance(run_id, str) and run_id:
        ref["run_id"] = run_id
    if isinstance(generation_id, str) and generation_id:
        ref["generation_id"] = generation_id
    return ref


def _stamp(
    artifact: Mapping[str, Any],
    *,
    generated_from: list[Mapping[str, Any]],
    run_id: str | None,
    generation_id: str | None,
) -> dict[str, Any]:
    resolved_generation_id = generation_id or derive_generation_id(
        artifact,
        run_id=run_id,
        generated_from=generated_from,
    )
    stamped = copy.deepcopy(dict(artifact))
    lineage: dict[str, Any] = {
        "version": LINEAGE_VERSION,
        "run_id": run_id,
        "generation_id": resolved_generation_id,
        "generated_from": copy.deepcopy(generated_from),
    }
    stamped["lineage"] = lineage
    lineage["artifact_ref"] = artifact_ref(stamped)
    return stamped


def stamp_source_truth(
    source_truth_artifact: Mapping[str, Any],
    *,
    run_id: str | None = None,
    generation_id: str | None = None,
    source_refs: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a copy of a Source Truth artifact with lineage metadata."""
    if source_truth_artifact.get("artifact_type") != SOURCE_TRUTH_ARTIFACT_TYPE:
        raise SourceTruthLineageError("stamp_source_truth requires a source-truth artifact")
    return _stamp(
        source_truth_artifact,
        generated_from=[dict(ref) for ref in source_refs],
        run_id=run_id,
        generation_id=generation_id,
    )


def stamp_generated_artifact(
    artifact: Mapping[str, Any],
    source_truth_artifact: Mapping[str, Any],
    *,
    run_id: str | None = None,
    generation_id: str | None = None,
    generated_from: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a copy of a derived artifact with Source Truth lineage."""
    if _lineage(source_truth_artifact) is None:
        raise SourceTruthLineageError("source_truth_artifact is missing lineage")
    parents = list(generated_from) if generated_from is not None else [source_truth_artifact]
    parent_refs: list[Mapping[str, Any]] = [artifact_ref(parent) for parent in parents]
    source_lineage = _lineage(source_truth_artifact)
    source_run_id = source_lineage.get("run_id") if source_lineage is not None else None
    resolved_run_id = run_id or (source_run_id if isinstance(source_run_id, str) else None)
    return _stamp(
        artifact,
        generated_from=parent_refs,
        run_id=resolved_run_id,
        generation_id=generation_id,
    )


def _problem(code: str, artifact: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    problem = {
        "code": code,
        "artifact_type": artifact.get("artifact_type"),
    }
    lineage = _lineage(artifact)
    if lineage is not None and isinstance(lineage.get("generation_id"), str):
        problem["generation_id"] = lineage["generation_id"]
    problem.update(extra)
    return problem


def _source_truth_parent_ref(lineage: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for ref in _generated_from(lineage):
        if ref.get("artifact_type") == SOURCE_TRUTH_ARTIFACT_TYPE:
            return ref
    return None


def build_lineage_report(artifacts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Report lineage compatibility across Source Truth-derived artifacts."""
    artifact_list = list(artifacts)
    problems: list[dict[str, Any]] = []
    lineages: list[Mapping[str, Any]] = []

    for artifact in artifact_list:
        if not isinstance(artifact, Mapping):
            raise SourceTruthLineageError("lineage bundle entries must be mappings")
        lineage = _lineage(artifact)
        if lineage is None:
            problems.append(_problem("missing_lineage", artifact))
            continue
        lineages.append(lineage)

    versions = {
        lineage["version"]
        for lineage in lineages
        if isinstance(lineage.get("version"), str) and lineage["version"]
    }
    run_ids = {
        lineage["run_id"]
        for lineage in lineages
        if isinstance(lineage.get("run_id"), str) and lineage["run_id"]
    }
    if len(versions) > 1:
        problems.append({"code": "mixed_lineage_versions", "versions": sorted(versions)})
    if len(run_ids) > 1:
        problems.append({"code": "mixed_run_ids", "run_ids": sorted(run_ids)})
    source_truths = [
        artifact
        for artifact in artifact_list
        if artifact.get("artifact_type") == SOURCE_TRUTH_ARTIFACT_TYPE and _lineage(artifact)
    ]
    if not source_truths:
        problems.append({"code": "missing_source_truth"})
        source_truth_generation_id = None
    elif len(source_truths) > 1:
        problems.append({"code": "multiple_source_truth_artifacts"})
        source_truth_generation_id = None
    else:
        source_truth_generation_id = source_truths[0]["lineage"]["generation_id"]

    for artifact in artifact_list:
        if artifact.get("artifact_type") == SOURCE_TRUTH_ARTIFACT_TYPE:
            continue
        lineage = _lineage(artifact)
        if lineage is None or source_truth_generation_id is None:
            continue
        parent_ref = _source_truth_parent_ref(lineage)
        if parent_ref is None:
            problems.append(_problem("missing_source_truth_ref", artifact))
            continue
        actual_generation_id = parent_ref.get("generation_id")
        if actual_generation_id != source_truth_generation_id:
            problems.append(
                _problem(
                    "stale_artifact",
                    artifact,
                    expected_source_truth_generation_id=source_truth_generation_id,
                    actual_source_truth_generation_id=actual_generation_id,
                )
            )

    return {
        "ok": not problems,
        "version": next(iter(versions), None) if len(versions) <= 1 else None,
        "run_id": next(iter(run_ids), None) if len(run_ids) <= 1 else None,
        "source_truth_generation_id": source_truth_generation_id,
        "artifact_count": len(artifact_list),
        "problems": problems,
    }


def validate_lineage_bundle(artifacts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a compatibility report or raise on invalid lineage."""
    report = build_lineage_report(artifacts)
    if not report["ok"]:
        codes = ", ".join(problem["code"] for problem in report["problems"])
        raise SourceTruthLineageError(f"invalid source-truth lineage bundle: {codes}")
    return report
