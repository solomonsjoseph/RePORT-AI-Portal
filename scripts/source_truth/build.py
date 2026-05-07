# scripts/source_truth/build.py
"""SoT-driven build coordinator.

CLI entrypoint that reads:
    - data/{study}/SoT/{form_id}_policy.yaml × N (manual SoT, frozen)
    - optional column inventory from dataset extraction

and emits to output/{study}/:
    - llm_source/study_metadata_catalog.json
    - llm_source/evidence_packs/{variable_id}.json
    - llm_source/concept/concept_index.json (initial — analysis_queryable=null)
    - audit/phi_handling_ledger.declared.json
    - audit/dataset_cleanup_ledger.declared.json

If column_inventory is provided, also emits to staging/llm_source/:
    - phi_handled_dataset_schema.json
    - concept/concept_index.json (enriched copy)

The concept index is now DERIVED structurally from the SoT policy
files — there is no longer a hand-authored study_concepts.yaml. See
``scripts.source_truth.concept_derivation`` for the derivation logic.

Manual policy YAMLs are not modified. PHI scrub, dedup, and consumer
chat tools are not invoked by this module.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.concept_derivation import derive_concept_index
from scripts.source_truth.concepts import (
    build_concept_index,
    enrich_concept_index_with_schema,
)
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.evidence_pack_splitter import split_catalog_artifact
from scripts.source_truth.ledgers import (
    build_dataset_cleanup_ledger,
    build_phi_handling_ledger,
)
from scripts.source_truth.policy_loader import (
    DuplicateFormNameError,
    load_policy_yaml,
    validate_unique_form_names,
)

logger = logging.getLogger(__name__)

__all__ = ["BuildCoordinatorError", "main", "run_build"]


class BuildCoordinatorError(RuntimeError):
    """Raised when the build coordinator cannot proceed."""


def _write_canonical_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
    path.write_text(encoded + "\n", encoding="utf-8")


def _strip_ledger_forbidden_keys(policy_artifact: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of the policy artifact with keys that the
    ledger builders reject (e.g. `option_sets`, which carries `values`
    sub-keys) removed.

    These keys belong in catalog/evidence-pack derivation paths but are
    not consumed by ledger builders.
    """
    forbidden_top_level = {"option_sets"}
    return {k: v for k, v in policy_artifact.items() if k not in forbidden_top_level}


def _aggregate_declared_ledgers(
    policy_artifacts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate per-form declared ledger entries into study-wide ledgers."""
    phi_entries: list[dict[str, Any]] = []
    cleanup_entries: list[dict[str, Any]] = []
    for art in policy_artifacts:
        ledger_input = _strip_ledger_forbidden_keys(art)
        phi = build_phi_handling_ledger(ledger_input)
        cleanup = build_dataset_cleanup_ledger(ledger_input)
        phi_entries.extend(phi.get("entries") or [])
        cleanup_entries.extend(cleanup.get("entries") or [])
    return (
        {"artifact_type": "phi_handling_ledger", "kind": "declared", "entries": phi_entries},
        {"artifact_type": "dataset_cleanup_ledger", "kind": "declared", "entries": cleanup_entries},
    )


def _aggregate_catalog(policy_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-form catalogs into a single study-wide catalog mapping.

    Cross-form duplicates (e.g. SUBJID appearing in every form) are resolved
    with first-form-wins semantics. The merge semantics for conflicting records
    is a Plan-A.1 follow-up — for now we skip subsequent forms' copies silently.
    """
    aggregated_compact: list[dict[str, Any]] = []
    aggregated_packs: list[dict[str, Any]] = []
    seen_vids: set[str] = set()
    for art in policy_artifacts:
        per_form = build_catalog_artifact(art)
        compact_only, packs = split_catalog_artifact(per_form)
        for record in compact_only.get("compact_records") or []:
            vid = record["variable_id"]
            if vid in seen_vids:
                continue  # cross-form duplicate (e.g. SUBJID); first form wins
            seen_vids.add(vid)
            aggregated_compact.append(record)
        for vid, pack in packs.items():
            if any(p.get("variable_id") == vid for p in aggregated_packs):
                continue  # cross-form duplicate; first form wins
            aggregated_packs.append(pack)
    return {
        "artifact_type": "study_metadata_catalog",
        "compact_records": aggregated_compact,
        "evidence_packs": aggregated_packs,
    }


def _load_column_inventory(path: Path) -> dict[str, list[str]]:
    """Return {form: [column, ...]} from the extraction column inventory JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    forms = raw.get("forms") or {}
    return {form: list(body.get("columns") or []) for form, body in forms.items()}


def _build_combined_dataset_schema(
    policy_artifacts: list[dict[str, Any]],
    inventory: dict[str, list[str]],
) -> dict[str, Any]:
    """Build a study-wide dataset schema by combining per-form builds."""
    entries: list[dict[str, Any]] = []
    for art in policy_artifacts:
        form = art["form"]
        cols = inventory.get(form, [])
        per_form = build_dataset_schema(art, dataset_columns=cols)
        entries.extend(per_form.get("entries") or [])
    return {"artifact_type": "study_dataset_schema", "entries": entries}


def _validated_concept_index(policy_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the concept index and validate every member_variable resolves.

    Re-uses ``build_concept_index`` for its (form, variable_id) cross-check
    against the policy artifacts. Any unresolved member would indicate a
    derivation bug.
    """
    derived = derive_concept_index(policy_artifacts)
    # build_concept_index expects an outer mapping with the same
    # schema_version/policy_status/study + section keys; the derived
    # output already has that shape, so we can pass it through directly.
    validated = build_concept_index(derived, policy_artifacts=policy_artifacts)
    return validated


def run_build(
    *,
    study: str,
    policies_dir: Path,
    output_root: Path,
    column_inventory: Path | None,
) -> dict[str, Any]:
    """Run the full Branch Y emission and (if column inventory present) Stage 2.

    The concept index is derived structurally from the SoT policy artifacts
    via ``concept_derivation.derive_concept_index``; there is no longer a
    hand-authored ``study_concepts.yaml``.

    Returns a summary dict listing emitted file paths and counts.
    """
    if not policies_dir.is_dir():
        raise BuildCoordinatorError(f"policies_dir does not exist: {policies_dir}")

    llm_source_dir = output_root / "llm_source"
    audit_dir = output_root / "audit"
    staging_llm_source_dir = output_root / "staging" / "llm_source"
    staging_audit_dir = output_root / "staging" / "audit"
    evidence_pack_dir = llm_source_dir / "evidence_packs"
    concept_dir = llm_source_dir / "concept"
    staging_concept_dir = staging_llm_source_dir / "concept"

    for directory in (
        llm_source_dir,
        audit_dir,
        staging_llm_source_dir,
        staging_audit_dir,
        evidence_pack_dir,
        concept_dir,
        staging_concept_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "study": study,
        "output_root": str(output_root),
        "emitted": [],
    }

    policy_paths = sorted(policies_dir.glob("*_policy.yaml"))
    if not policy_paths:
        raise BuildCoordinatorError(f"no *_policy.yaml files in {policies_dir}")
    policy_artifacts = [load_policy_yaml(p) for p in policy_paths]

    # Reject duplicate ``form:`` declarations BEFORE any aggregation runs.
    # Two policy YAMLs sharing the same form name would silently clobber each
    # other in the aggregated catalog, ledgers, and concept index. This is the
    # same invariant enforced by the verify-and-promote gate; both call sites
    # share the helper in ``policy_loader``.
    validate_unique_form_names(policy_artifacts, sources=list(policy_paths))

    aggregated = _aggregate_catalog(policy_artifacts)
    compact_only, packs = split_catalog_artifact(aggregated)

    _write_canonical_json(llm_source_dir / "study_metadata_catalog.json", compact_only)
    summary["emitted"].append("llm_source/study_metadata_catalog.json")

    for vid, pack in packs.items():
        _write_canonical_json(evidence_pack_dir / f"{vid}.json", pack)
    summary["emitted"].append(f"llm_source/evidence_packs/*.json (count={len(packs)})")

    summary["forms_loaded"] = [art["form"] for art in policy_artifacts]
    summary["compact_record_count"] = len(compact_only.get("compact_records") or [])
    summary["evidence_pack_count"] = len(packs)

    phi_declared, cleanup_declared = _aggregate_declared_ledgers(policy_artifacts)
    _write_canonical_json(audit_dir / "phi_handling_ledger.declared.json", phi_declared)
    _write_canonical_json(audit_dir / "dataset_cleanup_ledger.declared.json", cleanup_declared)
    summary["emitted"].extend([
        "audit/phi_handling_ledger.declared.json",
        "audit/dataset_cleanup_ledger.declared.json",
    ])

    concept_index = _validated_concept_index(policy_artifacts)
    _write_canonical_json(concept_dir / "concept_index.json", concept_index)
    summary["emitted"].append("llm_source/concept/concept_index.json")

    if column_inventory is not None:
        if not column_inventory.is_file():
            raise BuildCoordinatorError(f"column_inventory not found: {column_inventory}")
        inventory = _load_column_inventory(column_inventory)
        dataset_schema = _build_combined_dataset_schema(policy_artifacts, inventory)
        _write_canonical_json(
            staging_llm_source_dir / "phi_handled_dataset_schema.json",
            dataset_schema,
        )
        summary["emitted"].append(
            "staging/llm_source/phi_handled_dataset_schema.json"
        )

        enriched = enrich_concept_index_with_schema(concept_index, dataset_schema=dataset_schema)
        _write_canonical_json(staging_concept_dir / "concept_index.json", enriched)
        summary["emitted"].append("staging/llm_source/concept/concept_index.json")

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.source_truth.build")
    parser.add_argument("--study", required=True)
    parser.add_argument("--policies-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--column-inventory", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = run_build(
            study=args.study,
            policies_dir=args.policies_dir,
            output_root=args.output_root,
            column_inventory=args.column_inventory,
        )
    except BuildCoordinatorError as exc:
        logger.error("build failed: %s", exc)
        return 2
    except DuplicateFormNameError as exc:
        logger.error("build failed: %s", exc)
        return 2

    logger.info("build summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
