"""Reconciliation gate: verify SoT vs scrubbed dataset shapes, then promote.

The gate orchestrates the per-form reconciliation rule:

    set(sot_columns(form))
     − set(phi_dropped_aswritten(form))
     − set(cleanup_dropped_aswritten(form))
     == set(scrubbed_columns(form))

This is a **schema-promotion gate, not a JSONL-promotion gate.** The
extraction pipeline publishes scrubbed JSONLs to
``output/{study}/trio_bundle/datasets/`` and clears the staging
``tmp/`` directory *before* this gate runs. So when reconciliation
fails, the JSONLs already sit in their published location — only
``dataset_schema.json`` is held back from GREEN. Downstream consumers
that read the trio_bundle directly will still see the scrubbed JSONLs
even on a failed run; only the canonical schema file remains
unchanged.

Outcomes:
    - All forms pass:   exit code 0. The staging dataset_schema is then
                        promoted from
                        ``output/{study}/staging/llm_source/phi_handled_dataset_schema.json``
                        to
                        ``output/{study}/llm_source/dataset_schema.json``
                        atomically (when the staging file exists; if it
                        does not — e.g. the build coordinator was run
                        without ``--column-inventory`` — promotion is
                        skipped with a warning, but the gate still passes).
    - Any form fails:   exit code 2. A per-form discrepancy file is written
                        to ``tmp/{study}/human_review/<form>_discrepancies.json``.
                        No promotion happens — the schema stays in staging.
    - No scrubbed data: exit code 0 with a clear log message — the developer
                        ran the build before scrub. This is by design so
                        ``make build-llm-source`` does not break in early
                        bootstrapping. No promotion happens (there is
                        nothing verified to promote).
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.extraction.io import atomic_write_json
from scripts.source_truth.cross_verify_pipeline import run as run_cross_verify
from scripts.source_truth.gate_checks import (
    GateFinding,
    check_c_phi_ledger_alignment,
    check_d_phi_action_mismatch,
    check_g_phi_dropped_vars_absent,
)
from scripts.source_truth.ledger_readers import (
    load_cleanup_dropped_columns,
    load_phi_dropped_columns,
)
from scripts.source_truth.policy_loader import (
    DuplicateFormNameError,
    validate_unique_form_names,
)
from scripts.source_truth.reconciliation import (
    ReconciliationResult,
    load_scrubbed_columns,
    load_sot_columns,
    reconcile,
)

logger = logging.getLogger(__name__)

__all__ = ["AuditEnvelopeCorruptError", "main", "run_verification"]


class AuditEnvelopeCorruptError(RuntimeError):
    """Raised when an audit-envelope JSON file fails to parse.

    Distinct from the ``file does not exist`` case (which is benign and
    returns ``{}``). A corrupt envelope is a data-integrity failure: the
    gate must refuse to proceed against partial data and exit cleanly
    rather than dying with a stack trace.
    """


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

    Raises:
        AuditEnvelopeCorruptError: file exists but its contents fail to
            parse as JSON. Logged at error level with the file path and
            parse error so the gate fails cleanly instead of dying with
            a bare stack trace.
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "verify-and-promote: malformed audit envelope at %s: %s",
            path,
            exc,
        )
        raise AuditEnvelopeCorruptError(
            f"malformed audit envelope at {path}: {exc}"
        ) from exc


def _staging_has_jsonl(datasets_dir: Path) -> bool:
    datasets_dir = Path(datasets_dir)
    if not datasets_dir.is_dir():
        return False
    return any(datasets_dir.glob("*.jsonl"))


def _promote_dataset_schema(
    *,
    output_root: Path,
    staging_dir: Path,
) -> int:
    """Atomically promote the staging dataset_schema to ``llm_source/``.

    Reads ``staging_dir / "phi_handled_dataset_schema.json"`` and
    atomically writes it to
    ``<output_root>/llm_source/dataset_schema.json``. The destination
    filename is the canonical ``dataset_schema.json`` — the
    ``phi_handled_`` prefix was a staging-zone marker and is stripped on
    promotion.

    Returns:
        - ``0`` on a successful promotion *or* when the staging file
          simply does not exist (e.g. the build coordinator was run
          without ``--column-inventory``). The latter is a benign skip:
          a warning is logged and the gate is not failed.
        - ``2`` when the staging file exists but is corrupt/unreadable
          as JSON. Promotion does not proceed; this is a hard error.

    Args:
        output_root: Per-study output root (e.g. ``output/Indo-VAP``).
        staging_dir: Directory holding the staged schema file
            (e.g. ``config.TMP_DIR / study / "staging" / "llm_source"``).
    """
    staging_schema_path = staging_dir / "phi_handled_dataset_schema.json"
    promoted_path = output_root / "llm_source" / "dataset_schema.json"

    if not staging_schema_path.is_file():
        logger.warning(
            "verify-and-promote: staging dataset_schema not found at %s — "
            "skipping promotion. (Run the build coordinator with "
            "--column-inventory to emit it.)",
            staging_schema_path,
        )
        return 0

    try:
        payload = json.loads(staging_schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error(
            "verify-and-promote: cannot promote — staging dataset_schema at %s "
            "is malformed JSON: %s",
            staging_schema_path,
            exc,
        )
        return 2

    atomic_write_json(promoted_path, payload)
    logger.info(
        "verify-and-promote: PROMOTED dataset_schema.json from staging to llm_source/"
    )
    return 0


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
        staging_root: [Deprecated — Phase 5b] Retained for API back-compat
            but no longer consulted. Scrubbed JSONLs are read directly from
            ``output_root/llm_source/dataset_schema/files/``.
        scrub_report_path: Path to ``phi_scrub_report.json``.
        cleanup_report_path: Path to ``dataset_cleanup_report.json``.
        output_root: Per-study output root (e.g. ``output/Indo-VAP``); the
            gate writes failures to ``tmp/{study}/human_review/``.

    Returns:
        - ``0`` when all forms pass *or* there is no scrubbed data to
          reconcile (graceful skip).
        - ``2`` when at least one form fails. Per-form discrepancy files
          are written under ``tmp/{study}/human_review/``.

    Graceful-skip asymmetry (intentional):
        Empty staging exits 0 (graceful). Empty SoT exits 2 (error).
        Empty staging is benign — scrub hasn't run; the gate simply
        skips. Empty SoT is a misconfiguration: the build coordinator
        and the Makefile both gate on SoT presence, so reaching this
        code path with no policies indicates an environmental problem,
        not a workflow stage.
    """
    sot_dir = Path(sot_dir)
    staging_root = Path(staging_root)
    scrub_report_path = Path(scrub_report_path)
    cleanup_report_path = Path(cleanup_report_path)
    output_root = Path(output_root)

    # Phase 5b: canonical scrubbed JSONL location is the flat directory
    # ``output/<study>/llm_source/dataset_schema/files/<form>.jsonl`` —
    # no ``datasets/`` subdir, no ``trio_bundle/``. The legacy
    # ``staging_root`` parameter is retained for CLI/API back-compat but
    # is no longer consulted for JSONL reads.
    datasets_dir = output_root / "llm_source" / "dataset_schema" / "files"

    if not sot_dir.is_dir():
        logger.error("SoT directory does not exist: %s — aborting verification", sot_dir)
        return 2

    policy_paths = sorted(sot_dir.glob("*_policy.yaml"))
    if not policy_paths:
        logger.error("no *_policy.yaml files in %s — aborting verification", sot_dir)
        return 2

    if not _staging_has_jsonl(datasets_dir):
        logger.info(
            "verify-and-promote: SKIP — no scrubbed JSONLs at %s. "
            "Reconciliation requires the scrub leg to have run first; "
            "this is expected if `make build-llm-source` runs before scrub. "
            "study=%s",
            datasets_dir,
            study,
        )
        return 0

    try:
        phi_report = _load_json_or_empty(scrub_report_path)
        cleanup_report = _load_json_or_empty(cleanup_report_path)
    except AuditEnvelopeCorruptError:
        # _load_json_or_empty already logged the file path + parse error.
        # Refuse to proceed against partial data; emit a clear summary line
        # and return the gate-failure exit code.
        logger.error(
            "verify-and-promote: aborting — audit envelope corruption detected; "
            "fix or regenerate the audit file and re-run."
        )
        return 2

    # Load policies and build source→form map. Duplicate ``form:`` declarations
    # across policy YAMLs are rejected eagerly via the shared
    # ``validate_unique_form_names`` helper (same invariant enforced by the
    # build coordinator). Otherwise discrepancy files would silently clobber
    # each other later in the run.
    policy_artifacts: list[dict[str, Any]] = []
    policy_sources: list[Path] = []
    source_to_form: dict[str, str] = {}
    for policy_path in policy_paths:
        artifact = _load_yaml(policy_path)
        if not isinstance(artifact.get("form"), str):
            logger.warning("Skipping malformed policy (no `form`): %s", policy_path)
            continue
        form_name = artifact["form"]
        policy_artifacts.append(artifact)
        policy_sources.append(policy_path)
        source = artifact.get("source") or {}
        dataset_file = source.get("dataset_file") if isinstance(source, dict) else None
        if isinstance(dataset_file, str) and dataset_file:
            source_to_form[dataset_file] = form_name

    try:
        validate_unique_form_names(policy_artifacts, sources=policy_sources)
    except DuplicateFormNameError as exc:
        logger.error("verify-and-promote: %s", exc)
        raise

    phi_drops = load_phi_dropped_columns(phi_report)
    cleanup_drops = load_cleanup_dropped_columns(
        cleanup_report,
        source_to_form=source_to_form,
    )

    # ---------------------------------------------------------------------------
    # Gate checks C, D, G — PHI ledger alignment pre-flight
    # ---------------------------------------------------------------------------
    declared_ledger_path = output_root / "audit" / "phi_handling_ledger.declared.json"
    as_written_ledger_path = output_root / "audit" / "phi_handling_ledger.as_written.json"

    try:
        declared_ledger = _load_json_or_empty(declared_ledger_path)
        as_written_ledger = _load_json_or_empty(as_written_ledger_path)
    except AuditEnvelopeCorruptError:
        logger.error(
            "verify-and-promote: aborting — PHI ledger file corruption detected; "
            "fix or regenerate the ledger files and re-run."
        )
        return 2

    declared_entries: list[dict] = declared_ledger.get("entries", [])
    as_written_events: list[dict] = as_written_ledger.get("events", [])

    scrubbed_cols_by_form: dict[str, frozenset[str]] = {}
    for _form in {e["form"] for e in as_written_events}:
        _cols = load_scrubbed_columns(_form, datasets_dir)
        if _cols is not None:
            scrubbed_cols_by_form[_form] = _cols

    preflight_findings: list[GateFinding] = []
    preflight_findings.extend(check_c_phi_ledger_alignment(declared_entries, as_written_events))
    preflight_findings.extend(check_d_phi_action_mismatch(declared_entries, as_written_events))
    preflight_findings.extend(check_g_phi_dropped_vars_absent(as_written_events, scrubbed_cols_by_form))

    if preflight_findings:
        preflight_path = output_root / "audit" / "preflight_mismatch.json"
        preflight_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            preflight_path,
            {
                "generated_utc": _now_utc_iso(),
                "finding_count": len(preflight_findings),
                "findings": [
                    {
                        "check": f.check,
                        "form": f.form,
                        "variable_id": f.variable_id,
                        "issue": f.issue,
                    }
                    for f in preflight_findings
                ],
            },
        )
        for f in preflight_findings:
            logger.error(
                "verify-and-promote: gate check %s — form=%s variable_id=%s: %s",
                f.check,
                f.form,
                f.variable_id,
                f.issue,
            )
        logger.error(
            "verify-and-promote: %d preflight finding(s); see %s",
            len(preflight_findings),
            preflight_path,
        )
        return 2

    # Surface cleanup-ledger entries that no policy claims. These would
    # otherwise vanish silently from the explanation set; the gate should
    # not fail on them (the policies that ARE present can still
    # reconcile), but a developer should see the warning.
    policy_form_names = {a["form"] for a in policy_artifacts}
    orphan_forms = [f for f in cleanup_drops if f not in policy_form_names]
    for orphan in sorted(orphan_forms):
        logger.warning(
            "cleanup ledger references unmatched form %r with %d dropped columns; "
            "explanation will not be applied",
            orphan,
            len(cleanup_drops[orphan]),
        )

    review_dir = config.TMP_DIR / study / "human_review"
    failures: list[ReconciliationResult] = []

    for artifact in policy_artifacts:
        form = artifact["form"]
        sot_cols = load_sot_columns(artifact)
        scrubbed_cols = load_scrubbed_columns(form, datasets_dir)
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

    # Promotion: gate has passed AND scrubbed data was actually verified
    # (we know this because we passed the ``_staging_has_jsonl`` check
    # above). Atomic write; corrupt staging schema → exit 2; missing
    # staging schema → warning + still pass.
    staging_dir = config.TMP_DIR / study / "staging" / "llm_source"
    promote_code = _promote_dataset_schema(output_root=output_root, staging_dir=staging_dir)
    if promote_code != 0:
        return promote_code

    # Phase 5b: Phase 2 dual-write to ``trio_bundle/datasets/`` removed.
    # The scrub leg now publishes directly to
    # ``output/<study>/llm_source/dataset_schema/files/<form>.jsonl``
    # (computed above as ``datasets_dir``), so there is nothing to copy.

    # Phase 3: cross-verify (mid-pipeline, accumulate-don't-block).
    # Runs after dataset_schema/files/ is populated. Scanner-only mode by
    # default (no llm_call, no gh_runner). When the orchestrator wants live
    # LLM/gh, it wires those callables explicitly.
    try:
        run_cross_verify()
    except Exception as exc:  # noqa: BLE001 -- never block verify_and_promote on cross-verify failure
        logger.warning("cross_verify_pipeline.failed err=%s", exc)

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
        help=(
            "[DEPRECATED — Phase 5b] Retained for CLI back-compat. "
            "run_verification now reads scrubbed JSONLs directly from "
            "output/<study>/llm_source/dataset_schema/files/, regardless "
            "of this value."
        ),
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
    output_root = args.output_root or (config.OUTPUT_DIR / study)
    # Phase 5b: canonical scrubbed JSONL location is
    # ``output/<study>/llm_source/dataset_schema/files/<form>.jsonl``.
    # The ``staging_root`` parameter is retained for CLI back-compat but
    # is unused by ``run_verification`` — the path is now derived from
    # ``output_root`` directly.
    staging_root = args.staging_root or (output_root / "llm_source" / "dataset_schema" / "files")
    scrub_report = args.scrub_report or (output_root / "audit" / "phi_scrub_report.json")
    cleanup_report = args.cleanup_report or (output_root / "audit" / "dataset_cleanup_report.json")

    try:
        return run_verification(
            study=study,
            sot_dir=sot_dir,
            staging_root=staging_root,
            scrub_report_path=scrub_report,
            cleanup_report_path=cleanup_report,
            output_root=output_root,
        )
    except ValueError as exc:
        # Configuration errors (e.g. duplicate form names across policy
        # YAMLs) are gate failures, not crashes — return exit code 2.
        logger.error("verify-and-promote: aborting — %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
