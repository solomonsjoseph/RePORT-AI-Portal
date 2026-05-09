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

import config

from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.concept_derivation import derive_concept_index
from scripts.source_truth.concepts import (
    build_concept_index,
    enrich_concept_index_with_schema,
)
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.evidence_pack_splitter import split_catalog_artifact
from scripts.source_truth.builder import DERIVATION_CLEANUP_LEDGER, DERIVATION_PHI_LEDGER
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


def _aggregate_declared_ledgers(
    policy_artifacts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate per-form declared ledger entries into study-wide ledgers.

    Ledger builders accept the full policy artifact unchanged; they read
    only the subtrees they consume (``records`` and, when provided,
    runtime-event lists) and ignore unrelated top-level keys such as
    ``option_sets``.
    """
    phi_entries: list[dict[str, Any]] = []
    cleanup_entries: list[dict[str, Any]] = []
    for art in policy_artifacts:
        phi = build_phi_handling_ledger(art)
        cleanup = build_dataset_cleanup_ledger(art)
        phi_entries.extend(phi.get("entries") or [])
        cleanup_entries.extend(cleanup.get("entries") or [])
    return (
        {"artifact_type": "phi_handling_ledger", "kind": "declared", "entries": phi_entries},
        {"artifact_type": "dataset_cleanup_ledger", "kind": "declared", "entries": cleanup_entries},
    )


def _build_new_phi_declared_entries(
    policy_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build new-schema PHI declared entries from all policy artifacts.

    Includes one entry per record whose ``derivation_targets`` contains
    ``DERIVATION_PHI_LEDGER``.  Fields are sourced directly from the
    translated policy artifact; no runtime data is included (``count``
    is always ``None``).
    """
    entries: list[dict[str, Any]] = []
    for art in policy_artifacts:
        form = art.get("form")
        source = art.get("source") or {}
        dataset_file = source.get("dataset_file")
        pdf_source = source.get("pdf_file")
        for record in art.get("records") or []:
            if DERIVATION_PHI_LEDGER not in record.get("derivation_targets", []):
                continue
            normalized = record.get("normalized") or {}
            sensitivity_flags = normalized.get("sensitivity_flags") or []
            entries.append(
                {
                    "form": form,
                    "variable_id": record.get("variable_id"),
                    "action": normalized.get("handling_action"),
                    "rule": {
                        "taxonomy": None,
                        "project_category": sensitivity_flags[0] if sensitivity_flags else None,
                    },
                    "rationale": normalized.get("handling_reason"),
                    "where": {
                        "dataset_file": dataset_file,
                        "pdf_source": pdf_source,
                    },
                    "count": None,
                }
            )
    return entries


def _build_new_cleanup_declared_entries(
    policy_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build new-schema dataset cleanup declared entries from all policy artifacts.

    Includes one entry per record whose ``derivation_targets`` contains
    ``DERIVATION_CLEANUP_LEDGER``.  SoT only declares column-level drops,
    so ``action`` is always ``"dataset_column_drop"`` and
    ``rule.project_category`` is always ``"cleanup"``.
    ``where.pdf_source`` is always ``None`` (cleanup is dataset-only).
    """
    entries: list[dict[str, Any]] = []
    for art in policy_artifacts:
        form = art.get("form")
        source = art.get("source") or {}
        dataset_file = source.get("dataset_file")
        for record in art.get("records") or []:
            if DERIVATION_CLEANUP_LEDGER not in record.get("derivation_targets", []):
                continue
            normalized = record.get("normalized") or {}
            entries.append(
                {
                    "form": form,
                    "variable_id": record.get("variable_id"),
                    "action": "dataset_column_drop",
                    "rule": {
                        "taxonomy": None,
                        "project_category": "cleanup",
                    },
                    "rationale": normalized.get("handling_reason"),
                    "where": {
                        "dataset_file": dataset_file,
                        "pdf_source": None,
                    },
                    "count": None,
                }
            )
    return entries


def _aggregate_catalog(policy_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-form catalogs into a single study-wide catalog mapping.

    Cross-form duplicates (e.g. SUBJID appearing in every form) are resolved
    with first-form-wins semantics: the first policy artifact in iteration
    order whose emitted catalog declares a given ``variable_id`` produces the
    canonical record / evidence pack; subsequent forms' copies are skipped.

    The decision rule itself does not merge record content — semantic merging
    (taking max option_set, longest meaning, etc.) is risky. Instead, every
    canonical record and evidence pack gains an additive
    ``seen_in_forms: [sorted form names]`` field listing every form whose
    emitted catalog (compact_records OR evidence_packs) declared that
    variable_id. For variables that appear in only one form, the list has
    exactly one element. For cross-form duplicates, the list has all forms.
    Downstream consumers can use this metadata to discover and reason about
    cross-form occurrence without altering the canonical record's content.
    """
    # Single pass over each policy artifact. For each form we capture the
    # emitted (compact_records, evidence_packs) along with the form name so
    # that we can (a) build a vid -> {forms} cross-form map and (b) perform
    # the first-form-wins aggregation in a deterministic second pass without
    # re-running the per-form catalog builder.
    per_form_outputs: list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]] = []
    vid_to_forms: dict[str, set[str]] = {}
    for art in policy_artifacts:
        form = art.get("form")
        if not isinstance(form, str) or not form:
            raise BuildCoordinatorError(
                "policy artifact missing required 'form' for catalog aggregation"
            )
        per_form = build_catalog_artifact(art)
        compact_only, packs = split_catalog_artifact(per_form)
        per_form_outputs.append((form, compact_only, packs))
        for record in compact_only.get("compact_records") or []:
            vid_to_forms.setdefault(record["variable_id"], set()).add(form)
        for vid in packs.keys():
            vid_to_forms.setdefault(vid, set()).add(form)

    aggregated_compact: list[dict[str, Any]] = []
    aggregated_packs: list[dict[str, Any]] = []
    seen_compact_vids: set[str] = set()
    seen_pack_vids: set[str] = set()
    for _form, compact_only, packs in per_form_outputs:
        for record in compact_only.get("compact_records") or []:
            vid = record["variable_id"]
            if vid in seen_compact_vids:
                continue  # cross-form duplicate (e.g. SUBJID); first form wins
            seen_compact_vids.add(vid)
            enriched = dict(record)
            enriched["seen_in_forms"] = sorted(vid_to_forms[vid])
            aggregated_compact.append(enriched)
        for vid, pack in packs.items():
            if vid in seen_pack_vids:
                continue  # cross-form duplicate; first form wins
            seen_pack_vids.add(vid)
            enriched_pack = dict(pack)
            enriched_pack["seen_in_forms"] = sorted(vid_to_forms[vid])
            aggregated_packs.append(enriched_pack)
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
    # Intermediate staging lives under tmp/<study>/ per spec §6.4.
    staging_root = config.TMP_DIR / study / "staging"
    staging_llm_source_dir = staging_root / "llm_source"
    staging_audit_dir = staging_root / "audit"
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

    phi_entries = _build_new_phi_declared_entries(policy_artifacts)
    cleanup_entries = _build_new_cleanup_declared_entries(policy_artifacts)
    _write_canonical_json(
        audit_dir / "phi_handling_ledger.declared.json",
        {"artifact_type": "phi_handling_ledger", "kind": "declared", "entries": phi_entries},
    )
    _write_canonical_json(
        audit_dir / "dataset_cleanup_ledger.declared.json",
        {"artifact_type": "dataset_cleanup_ledger", "kind": "declared", "entries": cleanup_entries},
    )
    summary["emitted"].extend([
        "audit/phi_handling_ledger.declared.json",
        "audit/dataset_cleanup_ledger.declared.json",
    ])

    concept_index = _validated_concept_index(policy_artifacts)
    _write_canonical_json(concept_dir / "concept_index.json", concept_index)
    summary["emitted"].append("llm_source/concept/concept_index.json")

    # Defensive cleanup: Phase 2 of Plan B moved concept_index.json from the
    # flat ``llm_source/`` path to the nested ``llm_source/concept/`` path.
    # On developer machines that built a previous revision, a stale flat
    # artifact can persist and silently shadow the new nested one for any
    # downstream consumer that still resolves the legacy path. Unlink the
    # legacy flat path AFTER the new nested write succeeds so a failed
    # nested write does not also wipe the previous good copy.
    (llm_source_dir / "concept_index.json").unlink(missing_ok=True)

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

        # Defensive cleanup: same rationale as the llm_source/ unlink above —
        # the staging mirror also previously emitted a flat
        # ``staging/llm_source/concept_index.json`` before Phase 2 nested it
        # under ``concept/``. Remove the legacy flat staging artifact only
        # after the new nested staging write succeeds.
        (staging_llm_source_dir / "concept_index.json").unlink(missing_ok=True)

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
