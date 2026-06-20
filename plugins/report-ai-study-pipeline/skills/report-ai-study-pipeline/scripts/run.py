#!/usr/bin/env python3
"""Orchestrator: the 10-phase host publish state machine (Wave 4 B3.7).

This is the top-level entry point of the consolidated pipeline — "the plugin IS
the pipeline". It holds the per-study lock for the whole run, drives the ordered
phases, records a durable ``run_state.json``, and supports the maintainer
human-review resume loop (``--resume-held``).

Topology (the conceptual 10 runtime phases map onto the supervisory steps this
orchestrator drives; the contiguous publish phases 2-7 are executed by the
proven ``dataset-to-llm-source`` publish supervisor in one locked subprocess):

    P0  preflight   — config validation, rulebook resolve + drift, input-
                      fingerprint redundant-run check, dir pre-creation, lock
    P1  headers     — header-extraction skill (column NAMES only; gates classify)
    P1b SoT         — generate_lean_outputs (policy/schema/joined under llm_source/SoT/)
    P2  publish     — dataset-to-llm-source `run` (classify → extract → scrub →
                      dedup → PHI guard gate → promote → destroy → inline verify
                      → snapshot), under the lock baton
    P8  cleanup     — cleanup_verifier over the published tree + cleanup ledgers
    P9  verify      — audit-verification skill (idempotent 14-assertion re-verify)
    P10 finalize    — record input fingerprint, finalize run_state, release lock

**Lock baton (risk #7).** The orchestrator holds the lock for the whole run and
hands a validated baton (``REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT`` +
``REPORTAL_PIPELINE_LOCK_PARENT_PID = our pid``) to every skill subprocess, so
they skip re-acquisition rather than racing the same flock. Assertion 11 was
taught to accept a valid parent baton.

Value-free: ``run_state.json`` carries phase names, statuses, exit codes, form
NAMES, and counts — never a row value.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.utils.skill_protocol import SkillResult, invoke_skill  # noqa: E402

RUN_STATE_SCHEMA = 1


@dataclass
class _PhaseRecord:
    phase: str
    status: str = "pending"  # pending | running | complete | held | failed | skipped
    detail: str = ""
    exit_code: int | None = None

    def to_json(self) -> dict:
        return {
            "phase": self.phase,
            "status": self.status,
            "detail": self.detail,
            "exit_code": self.exit_code,
        }


@dataclass
class _RunState:
    study: str
    run_id: str
    status: str = "in_progress"  # in_progress | complete | held | failed | skipped_redundant
    phases: list[_PhaseRecord] = field(default_factory=list)
    input_fingerprint: str | None = None
    snapshot_id: str | None = None
    held_forms: list[str] = field(default_factory=list)
    partial: bool = False
    path: Path | None = None

    def to_json(self) -> dict:
        return {
            "schema": RUN_STATE_SCHEMA,
            "study": self.study,
            "run_id": self.run_id,
            "status": self.status,
            "phases": [p.to_json() for p in self.phases],
            "input_fingerprint": self.input_fingerprint,
            "snapshot_id": self.snapshot_id,
            "held_forms": sorted(self.held_forms),
            "partial": self.partial,
        }

    def flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def phase(self, name: str) -> _PhaseRecord:
        rec = _PhaseRecord(phase=name, status="running")
        self.phases.append(rec)
        self.flush()
        return rec


def _baton_env(*, run_id: str, study: str) -> dict[str, str]:
    """Child env: lock baton (our pid) + shared run id + study."""
    env = dict(os.environ)
    env["REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT"] = "1"
    env["REPORTAL_PIPELINE_LOCK_PARENT_PID"] = str(os.getpid())
    env["REPORTAL_RUN_ID"] = run_id
    env["STUDY_NAME"] = study
    return env


def _preflight(state: _RunState, *, study: str, run_id: str, resume_held: bool, force: bool) -> int:
    """Phase 0: config validation, rulebook drift, redundant-run check, dirs."""
    import config

    rec = state.phase("P0:preflight")

    # Required inputs.
    forms_manifest = Path(config.study_config_path("_forms_manifest.yaml", study=study))
    study_privacy = Path(config.study_config_path("_study_privacy.yaml", study=study))
    datasets_dir = Path(config.RAW_DATA_DIR) / study / "datasets"
    missing = [
        label
        for label, present in (
            ("_forms_manifest.yaml", forms_manifest.is_file()),
            ("_study_privacy.yaml", study_privacy.is_file()),
            ("datasets/", datasets_dir.is_dir()),
        )
        if not present
    ]
    if missing:
        # Note 11/16: actionable guidance when the study config is absent.
        detail = f"missing inputs: {missing}"
        if any(m.endswith(".yaml") for m in missing):
            detail += (
                f" — no study config; run the study-setup wizard "
                f"(study-setup --study {study} --interactive) or add "
                f"config/{study}/_forms_manifest.yaml + _study_privacy.yaml"
            )
        rec.status, rec.detail, rec.exit_code = "failed", detail, 2
        state.flush()
        return 2

    config.ensure_run_directories(study=study, run_id=run_id)

    # Accumulation guard (Note 13): a surviving cleanup.in_progress token from a
    # prior run means a previous cleanup was interrupted mid-way — halt rather
    # than build on an unknown workspace state. --force overrides for operator
    # recovery (after `make rebuild-llm-source` clears the runs/ dir).
    if not force:
        from scripts.utils.run_context import (
            CLEANUP_RECOVERY_MESSAGE,
            scan_for_in_progress_cleanups,
        )

        stale = scan_for_in_progress_cleanups(Path(config.STUDY_OUTPUT_DIR) / "runs")
        if stale:
            print(CLEANUP_RECOVERY_MESSAGE.format(path=stale[0]), file=sys.stderr)
            rec.status = "failed"
            rec.detail = "interrupted cleanup token present"
            rec.exit_code = 6
            state.flush()
            return 6

    # Rulebook drift (advisory — never blocks).
    try:
        from scripts.security.phi_review import load_study_privacy_config
        from scripts.security.phi_rulebook import resolve_rulebook

        privacy = load_study_privacy_config(Path(config.RAW_DATA_DIR) / study)
        resolution = resolve_rulebook(privacy, allow_network=False)
        if resolution.drift_detected:
            print(
                f"WARNING: PHI rulebook drift detected for {study} "
                f"(baseline {resolution.baseline_sha256}); confirm the change is intended.",
                file=sys.stderr,
            )
    except Exception as exc:  # advisory only
        print(f"rulebook resolve skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Input-fingerprint redundant-run check (skipped on --resume-held / --force).
    from scripts.utils.input_fingerprint import (
        compute_input_fingerprint,
        fingerprint_record_path,
        is_redundant_run,
        read_recorded_fingerprint,
    )

    fp = compute_input_fingerprint(study=study)
    state.input_fingerprint = fp.fingerprint
    if not resume_held and not force:
        recorded = read_recorded_fingerprint(fingerprint_record_path(Path(config.STUDY_AUDIT_DIR)))
        if is_redundant_run(fp, recorded):
            # C5.5: identical inputs → activate the existing clean snapshot for
            # this fingerprint (point `current` at it) instead of re-running.
            detail = "inputs unchanged since last clean run (use --force to re-run)"
            try:
                from scripts.utils import snapshot as _snapshot

                existing = _snapshot.find_snapshot_by_fingerprint(study, fp.fingerprint)
                if existing is not None:
                    _snapshot.set_current_snapshot(study, existing)
                    state.snapshot_id = existing
                    detail = f"identical inputs detected, activating snapshot {existing}"
                    print(f"P0:preflight — {detail}", file=sys.stderr)
            except Exception as exc:  # advisory — short-circuit either way
                print(f"redundant-run snapshot activation skipped: {exc}", file=sys.stderr)
            rec.status = "skipped"
            rec.detail = detail
            rec.exit_code = 0
            state.status = "skipped_redundant"
            state.flush()
            return -1  # sentinel: redundant, short-circuit cleanly

    rec.status, rec.exit_code = "complete", 0
    state.flush()
    return 0


def _record_skill_phase(state: _RunState, name: str, result: SkillResult) -> _PhaseRecord:
    rec = _PhaseRecord(
        phase=name,
        status="complete" if result.ok else "failed",
        detail=result.summary,
        exit_code=result.exit_code,
    )
    state.phases.append(rec)
    state.flush()
    return rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the consolidated 10-phase host publish pipeline for a study."
    )
    parser.add_argument("--study", required=True, help="Study name (folder under data/raw/).")
    parser.add_argument("--run-id", dest="run_id", default=None, help="Override the run id.")
    parser.add_argument(
        "--force", action="store_true", help="Run even if inputs are unchanged (skip redundancy)."
    )
    parser.add_argument(
        "--resume-held",
        dest="resume_held",
        action="store_true",
        help="Maintainer: re-publish the full surviving set after resolving held forms.",
    )
    parser.add_argument("--max-workers", type=int, default=None, dest="max_workers")
    parser.add_argument("--form", action="append", default=None, dest="forms")
    parser.add_argument(
        "--strict-abort",
        action="store_true",
        help="Abort the whole study on the first un-scrubbable row (default: partial publish).",
    )
    parser.add_argument(
        "--skip-header-extraction",
        action="store_true",
        help="Skip the standalone header-extraction phase (publish reads headers anyway).",
    )
    args = parser.parse_args(argv)

    import config
    from scripts.skills.extract_to_llm_source import (
        EXIT_NEEDS_ADVICE,
        EXIT_OK,
        EXIT_PARTIAL_REVIEW,
    )
    from scripts.utils.input_fingerprint import (
        compute_input_fingerprint,
        fingerprint_record_path,
        write_fingerprint_record,
    )
    from scripts.utils.pipeline_lock import acquire_pipeline_lock, release_pipeline_lock
    from scripts.utils.run_context import resolve_run_id

    study = args.study
    os.environ.setdefault("STUDY_NAME", study)
    # ``config`` resolves all study-scoped paths (STUDY_OUTPUT_DIR, STUDY_AUDIT_DIR,
    # …) from STUDY_NAME at *import* time. If the ambient STUDY_NAME differs from
    # the ``--study`` we were asked to run, the orchestrator would write its
    # run_state.json / fingerprint / current-pointer under the wrong study tree
    # while the publish skill (which honours ``--study``) writes status.json under
    # the right one — so ``_absorb_status`` would silently read a non-existent
    # status.json and the snapshot/current-pointer wiring would no-op. Fail closed
    # with an actionable message rather than diverging silently. The production
    # entry point (`make study STUDY=<name>`) exports STUDY_NAME, so this passes.
    if study != config.STUDY_NAME:
        print(
            f"Refusing to run: --study is '{study}' but config resolved study "
            f"'{config.STUDY_NAME}' (from the STUDY_NAME env at import time). "
            f"Re-run with STUDY_NAME={study} set in the environment "
            f"(e.g. `make study STUDY={study}`).",
            file=sys.stderr,
        )
        return EXIT_NEEDS_ADVICE
    run_id = args.run_id or resolve_run_id()
    run_dir = Path(config.STUDY_OUTPUT_DIR) / "runs" / run_id

    state = _RunState(study=study, run_id=run_id)
    state.path = run_dir / "run_state.json"

    # ── Acquire the lock for the whole orchestrated run ───────────────────────
    try:
        acquire_pipeline_lock(study)
    except RuntimeError as exc:
        state.phases.append(
            _PhaseRecord("P0:lock", "failed", f"lock unavailable: {exc}", exit_code=6)
        )
        state.status = "failed"
        state.flush()
        print(f"Pipeline lock unavailable: {exc}", file=sys.stderr)
        return 6

    child_env = _baton_env(run_id=run_id, study=study)
    try:
        # ── P0 preflight ─────────────────────────────────────────────────────
        pf = _preflight(
            state, study=study, run_id=run_id, resume_held=args.resume_held, force=args.force
        )
        if pf == -1:  # redundant short-circuit
            print(f"Skipping {study}: inputs unchanged since the last clean run.")
            return EXIT_OK
        if pf != 0:
            state.status = "failed"
            state.flush()
            return pf

        # ── P1 header extraction (gate; column NAMES only) ───────────────────
        if not args.skip_header_extraction:
            hdr = invoke_skill(
                "header-extraction",
                ["--study", study, "--run-id", run_id, "--run-dir", str(run_dir)],
                env=child_env,
            )
            hrec = _record_skill_phase(state, "P1:header-extraction", hdr)
            if not hdr.ok:
                state.status = "failed"
                hrec.detail = hdr.summary
                state.flush()
                return hdr.exit_code or 1

        # ── P1c dictionary extraction (Note 1 — the orchestrator invokes the
        # dictionary-to-llm-source skill; its publish leg runs in-lock at Step 2
        # after cleanup-propagation prunes dropped columns) ───────────────────
        dict_ext = invoke_skill(
            "dictionary-to-llm-source",
            ["--study", study, "--run-id", run_id, "--run-dir", str(run_dir), "--leg", "extract"],
            env=child_env,
        )
        derec = _record_skill_phase(state, "P1c:dictionary-extract", dict_ext)
        if not dict_ext.ok:
            state.status = "failed"
            derec.detail = dict_ext.summary
            state.flush()
            return dict_ext.exit_code or 1

        # ── P2 raw-file deduplication (Note 4 — before SoT / extraction) ───
        dedup = invoke_skill(
            "dataset-deduplication",
            ["--study", study, "--run-id", run_id, "--run-dir", str(run_dir)],
            env=child_env,
        )
        drec = _record_skill_phase(state, "P2:dataset-deduplication", dedup)
        if not dedup.ok:
            state.status = "failed"
            drec.detail = dedup.summary
            state.flush()
            return dedup.exit_code or 1

        # ── P1b SoT lean outputs (joined views before publish gate) ─────────
        from scripts.source_truth.generate_lean_outputs import main as generate_lean_outputs_main

        sot_rec = state.phase("P1b:sot-lean-generate")
        sot_rc = generate_lean_outputs_main(["--study", study, "--repo-root", str(config.BASE_DIR)])
        sot_rec.exit_code = sot_rc
        if sot_rc != 0:
            sot_rec.status = "failed"
            state.status = "failed"
            state.flush()
            return sot_rc or 1
        sot_rec.status = "complete"

        # ── P2 publish (delegated supervisor under the baton) ────────────────
        publish_args = ["run", "--study", study]
        if args.max_workers is not None:
            publish_args += ["--max-workers", str(args.max_workers)]
        for form in args.forms or []:
            publish_args += ["--form", form]
        if args.resume_held:
            publish_args += ["--resume-held"]
        if args.strict_abort:
            child_env = dict(child_env, REPORTAL_SCRUB_STRICT_ABORT="1")

        # Note 13: defer the supervisor's Step-7 snapshot commit so the orchestrator
        # commits at P10, only after the cleanup (P8) + audit (P9) verifiers pass.
        # (A scrub-only-partial publish still commits inline — see Step 7.)
        publish_env = dict(child_env, REPORTAL_DEFER_SNAPSHOT_COMMIT="1")
        publish = invoke_skill("dataset-to-llm-source", publish_args, env=publish_env)
        prec = _record_skill_phase(state, "P2:publish", publish)

        # Surface held/partial state from the run's status.json (form names only).
        _absorb_status(state, run_dir)

        # Note 6: the header-extraction shared store has now been consumed by the
        # dedup (P2) and PHI-classification (inside the publish supervisor) legs.
        # Destroy it so the workspace ends in the two-list clean state (Note 13).
        try:
            from scripts.extraction.header_store import destroy_header_store

            destroy_header_store(run_dir)
        except Exception as exc:  # best-effort cleanup, never blocks the run
            print(f"P7: header-store destroy skipped: {exc}", file=sys.stderr)

        if publish.exit_code not in {EXIT_OK, EXIT_PARTIAL_REVIEW}:
            state.status = "failed"
            prec.status = "failed"
            state.flush()
            return publish.exit_code

        if publish.exit_code == EXIT_PARTIAL_REVIEW:
            # Published forms ARE published; held forms await maintainer review.
            prec.status = "held"
            state.status = "held"
            state.partial = True
            state.flush()
            print(
                f"Partial publish for {study}: {len(state.held_forms)} form(s) held for review. "
                f"Resolve, then re-run with --resume-held.",
                file=sys.stderr,
            )
            return EXIT_PARTIAL_REVIEW

        # ── P8 cleanup verifier (native): ledger consistency + two-list purge ──
        from dataclasses import asdict

        from scripts.extraction.io import atomic_write_json
        from scripts.utils.cleanup_verifier import verify_cleanup, verify_workspace_cleanup
        from scripts.utils.run_context import delete_cleanup_token, write_cleanup_token

        # Gap 7 token: written before the verifier, deleted only on a clean pass;
        # a surviving token on the next run signals an interrupted cleanup.
        write_cleanup_token(run_dir)
        crec = state.phase("P8:cleanup-verifier")
        # Ledger consistency: published JSONL lives under llm_source/dataset_schema/
        # files/ (config.TRIO_DATASETS_DIR), the same location assertion 10 checks.
        ledger_report = verify_cleanup(Path(config.STUDY_AUDIT_DIR), Path(config.TRIO_DATASETS_DIR))
        # Two-list workspace purge (Note 13): must-be-gone temporaries absent +
        # must-remain permanents present. The live cleanup token is held during the
        # walk, so it is excluded from the must-be-gone set.
        ws_report = verify_workspace_cleanup(
            study=study, run_dir=run_dir, expect_cleanup_token_present=True
        )
        ok = ledger_report.ok and ws_report.ok
        # Persist the names-only combined record to the audit zone (permanent).
        try:
            atomic_write_json(
                Path(config.STUDY_AUDIT_DIR) / "cleanup_verification_report.json",
                {
                    "run_id": run_id,
                    "ledger_ok": ledger_report.ok,
                    "ledger_findings": [asdict(f) for f in ledger_report.findings],
                    "workspace_ok": ws_report.ok,
                    "workspace_findings": [asdict(f) for f in ws_report.findings],
                    "checked_must_gone": ws_report.checked_must_gone,
                    "checked_must_remain": ws_report.checked_must_remain,
                },
            )
        except Exception as exc:  # advisory record; never fail the run on a write hiccup
            print(f"P8: cleanup_verification_report write skipped: {exc}", file=sys.stderr)
        n_find = len(ledger_report.findings) + len(ws_report.findings)
        crec.status = "complete" if ok else "failed"
        crec.detail = "clean" if ok else f"{n_find} cleanup finding(s)"
        crec.exit_code = 0 if ok else 1
        state.flush()
        if not ok:
            state.status = "failed"
            state.flush()
            return 5  # EXIT_VERIFIER_FAIL family — token LEFT in place (interrupted cleanup)
        delete_cleanup_token(run_dir)  # only after BOTH verifiers pass

        # ── P9 full verifier (idempotent re-verify under the baton) ──────────
        verify = invoke_skill(
            "audit-verification",
            ["--study", study, "--run-id", run_id],
            env=child_env,
        )
        vrec = _record_skill_phase(state, "P9:verify", verify)
        if not verify.ok:
            state.status = "failed"
            vrec.status = "failed"
            state.flush()
            return verify.exit_code or 5

        # ── P10 finalize ─────────────────────────────────────────────────────
        frec = state.phase("P10:finalize")
        fp = compute_input_fingerprint(study=study)
        write_fingerprint_record(fingerprint_record_path(Path(config.STUDY_AUDIT_DIR)), fp)
        state.input_fingerprint = fp.fingerprint
        # Commit the snapshot NOW (Note 13) — only after P8 (cleanup) + P9 (audit)
        # both passed. The supervisor deferred its Step-7 commit via
        # REPORTAL_DEFER_SNAPSHOT_COMMIT, so the snapshot is created only on a
        # fully-verified clean pass. cleanup_verifier_passed=True records the proof
        # in the manifest (Note 14). Fail-soft — a commit hiccup must not fail an
        # otherwise-complete run.
        try:
            from scripts.utils.snapshot import commit_run_snapshot

            commit_run_snapshot(
                study=study,
                run_id=run_id,
                run_dir=run_dir,
                resume_held=args.resume_held,
                cleanup_verifier_passed=True,
            )
        except Exception as exc:  # advisory: never fail a complete run on commit hiccup
            print(f"P10:finalize — snapshot commit skipped: {exc}", file=sys.stderr)
        _absorb_status(state, run_dir)  # pick up snapshot_id committed at P10
        # C5.3: phase-10 points `current` at the freshly committed snapshot so a
        # clean publish becomes the study's designated active one. Fail-soft — a
        # pointer-write hiccup must not fail an otherwise-complete run.
        if state.snapshot_id:
            try:
                from scripts.utils import snapshot as _snapshot

                _snapshot.set_current_snapshot(study, state.snapshot_id)
            except Exception as exc:  # advisory: never fail a complete run
                print(f"P10:finalize — current-pointer write skipped: {exc}", file=sys.stderr)
        frec.status, frec.exit_code = "complete", 0
        state.status = "complete"
        state.flush()
        print(f"Pipeline complete for {study} (run {run_id}).")
        return EXIT_OK
    finally:
        release_pipeline_lock(study)


def _absorb_status(state: _RunState, run_dir: Path) -> None:
    """Best-effort copy of held_forms / snapshot_id from the run's status.json."""
    status_path = run_dir / "status.json"
    if not status_path.is_file():
        return
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(status, dict):
        return
    held = status.get("held_forms")
    if isinstance(held, list) and held:
        state.held_forms = [str(h) for h in held]
    elif int(status.get("held_forms_count") or 0) > 0:
        # Older runs wrote counts only; merge SoT joined-view holds when present.
        sot_path = run_dir / "sot_joined_gate_outcome.json"
        try:
            if sot_path.is_file():
                sot_raw = json.loads(sot_path.read_text(encoding="utf-8"))
                if isinstance(sot_raw, dict) and sot_raw.get("run_id") == state.run_id:
                    sot_held = sot_raw.get("held_forms")
                    if isinstance(sot_held, list):
                        state.held_forms = sorted({str(h) for h in sot_held})
        except (OSError, ValueError):
            pass
    snap = status.get("snapshot_id")
    if isinstance(snap, str):
        state.snapshot_id = snap


if __name__ == "__main__":
    raise SystemExit(main())
