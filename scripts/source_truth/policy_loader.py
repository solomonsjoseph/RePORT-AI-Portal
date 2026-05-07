# scripts/source_truth/policy_loader.py
"""Adapter that reads a manual policy YAML and returns the
source-truth-artifact mapping shape that downstream builders accept.

This is a pure transformation. Manual policy YAMLs are frozen
(`CONTEXT.md` §"Build Pipeline — May 2026" hard invariant #1) — this
loader does not modify the source files. The output is constructed
in-memory only.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts.source_truth import builder as _builder
from scripts.source_truth.record import SourceTruthValidationError, validate_record

__all__ = [
    "DuplicateFormNameError",
    "PolicyLoaderError",
    "load_policy_yaml",
    "validate_unique_form_names",
]


class DuplicateFormNameError(ValueError):
    """Raised when two or more policy YAMLs declare the same ``form:`` value.

    Two policy files declaring the same ``form:`` name would silently clobber
    each other downstream — aggregated catalogs, ledgers, and the concept index
    would all be corrupt. This error is raised eagerly at policy-load time, in
    both the build coordinator and the verify-and-promote gate, so the
    collision is impossible to miss.
    """


def validate_unique_form_names(
    policy_artifacts: list[dict[str, Any]],
    *,
    sources: list[Path] | None = None,
) -> None:
    """Raise ``DuplicateFormNameError`` if two policy artifacts share a ``form:`` name.

    Args:
        policy_artifacts: Loaded policy mappings (each must contain a string
            ``form`` key — artifacts without one are skipped silently, matching
            the previous gate behavior).
        sources: Optional list of source paths parallel to ``policy_artifacts``.
            When provided, the error message includes the YAML filenames that
            declared each duplicate; when ``None``, only form names are listed.

    The function is the single source of truth for this invariant. Both
    ``scripts.source_truth.build.run_build`` and
    ``scripts.source_truth.verify_and_promote.run_verification`` call it
    immediately after loading policies and before any aggregation runs.
    """
    if sources is not None and len(sources) != len(policy_artifacts):
        raise ValueError(
            "validate_unique_form_names: sources length must match policy_artifacts length"
        )

    form_sources: dict[str, list[Path | None]] = {}
    for index, artifact in enumerate(policy_artifacts):
        form_name = artifact.get("form")
        if not isinstance(form_name, str):
            continue
        source_path = sources[index] if sources is not None else None
        form_sources.setdefault(form_name, []).append(source_path)

    duplicates = {f: ps for f, ps in form_sources.items() if len(ps) > 1}
    if not duplicates:
        return

    if sources is not None:
        details = "; ".join(
            f"{form}={[str(p) for p in paths]}"
            for form, paths in sorted(duplicates.items())
        )
    else:
        details = ", ".join(
            f"{form}({len(paths)} policies)" for form, paths in sorted(duplicates.items())
        )
    raise DuplicateFormNameError(
        f"duplicate form name(s) declared across policy YAMLs: {details}"
    )


class PolicyLoaderError(ValueError):
    """Raised when a policy YAML cannot be adapted into a source-truth artifact."""


_REQUIRED_TOP_LEVEL = ("schema_version", "study", "form", "variables")

# YAML review.state values that map to record-level review_state values.
# "resolved" is the YAML convention for a human-reviewed decision; it maps to
# "reviewed" which is the only valid slot in REVIEW_STATE_VALUES for that state.
_REVIEW_STATE_MAP: dict[str, str] = {
    "auto_normalized": "auto_normalized",
    "review_required": "review_required",
    "reviewed": "reviewed",
    "resolved": "reviewed",  # YAML convention → record schema equivalent
}


def _translate_record(yaml_record: Mapping[str, Any], *, variable_id: str) -> dict[str, Any]:
    """Translate a manual-YAML record into the catalog-compatible record shape.

    The manual YAML schema uses different key names and value conventions than
    the shape produced by builder._build_record_for_field(). This function
    bridges the two so that load_policy_yaml() returns records that pass
    validate_record() and can be fed directly to build_catalog_artifact().

    Raises:
        PolicyLoaderError: If the translated record fails validate_record().
    """
    sp = yaml_record.get("source_presence") or {}
    dataset_present = sp.get("dataset") == "present"
    pdf_present = sp.get("pdf") == "present"
    dictionary_present = sp.get("dictionary") == "present"

    hi = yaml_record.get("handling_intent") or {}
    action: str = hi.get("action") or "review_required"
    reason_raw = hi.get("reason")
    reason: str | None = reason_raw if isinstance(reason_raw, str) else None

    nm = yaml_record.get("normalized_metadata") or {}
    field_label_raw = nm.get("display_label")
    field_label: str | None = field_label_raw if isinstance(field_label_raw, str) else None
    section_raw = nm.get("section")
    section: str | None = section_raw if isinstance(section_raw, str) else None
    option_set_name_raw = nm.get("options_ref")
    option_set_name: str | None = (
        option_set_name_raw if isinstance(option_set_name_raw, str) else None
    )

    source_kind = _builder._source_kind(dataset_present, pdf_present, action)
    derivation_targets = _builder._derivation_targets_for_action(
        action, reason, dataset_present=dataset_present
    )

    # Prefer the YAML review.state when it's a recognised value; fall back to
    # the action-derived default. "resolved" (YAML convention) maps to "reviewed".
    yaml_review_state = (yaml_record.get("review") or {}).get("state")
    review_state = _REVIEW_STATE_MAP.get(str(yaml_review_state), "") if yaml_review_state else ""
    if not review_state:
        review_state = _builder._review_state_for_action(action)

    est = yaml_record.get("exact_source_text") or {}
    annotation_text = est.get("pdf_annotation")
    annotation_status = "direct" if annotation_text else "not_annotated"

    presence: dict[str, Any] = {
        "dataset": (
            {"present": True, "column": variable_id} if dataset_present else {"present": False}
        ),
        "pdf": {
            "present": pdf_present,
            "annotation_status": annotation_status,
            **({"section": section} if section else {}),
        },
        "dictionary": {"present": dictionary_present},
    }

    pdf_options = list((yaml_record.get("options") or {}).get("source_defined") or [])

    exact_source_wording: dict[str, Any] = {
        "dataset_column": variable_id if dataset_present else None,
        "pdf_question": est.get("pdf_question"),
        "pdf_options": pdf_options,
        "dictionary_label": None,
    }

    phi_cat = yaml_record.get("phi_sensitive_category")
    sensitivity_flags: list[str] = (
        [phi_cat]
        if isinstance(phi_cat, str) and phi_cat and phi_cat not in ("not_phi", "unknown")
        else []
    )
    confidence = (yaml_record.get("evidence") or {}).get("level") or "low"
    analysis_queryable = dataset_present and _builder.DERIVATION_DATASET_SCHEMA in derivation_targets

    relationships_raw = yaml_record.get("relationships") or {}
    has_relationships = any(
        v not in (None, [], "") for v in relationships_raw.values()
    ) if isinstance(relationships_raw, Mapping) else False

    normalized: dict[str, Any] = {
        "label": _builder._normalize_label(variable_id, field_label),
        "confidence": confidence,
        "normalization_basis": _builder._normalization_basis(field_label),
        "handling_action": action,
    }
    if reason:
        normalized["handling_reason"] = reason
    if sensitivity_flags:
        normalized["sensitivity_flags"] = sensitivity_flags
    normalized["analysis_queryable"] = analysis_queryable
    if section:
        normalized["section"] = section
    if option_set_name:
        normalized["option_set"] = option_set_name
    if pdf_options:
        normalized["source_defined_options"] = pdf_options
    if has_relationships:
        normalized["relationships"] = dict(relationships_raw)

    source_references: dict[str, Any] = {
        "dataset": ({"column": variable_id} if dataset_present else {"present": False}),
        "pdf": {
            "annotation_status": annotation_status,
            "annotation_pages": [],
            **({"option_set": option_set_name} if option_set_name else {}),
            **({"relationships": dict(relationships_raw)} if has_relationships else {}),
        },
    }

    record: dict[str, Any] = {
        "variable_id": variable_id,
        "source_kind": source_kind,
        "review_state": review_state,
        "presence": presence,
        "exact_source_wording": exact_source_wording,
        "normalized": normalized,
        "source_references": source_references,
        "derivation_targets": derivation_targets,
    }

    try:
        validate_record(record)
    except SourceTruthValidationError as exc:
        raise PolicyLoaderError(
            f"Translated record for {variable_id!r} failed validation: {exc}"
        ) from exc

    return record


def load_policy_yaml(path: str | Path) -> dict[str, Any]:
    """Read a `_policy.yaml` and return a source-truth-artifact mapping.

    Args:
        path: Filesystem path to a manual policy YAML.

    Returns:
        Mapping with keys: study, form, schema_version, records,
        ledger_expectations, validation, source, pdf_form_metadata,
        coverage, pdf_sections, dataset_context, option_sets,
        catalog_refs, evidence_packs.

    Raises:
        PolicyLoaderError: missing required keys or malformed structure.
    """
    path = Path(path)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyLoaderError(f"Failed to parse YAML at {path}: {exc}") from exc

    if not isinstance(loaded, Mapping):
        raise PolicyLoaderError(f"{path}: top-level must be a mapping")

    for key in _REQUIRED_TOP_LEVEL:
        if key not in loaded:
            raise PolicyLoaderError(f"{path}: missing required key '{key}'")

    variables = loaded["variables"]
    if isinstance(variables, list):
        raw_entries = [(entry.get("variable_id", ""), entry) for entry in variables]
    elif isinstance(variables, Mapping):
        raw_entries = [(k, v) for k, v in variables.items()]
    else:
        raise PolicyLoaderError(f"{path}: 'variables' must be a list or mapping")

    records: list[dict[str, Any]] = []
    for variable_id, yaml_record in raw_entries:
        if not isinstance(yaml_record, Mapping):
            continue
        # Skip non-variable entries (e.g. PDF-only context records).
        record_type = yaml_record.get("record_type", "variable")
        if record_type != "variable":
            continue
        records.append(_translate_record(yaml_record, variable_id=variable_id))

    artifact: dict[str, Any] = {
        "schema_version": loaded["schema_version"],
        "study": loaded["study"],
        "form": loaded["form"],
        "records": records,
        "ledger_expectations": dict(loaded.get("ledger_expectations") or {}),
        "validation": dict(loaded.get("validation") or {}),
        "source": dict(loaded.get("source") or {}),
        "pdf_form_metadata": dict(loaded.get("pdf_form_metadata") or {}),
        "coverage": dict(loaded.get("coverage") or {}),
        "pdf_sections": dict(loaded.get("pdf_sections") or {}),
        "dataset_context": dict(loaded.get("dataset_context") or {}),
        "option_sets": dict(loaded.get("option_sets") or {}),
        "catalog_refs": dict(loaded.get("catalog_refs") or {}),
        "evidence_packs": dict(loaded.get("evidence_packs") or {}),
        "policy_status": loaded.get("policy_status"),
    }
    return artifact
