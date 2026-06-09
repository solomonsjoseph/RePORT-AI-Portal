"""Tests for run_scrub(partial_on_review=...) behavior.

Covers the 4 security invariants required when partial_on_review=True:

SI-1: Un-jitterable row is NEVER in the published staging JSONL.
      Assert row count == kept count and quarantine file holds bad rows.
SI-2: A cleanly-scrubbed row in the same form carries the _phi_scrubbed marker.
SI-3: Two-form mix (one clean, one with bad row): BOTH forms publish good rows;
      only the bad form appears in scrub_outcome.json partial_forms.
SI-4: An all-clean run in partial mode writes scrub_outcome.json with
      partial=False and partial_forms={}.

Also covers the default (partial_on_review=False) contract:
- Un-jitterable row raises PHIDateUnshiftableError and aborts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import config
from scripts.security import phi_scrub

# ── Fixtures (sidecar_key and scrub_config_path live in conftest.py) ─────────


def _write_config(path: Path, **overrides: object) -> None:
    """Write a minimal phi_scrub.yaml; callers can override any key.

    partial_max_quarantine_fraction is set to 1.0 so tests can exercise
    partial mode with small datasets (e.g. 1-bad/2-total) without hitting
    the systemic-failure guard. Tests that want to validate the guard
    behaviour must pass their own value via **overrides.
    """
    payload: dict[str, object] = {
        "compliance_posture": "safe_harbor",
        "subject_id_field": "SUBJID",
        # VISDAT and anything ending _DAT will be date-jittered.
        "date_fields": ["^VISDAT$", "_DAT$"],
        "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
        "birthdate_field": "^DOB$",
        "max_jitter_days": 30,
        "orphan_quarantine_threshold": 10,
        # Allow any quarantine fraction in unit tests (systemic-failure threshold
        # is production behaviour; tested separately if needed).
        "partial_max_quarantine_fraction": 1.0,
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _seed_staging(rows: list[dict[str, Any]], filename: str = "1A_Form.jsonl") -> Path:
    """Write rows into config.STAGING_DATASETS_DIR and return the file path."""
    staging = Path(config.STAGING_DATASETS_DIR)
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / filename
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return target


def _make_runs_dir(tmp_path: Path, run_id: str) -> Path:
    """Create runs/{run_id}/ under tmp and return the runs root."""
    runs = tmp_path / "runs"
    (runs / run_id).mkdir(parents=True, exist_ok=True)
    return runs


# ── Helper: a date value that will NOT parse ──────────────────────────────────
# The string "BADDATE" is not in date_null_tokens and does not parse as any
# date format, so shift_date() returns None → _scrub_row returns (None, counts)
# with scope "phi-scrub-date-quarantine:VISDAT".
BAD_DATE = "BADDATE"
GOOD_DATE = "2014-07-28"  # clean ISO date; will be jittered deterministically


# ── Default (fail-closed) contract ───────────────────────────────────────────


class TestDefaultFailClosed:
    """partial_on_review=False (the default): un-jitterable row → exception."""

    def test_bad_date_raises_phi_date_unshiftable(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Default mode: a row with an unparseable date raises PHIDateUnshiftableError."""
        _write_config(scrub_config_path)
        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ]
        )
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

    def test_default_is_false(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Calling run_scrub without partial_on_review behaves identically to =False."""
        _write_config(scrub_config_path)
        _seed_staging([{"SUBJID": "S1", "VISDAT": BAD_DATE}])
        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")  # no keyword → default False


# ── Security invariant SI-1: bad row absent from published JSONL ───────────────


class TestSI1BadRowAbsentFromPublished:
    """SI-1: the un-jitterable row must NEVER appear in the staging JSONL."""

    def test_bad_row_absent_from_staging_after_partial(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        good_row = {"SUBJID": "S1", "VISDAT": GOOD_DATE}
        bad_row = {"SUBJID": "S2", "VISDAT": BAD_DATE}
        staging_file = _seed_staging([good_row, bad_row])
        runs_dir = _make_runs_dir(tmp_path, "run1")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run1",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        published = [json.loads(ln) for ln in staging_file.read_text().splitlines() if ln.strip()]

        # Exactly 1 kept row (the good row)
        assert len(published) == 1, f"Expected 1 kept row; got {len(published)}: {published}"

        # BAD_DATE must not appear in any kept-row value
        published_text = staging_file.read_text()
        assert BAD_DATE not in published_text, (
            f"Un-jitterable date {BAD_DATE!r} leaked into published staging JSONL"
        )

    def test_quarantine_file_holds_bad_row(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        bad_row = {"SUBJID": "S2", "VISDAT": BAD_DATE}
        _seed_staging([{"SUBJID": "S1", "VISDAT": GOOD_DATE}, bad_row])
        runs_dir = _make_runs_dir(tmp_path, "run1")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run1",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        staging_root = Path(config.STUDY_STAGING_DIR)
        quarantine_file = staging_root / "quarantine" / "date_unshiftable_1A_Form.jsonl"
        assert quarantine_file.is_file(), "Quarantine file must exist for bad rows"

        quarantined = [
            json.loads(ln) for ln in quarantine_file.read_text().splitlines() if ln.strip()
        ]
        assert len(quarantined) == 1, f"Expected 1 quarantined row; got {len(quarantined)}"

        # The quarantine row must NOT contain the raw BAD_DATE either — it has already
        # been field-only-scrubbed (drop_fields + birthdate) before write.
        # However VISDAT is a DATE field (not a drop field), so it may still be present
        # in the quarantine row.  The critical invariant is that BAD_DATE is NOT
        # in the published staging file (checked above).
        #
        # What we verify here: the quarantine file received the row (row count).
        # Whether VISDAT is present or not in the quarantine row is implementation-
        # defined; the row was NOT published.


# ── Security invariant SI-2: clean rows carry _phi_scrubbed marker ─────────────


class TestSI2CleanRowMarked:
    """SI-2: a cleanly scrubbed row carries _phi_scrubbed == 'v3'."""

    def test_kept_row_has_scrub_marker(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        staging_file = _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run2")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run2",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        published = [json.loads(ln) for ln in staging_file.read_text().splitlines() if ln.strip()]
        assert len(published) == 1
        assert published[0].get("_phi_scrubbed") == "v3", (
            f"Kept row is missing or has wrong _phi_scrubbed marker: {published[0]}"
        )

    def test_kept_row_subject_id_is_pseudonymized(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """The good row's SUBJID must be pseudonymized, not raw — proving full scrub ran."""
        _write_config(scrub_config_path)
        staging_file = _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run2b")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run2b",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        published = [json.loads(ln) for ln in staging_file.read_text().splitlines() if ln.strip()]
        assert len(published) == 1
        subj = published[0].get("SUBJID", "")
        assert subj.startswith("RID_SUBJ_"), f"Kept row SUBJID must be pseudonymized; got: {subj!r}"


# ── Security invariant SI-3: two-form mix ─────────────────────────────────────


class TestSI3TwoFormMix:
    """SI-3: two forms — one clean, one with bad row — both publish good rows."""

    def test_clean_form_fully_published(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)

        # Form A: all clean rows
        clean_file = _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": "2014-08-01"},
            ],
            filename="2A_CleanForm.jsonl",
        )

        # Form B: mix of good and bad rows
        bad_file = _seed_staging(
            [
                {"SUBJID": "S3", "VISDAT": GOOD_DATE},
                {"SUBJID": "S4", "VISDAT": BAD_DATE},
            ],
            filename="3B_BadForm.jsonl",
        )

        runs_dir = _make_runs_dir(tmp_path, "run3")
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run3",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        # Clean form: both rows kept
        clean_published = [
            json.loads(ln) for ln in clean_file.read_text().splitlines() if ln.strip()
        ]
        assert len(clean_published) == 2, (
            f"Clean form must retain all 2 good rows; got {len(clean_published)}"
        )

        # Bad form: only the good row kept
        bad_published = [json.loads(ln) for ln in bad_file.read_text().splitlines() if ln.strip()]
        assert len(bad_published) == 1, (
            f"Bad form must retain exactly 1 good row; got {len(bad_published)}"
        )

        # scrub_outcome.json: partial_forms should list only the bad form
        outcome_path = runs_dir / "run3" / "scrub_outcome.json"
        assert outcome_path.is_file(), "scrub_outcome.json must exist when run_id is given"
        outcome = json.loads(outcome_path.read_text())

        assert outcome["partial"] is True, "partial flag must be True when any form has bad rows"
        partial_forms = outcome["partial_forms"]
        assert "3B_BadForm.jsonl" in partial_forms, (
            f"Bad form must appear in partial_forms; got keys: {list(partial_forms)}"
        )
        assert "2A_CleanForm.jsonl" not in partial_forms, (
            "Clean form must NOT appear in partial_forms"
        )

    def test_partial_forms_counts_correct(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """scrub_outcome.json partial_forms counts: kept + quarantined + reasons."""
        _write_config(scrub_config_path)

        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},  # kept
                {"SUBJID": "S2", "VISDAT": BAD_DATE},  # quarantined
                {"SUBJID": "S3", "VISDAT": BAD_DATE},  # quarantined
            ],
            filename="1A_Form.jsonl",
        )

        runs_dir = _make_runs_dir(tmp_path, "run3b")
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run3b",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        outcome = json.loads((runs_dir / "run3b" / "scrub_outcome.json").read_text())
        assert outcome["partial"] is True
        entry = outcome["partial_forms"]["1A_Form.jsonl"]
        assert entry["kept"] == 1, f"kept count wrong: {entry}"
        assert entry["quarantined"] == 2, f"quarantined count wrong: {entry}"
        # reasons must include a date_unshiftable:2 label
        reasons = entry["reasons"]
        assert any("date_unshiftable" in r for r in reasons), (
            f"reasons must include date_unshiftable; got {reasons}"
        )
        # The count suffix should be "2"
        assert any("date_unshiftable:2" in r for r in reasons), (
            f"reason must carry count=2; got {reasons}"
        )

    def test_bad_form_good_rows_have_marker_and_no_bad_date(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Combined SI-1 + SI-2 for the bad form in a two-form run."""
        _write_config(scrub_config_path)

        bad_file = _seed_staging(
            [
                {"SUBJID": "S10", "VISDAT": GOOD_DATE},
                {"SUBJID": "S11", "VISDAT": BAD_DATE},
            ],
            filename="1A_Form.jsonl",
        )
        _seed_staging(
            [{"SUBJID": "S20", "VISDAT": GOOD_DATE}],
            filename="2B_Clean.jsonl",
        )

        runs_dir = _make_runs_dir(tmp_path, "run3c")
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run3c",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        bad_published = [json.loads(ln) for ln in bad_file.read_text().splitlines() if ln.strip()]
        assert len(bad_published) == 1

        row = bad_published[0]
        # SI-2: marker present
        assert row.get("_phi_scrubbed") == "v3"
        # SI-1: BAD_DATE not anywhere in published row values
        for v in row.values():
            assert BAD_DATE not in str(v), f"BAD_DATE leaked into published row field value: {row}"


# ── Security invariant SI-4: all-clean run writes partial=false ────────────────


class TestSI4AllCleanPartialFalse:
    """SI-4: all-clean run in partial mode → scrub_outcome.json has partial=False."""

    def test_all_clean_partial_false_empty_partial_forms(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": "2014-09-10"},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run4")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run4",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        outcome_path = runs_dir / "run4" / "scrub_outcome.json"
        assert outcome_path.is_file(), "scrub_outcome.json must be written even for clean runs"
        outcome = json.loads(outcome_path.read_text())

        assert outcome["partial"] is False, (
            f"All-clean run must produce partial=False; got: {outcome}"
        )
        assert outcome["partial_forms"] == {}, (
            f"All-clean run must produce empty partial_forms; got: {outcome['partial_forms']}"
        )

    def test_all_clean_all_rows_published(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """Sanity: in a clean partial-mode run, all rows are kept and marked."""
        _write_config(scrub_config_path)
        staging_file = _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": "2014-09-10"},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run4b")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run4b",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        published = [json.loads(ln) for ln in staging_file.read_text().splitlines() if ln.strip()]
        assert len(published) == 2
        for row in published:
            assert row.get("_phi_scrubbed") == "v3"

    def test_no_run_id_no_outcome_file(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """scrub_outcome.json is only written when run_id is provided."""
        _write_config(scrub_config_path)
        _seed_staging([{"SUBJID": "S1", "VISDAT": GOOD_DATE}])

        # partial_on_review=True but no run_id → no sidecar written
        phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)

        # No stray outcome files under tmp
        stray = (
            list((tmp_path / "runs").glob("**/scrub_outcome.json"))
            if (tmp_path / "runs").exists()
            else []
        )
        assert not stray, f"Unexpected scrub_outcome.json written with no run_id: {stray}"


# ── Outcome sidecar integrity ──────────────────────────────────────────────────


class TestOutcomeSidecarIntegrity:
    """scrub_outcome.json content rules."""

    def test_outcome_sidecar_has_expected_keys(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        _write_config(scrub_config_path)
        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run5")
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run5",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        outcome = json.loads((runs_dir / "run5" / "scrub_outcome.json").read_text())
        assert set(outcome.keys()) >= {"run_id", "study", "partial", "partial_forms"}
        assert outcome["run_id"] == "run5"
        assert outcome["study"] == "TEST"

    def test_outcome_sidecar_contains_no_raw_dates(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """The sidecar records form NAMES and COUNTS only — never row values."""
        _write_config(scrub_config_path)
        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ]
        )
        runs_dir = _make_runs_dir(tmp_path, "run5b")
        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="run5b",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        sidecar_text = (runs_dir / "run5b" / "scrub_outcome.json").read_text()
        # GOOD_DATE is the raw clean date value — must not appear in sidecar
        assert GOOD_DATE not in sidecar_text, (
            f"Raw date value {GOOD_DATE!r} leaked into scrub_outcome.json"
        )
        # BAD_DATE is the unparseable raw value — must also not appear
        assert BAD_DATE not in sidecar_text, (
            f"Unparseable raw date {BAD_DATE!r} leaked into scrub_outcome.json"
        )


# ── M2: partial-mode ledger contains NO quarantine actions ────────────────────


class TestM2LedgerNoQuarantineActions:
    """M2 (SECURITY INVARIANT): the as-written PHI ledger for a partially-published
    form must contain NO event whose action contains the substring 'quarantine'.

    This locks the _SCOPE_TO_ACTION omission: quarantine scopes are deliberately
    absent from the mapping so quarantined rows (never published) never generate
    PHI ledger entries for the published dataset.
    """

    def test_partial_ledger_has_no_quarantine_action(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """After a partial-mode run with one quarantined row, the per-dataset
        phi_handling_ledger.as_written.json must have zero events whose
        ``action`` field contains the substring 'quarantine'.
        """
        import config
        from scripts.audit.ledger import dataset_phi_ledger_path

        _write_config(scrub_config_path)

        # Two rows: one publishable, one with an unparseable date that will be quarantined.
        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},  # kept + published
                {"SUBJID": "S2", "VISDAT": BAD_DATE},  # quarantined
            ],
            filename="1A_Form.jsonl",
        )
        runs_dir = _make_runs_dir(tmp_path, "runM2")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="runM2",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        # Locate the per-dataset ledger
        audit_dir = Path(config.AUDIT_SCRUB_REPORT_PATH).parent
        ledger_path = dataset_phi_ledger_path(audit_dir, "1A_Form.jsonl")
        assert ledger_path.is_file(), (
            "phi_handling_ledger.as_written.json must exist after partial-mode run"
        )

        payload = json.loads(ledger_path.read_text(encoding="utf-8"))

        # Security invariant: no event action must contain 'quarantine'
        quarantine_events = [
            ev for ev in payload.get("events", []) if "quarantine" in str(ev.get("action", ""))
        ]
        assert not quarantine_events, (
            f"Ledger must contain no quarantine-action events; found: {quarantine_events}"
        )

    def test_partial_ledger_kept_row_actions_are_valid_phi_actions(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
        tmp_path: Path,
    ) -> None:
        """All actions in the ledger must be recognised PHI handling actions —
        none should be a quarantine-scope action or any other unknown action.
        Valid actions (from ledger._PHI_ACTIONS): drop, pseudonymize, jitter_date,
        generalize, suppress_small_cell, cap, birthdate_drop, band.
        """
        import config
        from scripts.audit.ledger import dataset_phi_ledger_path

        _write_config(scrub_config_path)

        _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ],
            filename="1A_Form.jsonl",
        )
        runs_dir = _make_runs_dir(tmp_path, "runM2b")

        phi_scrub.run_scrub(
            study_name="TEST",
            run_id="runM2b",
            runs_dir=runs_dir,
            partial_on_review=True,
        )

        audit_dir = Path(config.AUDIT_SCRUB_REPORT_PATH).parent
        ledger_path = dataset_phi_ledger_path(audit_dir, "1A_Form.jsonl")
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))

        valid_actions = frozenset(
            {
                "drop",
                "pseudonymize",
                "jitter_date",
                "generalize",
                "suppress_small_cell",
                "cap",
                "birthdate_drop",
                "band",
            }
        )
        for ev in payload.get("events", []):
            action = ev.get("action", "")
            assert action in valid_actions, (
                f"Event action {action!r} is not a valid PHI action; event: {ev}"
            )


# ── M4: strict mode — PHIDateUnshiftableError + JSONL byte-unchanged ──────────


class TestM4StrictModeByteUnchanged:
    """M4 (SECURITY INVARIANT): in strict mode (partial_on_review=False), a
    date-unshiftable row must (a) raise PHIDateUnshiftableError, and (b) leave
    the staging JSONL byte-for-byte unchanged.

    The byte-equality assertion proves no partial rewrite happened on a strict abort.
    """

    def test_strict_mode_raises_and_jsonl_unchanged(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Strict default: bad date raises PHIDateUnshiftableError; JSONL is untouched."""
        _write_config(scrub_config_path)

        staging_file = _seed_staging(
            [
                {"SUBJID": "S1", "VISDAT": GOOD_DATE},
                {"SUBJID": "S2", "VISDAT": BAD_DATE},
            ],
            filename="1A_Form.jsonl",
        )

        # Capture bytes BEFORE the attempted scrub
        before_bytes = staging_file.read_bytes()

        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")  # partial_on_review defaults to False

        # Capture bytes AFTER the (aborted) scrub
        after_bytes = staging_file.read_bytes()

        assert before_bytes == after_bytes, (
            "Strict-mode abort must leave the staging JSONL byte-for-byte unchanged; "
            f"before={len(before_bytes)} bytes, after={len(after_bytes)} bytes"
        )

    def test_strict_mode_no_partial_write_single_bad_row(
        self,
        monkeypatch_config: Path,
        sidecar_key: Path,
        scrub_config_path: Path,
    ) -> None:
        """Even when the bad row is the only row, strict mode must not rewrite the file."""
        _write_config(scrub_config_path)

        staging_file = _seed_staging(
            [{"SUBJID": "S1", "VISDAT": BAD_DATE}],
            filename="1A_Form.jsonl",
        )
        before_bytes = staging_file.read_bytes()

        with pytest.raises(phi_scrub.PHIDateUnshiftableError):
            phi_scrub.run_scrub(study_name="TEST")

        assert staging_file.read_bytes() == before_bytes, (
            "Strict-mode abort on a single-row file must not rewrite the JSONL"
        )
