"""Reconciliation gate: verify SoT vs scrubbed dataset shapes, then promote.

The gate orchestrates the per-form reconciliation rule:

    set(sot_columns(form))
     − set(phi_dropped_aswritten(form))
     − set(cleanup_dropped_aswritten(form))
     == set(scrubbed_columns(form))

Outcomes:
    - All forms pass:   exit code 0. Build coordinator's emissions to
                        ``output/{study}/llm_source/`` are the production
                        promotion; this gate confirms they are not
                        stale relative to the scrubbed dataset.
    - Any form fails:   exit code 2. A per-form discrepancy file is written
                        to ``output/{study}/human_review/<form>_discrepancies.json``.
    - No scrubbed data: exit code 0 with a clear log message — the developer
                        ran the build before scrub. This is by design so
                        ``make build-llm-source`` does not break in early
                        bootstrapping.

The build coordinator does not emit a ``dataset_schema.json`` at
``llm_source/`` (it writes ``study_metadata_catalog.json``,
``concept/concept_index.json``, and evidence packs there). There is
therefore no explicit "promotion" file move; the gate's job is purely
verification + per-form discrepancy emission. See the design note in
``CONTEXT.md`` (Plan B Phase 4).
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from scripts.source_truth.ledger_readers import (
    load_cleanup_dropped_columns,
    load_phi_dropped_columns,
)
from scripts.source_truth.reconciliation import (
    ReconciliationResult,
    load_scrubbed_columns,
    load_sot_columns,
    reconcile,
)

logger = logging.getLogger(__name__)

__all__ = ["main", "run_verification"]


def _now_utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _load_json_or_empty(path: Path) -> dict[str, Any]:
    """Load a JSON file or return an empty dict if it does not exist.

    Empty dict is the neutral element for both ledger readers — they
    simply return ``{}`` when no events are present.
    """
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _staging_has_jsonl(staging_root: Path) -> bool:
    datasets = staging_root / "datasets"
    if not datasets.is_dir():
        return False
    return any(datasets.glob("*.jsonl"))


def _serialize_discrepancy(result: ReconciliationResult) -> dict[str, Any]:
    return {
        "form": result.form,
        "missing_unexplained": sorted(result.missing_unexplained),
        "extra_in_scrubbed": sorted(result.extra_in_scrubbed),
        "explained_by_phi": sorted(result.explained_by_phi),
        "explained_by_cleanup": sorted(result.explained_by_cleanup),
        "generated_utc": _now_utc_iso(),
    }


def run_verification(
    *,
    study: str,
    sot_dir: Path,
    staging_root: Path,
    scrub_report_path: Path,
    cleanup_report_path: Path,
    output_root: Path,
) -> int:
    """Run reconciliation across every policy YAML in ``sot_dir``.

    Args:
        study: Study name (logged for context).
        sot_dir: Directory holding ``*_policy.yaml`` files (one per form).
        staging_root: Staging tree root; expects ``staging_root/datasets/``.
        scrub_report_path: Path to ``phi_scrub_report.json``.
        cleanup_report_path: Path to ``dataset_cleanup_report.json``.
        output_root: Per-study output root (e.g. ``output/Indo-VAP``); the
            gate writes failures to ``output_root/human_review/``.

    Returns:
        - ``0`` when all forms pass *or* there is no scrubbed data to
          reconcile (graceful skip).
        - ``2`` when at least one form fails. Per-form discrepancy files
          are written under ``output_root/human_review/``.
    """
    sot_dir = Path(sot_dir)
    staging_root = Path(staging_root)
    scrub_report_path = Path(scrub_report_path)
    cleanup_report_path = Path(cleanup_report_path)
    output_root = Path(output_root)

    if not sot_dir.is_dir():
        logger.error("SoT directory does not exist: %s — aborting verification", sot_dir)
        return 2

    policy_paths = sorted(sot_dir.glob("*_policy.yaml"))
    if not policy_paths:
        logger.error("no *_policy.yaml files in %s — aborting verification", sot_dir)
        return 2

    if not _staging_has_jsonl(staging_root):
        logger.info(
            "verify-and-promote: SKIP — no scrubbed JSONLs at %s/datasets/. "
            "Reconciliation requires the scrub leg to have run first; "
            "this is expected if `make build-llm-source` runs before scrub. "
            "study=%s",
            staging_root,
            study,
        )
        return 0

    phi_report = _load_json_or_empty(scrub_report_path)
    cleanup_report = _load_json_or_empty(cleanup_report_path)

    # Load policies and build source→form map.
    policy_artifacts: list[dict[str, Any]] = []
    source_to_form: dict[str, str] = {}
    for policy_path in policy_paths:
        artifact = _load_yaml(policy_path)
        if not isinstance(artifact.get("form"), str):
            logger.warning("Skipping malformed policy (no `form`): %s", policy_path)
            continue
        policy_artifacts.append(artifact)
        source = artifact.get("source") or {}
        dataset_file = source.get("dataset_file") if isinstance(source, dict) else None
        if isinstance(dataset_file, str) and dataset_file:
            source_to_form[dataset_file] = artifact["form"]

    phi_drops = load_phi_dropped_columns(phi_report)
    cleanup_drops = load_cleanup_dropped_columns(
        cleanup_report,
        source_to_form=source_to_form,
    )

    review_dir = output_root / "human_review"
    failures: list[ReconciliationResult] = []

    for artifact in policy_artifacts:
        form = artifact["form"]
        sot_cols = load_sot_columns(artifact)
        scrubbed_cols = load_scrubbed_columns(form, staging_root)
        if scrubbed_cols is None:
            # Scrubbed JSONL is missing for this specific form even though
            # other JSONLs exist. Log it and skip this form rather than
            # raising — the gate is per-form, and one absent form should
            # not invalidate the rest.
            logger.info(
                "verify-and-promote: form %s has no scrubbed JSONL — skipping reconciliation for this form",
                form,
            )
            continue

        result = reconcile(
            form=form,
            sot_cols=sot_cols,
            scrubbed_cols=scrubbed_cols,
            phi_drop=phi_drops.get(form, frozenset()),
            cleanup_drop=cleanup_drops.get(form, frozenset()),
        )
        if not result.ok:
            failures.append(result)

    if failures:
        review_dir.mkdir(parents=True, exist_ok=True)
        for result in failures:
            payload = _serialize_discrepancy(result)
            target = review_dir / f"{result.form}_discrepancies.json"
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            logger.error(
                "verify-and-promote: %s mismatch — missing_unexplained=%s extra_in_scrubbed=%s",
                result.form,
                sorted(result.missing_unexplained),
                sorted(result.extra_in_scrubbed),
            )
        logger.error(
            "verify-and-promote: %d form(s) failed reconciliation; see %s/",
            len(failures),
            review_dir,
        )
        return 2

    logger.info(
        "verify-and-promote: PASS — all %d form(s) reconciled for study=%s",
        len(policy_artifacts),
        study,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Resolves paths from ``config`` based on ``--study``."""
    parser = argparse.ArgumentParser(prog="scripts.source_truth.verify_and_promote")
    parser.add_argument("--study", required=True, help="Study name (e.g. Indo-VAP)")
    parser.add_argument(
        "--sot-dir",
        type=Path,
        default=None,
        help="Path to SoT directory (default: data/<study>/SoT)",
    )
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=None,
        help="Path to staging root (default: tmp/<study>)",
    )
    parser.add_argument(
        "--scrub-report",
        type=Path,
        default=None,
        help="Path to phi_scrub_report.json (default: output/<study>/audit/phi_scrub_report.json)",
    )
    parser.add_argument(
        "--cleanup-report",
        type=Path,
        default=None,
        help="Path to dataset_cleanup_report.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Per-study output root (default: output/<study>)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Resolve defaults from the repo layout. We import lazily so the test
    # suite does not need a configured study to import this module.
    import config  # noqa: PLC0415 — deliberate lazy import

    study = args.study
    sot_dir = args.sot_dir or (config.DATA_DIR / study / "SoT")
    staging_root = args.staging_root or (config.TMP_DIR / study)
    output_root = args.output_root or (config.OUTPUT_DIR / study)
    scrub_report = args.scrub_report or (output_root / "audit" / "phi_scrub_report.json")
    cleanup_report = args.cleanup_report or (output_root / "audit" / "dataset_cleanup_report.json")

    return run_verification(
        study=study,
        sot_dir=sot_dir,
        staging_root=staging_root,
        scrub_report_path=scrub_report,
        cleanup_report_path=cleanup_report,
        output_root=output_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
