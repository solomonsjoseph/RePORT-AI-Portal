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

__all__ = ["PolicyLoaderError", "load_policy_yaml"]


class PolicyLoaderError(ValueError):
    """Raised when a policy YAML cannot be adapted into a source-truth artifact."""


_REQUIRED_TOP_LEVEL = ("schema_version", "study", "form", "variables")


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
        records = list(variables)
    elif isinstance(variables, Mapping):
        records = [{"variable_id": k, **v} for k, v in variables.items()]
    else:
        raise PolicyLoaderError(f"{path}: 'variables' must be a list or mapping")

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
