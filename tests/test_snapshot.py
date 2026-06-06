"""Tests for the immutable study-snapshot subsystem (W1).

Covered:

* write -> list -> load round-trip
* immutability — a second write with the same id raises
* deterministic id — same llm_source content + run_id -> same id
* manifest content hashes are stable / present
* ``.NO_LLM_ZONE`` sentinel at the snapshot root
* security zone: ``validate_agent_read`` REJECTS ``snapshots/{id}/`` and
  ``snapshots/{id}/phi_handling_approval.json``; PERMITS
  ``snapshots/{id}/llm_source/...`` once that subtree is selected
* the realpath snapshot-root guard exempts only ``<id>/llm_source/``

All filesystem state is under ``monkeypatch_config``'s tmp_path; no real study
data is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.ai_assistant.file_access import (
    ZoneViolationError,
    is_agent_readable,
    validate_agent_read,
)
from scripts.audit.zone_guards import (
    SnapshotZoneViolation,
    deny_if_snapshot_root,
)
from scripts.utils.snapshot import (
    MANIFEST_FILENAME,
    SnapshotError,
    SnapshotExistsError,
    SnapshotNotFoundError,
    list_snapshots,
    load_snapshot,
    select_snapshot_llm_source,
    snapshot_llm_source_path,
    snapshot_path,
    snapshots_root,
    write_snapshot,
)

RUN_ID = "run_testfixedid0001"


# ── helpers ────────────────────────────────────────────────────────────────


def _live_llm_source(study: str) -> Path:
    """The LIVE llm_source tree ``write_snapshot`` reads — derived from the study
    arg via ``config.OUTPUT_DIR`` (review #3), NOT from the (UI-repointable)
    module-global ``config.STUDY_LLM_SOURCE_DIR``."""
    return Path(config.OUTPUT_DIR) / study / "llm_source"


def _seed_llm_source(root: Path, *, marker: str = "alpha") -> None:
    """Populate an llm_source tree at *root* with scrubbed files."""
    ds = root / "dataset_schema" / "files"
    dd = root / "dictionary_mapping" / "jsonl"
    ds.mkdir(parents=True, exist_ok=True)
    dd.mkdir(parents=True, exist_ok=True)
    (ds / "1A_form.jsonl").write_text(
        json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": marker}) + "\n",
        encoding="utf-8",
    )
    (dd / "1A_form.jsonl").write_text(
        json.dumps({"variable_id": "RESULT", "label": "result"}) + "\n",
        encoding="utf-8",
    )


def _seed_run_artifacts(
    study: str,
    run_id: str,
    *,
    approved: list[str] | None = None,
    held: list[str] | None = None,
    verifier_passed: bool = True,
) -> Path:
    """Write phi_handling_approval.json + verifier_report.json for a run."""
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    approval = {
        "run_id": run_id,
        "study": study,
        "approved_forms": approved if approved is not None else ["1A_form.xlsx"],
        "held_forms": held if held is not None else [],
        "status": "approved" if not held else "partial",
    }
    (run_dir / "phi_handling_approval.json").write_text(json.dumps(approval), encoding="utf-8")
    # The real verifier writes "overall" ("pass"/"fail") + "exit_code" (0 on
    # pass) — NOT a "verifier_passed" key (review #14).
    verifier = {
        "run_id": run_id,
        "overall": "pass" if verifier_passed else "fail",
        "exit_code": 0 if verifier_passed else 5,
        "assertions": [],
    }
    (run_dir / "verifier_report.json").write_text(json.dumps(verifier), encoding="utf-8")
    return run_dir


# ── path helpers ────────────────────────────────────────────────────────────


class TestPathHelpers:
    def test_snapshots_root_under_output(self, monkeypatch_config: Path) -> None:
        root = snapshots_root(config.STUDY_NAME)
        assert root == Path(config.OUTPUT_DIR) / config.STUDY_NAME / "snapshots"

    def test_snapshot_path_none_is_root(self, monkeypatch_config: Path) -> None:
        assert snapshot_path(config.STUDY_NAME) == snapshots_root(config.STUDY_NAME)

    def test_snapshot_path_with_id(self, monkeypatch_config: Path) -> None:
        p = snapshot_path(config.STUDY_NAME, "snap_abc")
        assert p == snapshots_root(config.STUDY_NAME) / "snap_abc"

    def test_llm_source_path(self, monkeypatch_config: Path) -> None:
        p = snapshot_llm_source_path(config.STUDY_NAME, "snap_abc")
        assert p == snapshots_root(config.STUDY_NAME) / "snap_abc" / "llm_source"

    def test_empty_study_rejected(self) -> None:
        with pytest.raises(SnapshotError):
            snapshots_root("")

    @pytest.mark.parametrize("bad", ["../escape", "a/b", ".", "..", "x\x00y"])
    def test_path_traversal_id_rejected(self, monkeypatch_config: Path, bad: str) -> None:
        with pytest.raises(SnapshotError):
            snapshot_path(config.STUDY_NAME, bad)


# ── write / list / load round-trip ───────────────────────────────────────────


class TestWriteListLoad:
    def test_round_trip(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)

        dest = write_snapshot(study, RUN_ID)
        assert dest.is_dir()

        ids = list_snapshots(study)
        assert ids == [dest.name]

        manifest = load_snapshot(study, dest.name)
        assert manifest["source_run_id"] == RUN_ID
        assert manifest["study"] == study
        assert manifest["snapshot_id"] == dest.name
        assert manifest["verifier_passed"] is True
        assert manifest["approved_forms"] == ["1A_form.xlsx"]
        assert manifest["held_forms"] == []

    def test_copies_llm_source_tree(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        copied = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        assert copied.is_file()
        # content preserved verbatim — read from the LIVE tree write_snapshot
        # sources (OUTPUT_DIR/study/llm_source), not the repointable global.
        original = (
            _live_llm_source(study) / "dataset_schema" / "files" / "1A_form.jsonl"
        ).read_text(encoding="utf-8")
        assert copied.read_text(encoding="utf-8") == original

    def test_copies_approval_and_verifier(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        assert (dest / "phi_handling_approval.json").is_file()
        assert (dest / "verifier_report.json").is_file()
        assert (dest / MANIFEST_FILENAME).is_file()

    def test_held_forms_recorded(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID, approved=["1A_form.xlsx"], held=["9Z_held.xlsx"])
        dest = write_snapshot(study, RUN_ID)
        manifest = load_snapshot(study, dest.name)
        assert manifest["held_forms"] == ["9Z_held.xlsx"]
        assert manifest["approved_forms"] == ["1A_form.xlsx"]

    def test_list_empty_when_no_snapshots(self, monkeypatch_config: Path) -> None:
        assert list_snapshots(config.STUDY_NAME) == []

    def test_list_ignores_partial_dirs(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        write_snapshot(study, RUN_ID)
        # A leftover ``.partial`` dir must not be listed.
        stray = snapshots_root(study) / ".snap_stray.partial"
        stray.mkdir(parents=True, exist_ok=True)
        (stray / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
        assert ".snap_stray.partial" not in list_snapshots(study)


# ── immutability ─────────────────────────────────────────────────────────────


class TestImmutability:
    def test_second_write_same_id_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        snap_id = dest.name

        # Same content + run_id -> same deterministic id -> immutability trips.
        with pytest.raises(SnapshotExistsError):
            write_snapshot(study, RUN_ID)
        # The prior snapshot is untouched.
        assert list_snapshots(study) == [snap_id]

    def test_explicit_existing_id_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        write_snapshot(study, RUN_ID, snapshot_id="snap_fixed")
        with pytest.raises(SnapshotExistsError):
            write_snapshot(study, RUN_ID, snapshot_id="snap_fixed")


# ── deterministic id + stable manifest hashes ────────────────────────────────


class TestDeterministicId:
    def test_same_content_same_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study), marker="alpha")
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        # Wipe the snapshot dir; same inputs must mint the same id.
        import shutil

        shutil.rmtree(snapshots_root(study))
        second = write_snapshot(study, RUN_ID).name
        assert first == second

    def test_different_content_different_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study), marker="alpha")
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        # Mutate llm_source content -> different manifest -> different id.
        _seed_llm_source(_live_llm_source(study), marker="beta")
        second = write_snapshot(study, RUN_ID).name
        assert first != second

    def test_different_run_id_different_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        other_run = "run_othertestid0002"
        _seed_run_artifacts(study, other_run)
        second = write_snapshot(study, other_run).name
        assert first != second

    def test_manifest_hashes_match_files(self, monkeypatch_config: Path) -> None:
        import hashlib

        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        manifest = load_snapshot(study, dest.name)

        tree = manifest["llm_source_manifest"]
        assert "dataset_schema/files/1A_form.jsonl" in tree
        # Re-hash the copied file and compare to the recorded digest.
        copied = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        digest = hashlib.sha256(copied.read_bytes()).hexdigest()
        assert tree["dataset_schema/files/1A_form.jsonl"] == digest


# ── sentinel ─────────────────────────────────────────────────────────────────


class TestNoLlmSentinel:
    def test_sentinel_at_snapshot_root(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        sentinel = dest / config.AUDIT_NO_LLM_SENTINEL_NAME
        assert sentinel.is_file()


# ── missing-artifact fail-closed ─────────────────────────────────────────────


class TestFailClosed:
    def test_missing_llm_source_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        # No live tree is seeded at OUTPUT_DIR/study/llm_source (the path
        # write_snapshot sources from after review #3); its absence must
        # fail-closed. Remove it defensively in case a fixture created it.
        import shutil

        live = _live_llm_source(study)
        if live.exists():
            shutil.rmtree(live)
        _seed_run_artifacts(study, RUN_ID)
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_missing_approval_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "verifier_report.json").write_text(
            json.dumps({"verifier_passed": True}), encoding="utf-8"
        )
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_missing_verifier_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "phi_handling_approval.json").write_text(
            json.dumps({"approved_forms": [], "held_forms": []}), encoding="utf-8"
        )
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_load_missing_snapshot_raises(self, monkeypatch_config: Path) -> None:
        with pytest.raises(SnapshotNotFoundError):
            load_snapshot(config.STUDY_NAME, "snap_doesnotexist")

    def test_select_missing_snapshot_raises(self, monkeypatch_config: Path) -> None:
        with pytest.raises(SnapshotNotFoundError):
            select_snapshot_llm_source(config.STUDY_NAME, "snap_doesnotexist")


# ── security zone ────────────────────────────────────────────────────────────


class TestSecurityZone:
    def _make_snapshot(self) -> Path:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        return write_snapshot(study, RUN_ID)

    # After review #12 the snapshot-root guard is keyed on the configured
    # OUTPUT_DIR layout, so even the default tmp layout (no literal ``output``
    # segment) denies the snapshot root THROUGH THE GUARD — surfaced as
    # SnapshotZoneViolation, which runs before read-root containment. (Both
    # SnapshotZoneViolation and ZoneViolationError are PermissionError
    # subclasses, so is_agent_readable denies uniformly either way.)
    def test_snapshot_root_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest)

    def test_snapshot_approval_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        approval = dest / "phi_handling_approval.json"
        assert approval.is_file()
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(approval)

    def test_snapshot_manifest_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / MANIFEST_FILENAME)

    def test_snapshot_verifier_report_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / "verifier_report.json")

    def test_unselected_llm_source_rejected(self, monkeypatch_config: Path) -> None:
        """Until a snapshot is SELECTED (config repointed), even its
        llm_source subtree is outside the agent read zone."""
        dest = self._make_snapshot()
        leaf = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        assert leaf.is_file()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(leaf)

    def test_selected_llm_source_permitted(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a maintainer selects the snapshot, the loader repoints
        ``config.STUDY_LLM_SOURCE_DIR`` at ``snapshots/{id}/llm_source/`` and
        that subtree (already PHI-scrubbed) becomes readable — while the
        approval/manifest at the snapshot root stay denied."""
        study = config.STUDY_NAME
        dest = self._make_snapshot()
        snap_llm_source = select_snapshot_llm_source(study, dest.name)
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", snap_llm_source)

        leaf = snap_llm_source / "dataset_schema" / "files" / "1A_form.jsonl"
        assert validate_agent_read(leaf) == Path(leaf.resolve())
        assert is_agent_readable(leaf)

        # The snapshot ROOT and approval file remain denied even when selected —
        # the OUTPUT_DIR-keyed guard (review #12) fires before containment.
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / "phi_handling_approval.json")
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest)


# ── realpath snapshot-root guard (segment logic, independent of tmp paths) ────


class TestSnapshotRootGuardSegments:
    """``deny_if_snapshot_root`` literal-segment FALLBACK — for paths OUTSIDE the
    configured ``config.OUTPUT_DIR``. The primary detection (review #12) is keyed
    on the configured OUTPUT_DIR layout; these synthetic ``/srv/output/...``
    paths sit outside it, so they exercise the literal ``output/<study>/
    snapshots/`` segment-scan fallback that still denies them."""

    def test_root_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots/snap_abc")

    def test_approval_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(
                "/srv/output/StudyX/snapshots/snap_abc/phi_handling_approval.json"
            )

    def test_manifest_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots/snap_abc/snapshot_manifest.json")

    def test_snapshots_dir_itself_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots")

    def test_llm_source_subtree_exempt(self) -> None:
        # Exempt — no raise.
        deny_if_snapshot_root(
            "/srv/output/StudyX/snapshots/snap_abc/llm_source/dataset_schema/files/x.jsonl"
        )

    def test_non_snapshot_path_exempt(self) -> None:
        deny_if_snapshot_root("/srv/output/StudyX/llm_source/x.jsonl")
        deny_if_snapshot_root("/some/random/path")


# ── symlink hardening (review finding #3) ────────────────────────────────────


class TestSymlinkHardening:
    """A symlink under ``llm_source/`` that escapes the tree must fail-closed:
    ``copytree(symlinks=False)`` would otherwise dereference it and bake the
    out-of-tree (possibly PHI) target content into the immutable, re-exposable
    snapshot."""

    def test_escaping_symlink_rejected(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)

        # A file OUTSIDE the llm_source tree (stand-in for a raw PHI file).
        outside = Path(config.OUTPUT_DIR) / "outside_secret.jsonl"
        outside.write_text('{"SUBJID": "leak"}\n', encoding="utf-8")
        escaping = _live_llm_source(study) / "dataset_schema" / "files" / "link.jsonl"
        escaping.symlink_to(outside)

        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_in_tree_symlink_allowed(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        # A symlink whose target stays WITHIN the tree is fine — its content is
        # already scrubbed; copytree materialises it as a regular file.
        files_dir = _live_llm_source(study) / "dataset_schema" / "files"
        (files_dir / "alias.jsonl").symlink_to(files_dir / "1A_form.jsonl")
        dest = write_snapshot(study, RUN_ID)
        assert dest.is_dir()


# ── security zone under a REAL output layout (review finding #1) ──────────────


class TestSecurityZoneRealOutputLayout:
    """Co-test the ``deny_if_snapshot_root`` GUARD against a path that actually
    contains a literal ``output`` segment, so BOTH detection mechanisms agree:
    the OUTPUT_DIR-keyed layout check (primary, review #12) AND the literal
    ``output`` segment fallback. This is the production-shaped path layout
    (``<...>/output/<study>/snapshots/<id>/``)."""

    def test_guard_fires_on_real_output_layout(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = config.STUDY_NAME
        # Repoint config so every snapshot path carries a literal ``output``
        # segment — both the OUTPUT_DIR-keyed check and the literal fallback fire.
        out = Path(config.OUTPUT_DIR) / "output"
        llm_source = out / study / "llm_source"
        monkeypatch.setattr(config, "OUTPUT_DIR", out)
        monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", out)
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source)

        _seed_llm_source(llm_source)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        # Sanity: the path really does carry the segments the guard matches.
        assert "output" in dest.parts and "snapshots" in dest.parts

        # The GUARD itself denies the root/approval/manifest on this real layout.
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(dest / "phi_handling_approval.json")
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(dest / MANIFEST_FILENAME)

        # validate_agent_read surfaces the GUARD denial as SnapshotZoneViolation
        # (the guard runs before read-root containment). After review #12 the
        # default tmp layout ALSO denies through the guard (the OUTPUT_DIR-keyed
        # check fires there too); this test just additionally confirms the
        # literal-``output`` production layout. Both SnapshotZoneViolation and
        # ZoneViolationError are PermissionError subclasses, so is_agent_readable
        # (which catches the common base) denies uniformly either way.
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / "phi_handling_approval.json")
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / MANIFEST_FILENAME)
        assert not is_agent_readable(dest / "phi_handling_approval.json")
        assert not is_agent_readable(dest / MANIFEST_FILENAME)

        # ...while a SELECTED llm_source leaf is permitted, root still denied.
        snap_llm_source = select_snapshot_llm_source(study, dest.name)
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", snap_llm_source)
        leaf = snap_llm_source / "dataset_schema" / "files" / "1A_form.jsonl"
        assert validate_agent_read(leaf) == Path(leaf.resolve())
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / "phi_handling_approval.json")


# ── write_snapshot sources the LIVE tree, not the repointed global (review #3) ─


class TestWriteSnapshotSourcesLiveTree:
    """``write_snapshot`` must capture the LIVE publish tree
    (``OUTPUT_DIR/<study>/llm_source``), NOT the module-global
    ``config.STUDY_LLM_SOURCE_DIR`` — which the UI repoints at a
    previously-activated snapshot's ``llm_source/`` (review #3/#8/#15)."""

    def test_captures_live_tree_not_repointed_global(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = config.STUDY_NAME

        # The LIVE publish tree carries the marker we expect to be snapshotted.
        _seed_llm_source(_live_llm_source(study), marker="LIVE_PUBLISH")
        _seed_run_artifacts(study, RUN_ID)

        # Simulate a prior UI snapshot activation: repoint the module global at a
        # DIFFERENT tree with DIFFERENT content. A buggy write_snapshot reading
        # the global would capture this stale tree instead of the live publish.
        stale = monkeypatch_config / "stale_activated" / "llm_source"
        _seed_llm_source(stale, marker="STALE_ACTIVATED_SNAPSHOT")
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", stale)

        dest = write_snapshot(study, RUN_ID)

        # The snapshot must contain the LIVE content, never the repointed global's.
        copied = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        body = copied.read_text(encoding="utf-8")
        assert "LIVE_PUBLISH" in body
        assert "STALE_ACTIVATED_SNAPSHOT" not in body

        # The recorded manifest also reflects the live tree (same digest).
        manifest = load_snapshot(study, dest.name)
        live_leaf = (
            _live_llm_source(study) / "dataset_schema" / "files" / "1A_form.jsonl"
        ).read_bytes()
        import hashlib

        assert (
            manifest["llm_source_manifest"]["dataset_schema/files/1A_form.jsonl"]
            == hashlib.sha256(live_leaf).hexdigest()
        )


# ── verifier_passed derives from "overall", not a missing key (review #14) ────


class TestVerifierPassedDerivation:
    """The verifier report has no ``verifier_passed`` key — its canonical pass
    signal is ``overall == "pass"`` (with ``exit_code == 0``). The manifest must
    record the real verdict, not a hardcoded False from a missing key."""

    def test_passing_overall_records_true(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID, verifier_passed=True)
        dest = write_snapshot(study, RUN_ID)
        assert load_snapshot(study, dest.name)["verifier_passed"] is True

    def test_failing_overall_records_false(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID, verifier_passed=False)
        dest = write_snapshot(study, RUN_ID)
        assert load_snapshot(study, dest.name)["verifier_passed"] is False

    def test_legacy_verifier_passed_key_is_ignored(self, monkeypatch_config: Path) -> None:
        """A report carrying ONLY a (now non-canonical) ``verifier_passed`` key
        and no ``overall``/``exit_code`` must NOT be treated as passing — the
        derivation keys on ``overall``/``exit_code`` only (fail-closed)."""
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "phi_handling_approval.json").write_text(
            json.dumps({"approved_forms": ["1A_form.xlsx"], "held_forms": []}),
            encoding="utf-8",
        )
        (run_dir / "verifier_report.json").write_text(
            json.dumps({"run_id": RUN_ID, "verifier_passed": True}), encoding="utf-8"
        )
        dest = write_snapshot(study, RUN_ID)
        assert load_snapshot(study, dest.name)["verifier_passed"] is False


# ── snapshot-root guard fires under the STANDARD tmp layout (review #12) ───────


class TestSnapshotGuardUnderStandardTmpLayout:
    """Under the standard tmp layout (``OUTPUT_DIR=tmp_path``, NO literal
    ``output`` segment), the snapshot-root guard must STILL deny the root and
    approval THROUGH THE GUARD (review #12) — keyed on the configured OUTPUT_DIR
    layout, not on a hardcoded ``output`` literal. Previously the guard never
    fired here and denial relied solely on read-root containment."""

    def test_guard_denies_under_tmp_layout(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        # Sanity: the tmp layout has NO literal ``output`` segment — so the legacy
        # literal scan alone would never have fired here.
        assert "output" not in dest.parts
        assert "snapshots" in dest.parts

        # The GUARD itself denies the snapshot root + approval (not containment).
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(dest)
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(dest / "phi_handling_approval.json")
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(dest / MANIFEST_FILENAME)

        # validate_agent_read surfaces the same guard denial first.
        with pytest.raises(SnapshotZoneViolation):
            validate_agent_read(dest / "phi_handling_approval.json")
        assert not is_agent_readable(dest / "phi_handling_approval.json")

        # The ``<id>/llm_source/`` subtree stays exempt FROM THE GUARD (its
        # readability is gated by containment, not the snapshot guard).
        leaf = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        deny_if_snapshot_root(leaf)  # exempt — no raise
