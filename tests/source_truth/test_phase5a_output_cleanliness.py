"""Phase 5a Task 4 — output cleanliness smoke tests.

Regression guards: the pipeline must NEVER create ``staging/`` or
``human_review/`` directories under ``output/<study>/`` after the Phase 5a
refactor. Those intermediates live under ``config.TMP_DIR / <study> /``.

The three tests below are deliberately narrow path-discipline checks; they
share fixture helpers with ``test_build_coordinator.py`` and
``test_verify_and_promote.py`` rather than duplicating them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.build import BuildCoordinatorError, run_build
from scripts.source_truth.verify_and_promote import (
    _promote_dataset_schema,
    run_verification,
)

# Reuse the real-emitter fixture helpers from the verify_and_promote tests.
from tests.source_truth.test_verify_and_promote import (
    _write_policy,
    _write_real_cleanup_audit,
    _write_real_phi_audit,
    _write_scrubbed_jsonl,
    _write_staging_dataset_schema,
)


def test_build_creates_no_staging_under_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ``run_build``, ``output/<study>/staging/`` must not exist; the
    staging tree lives under ``tmp/<study>/staging/`` instead."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    policies_dir = tmp_path / "policies"
    policies_dir.mkdir()
    # Minimal valid policy YAML so build progresses far enough to create dirs.
    (policies_dir / "TestForm_policy.yaml").write_text(
        "schema_version: 2\nstudy: TestStudy\nform: TestForm\nvariables: {}\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "output" / "TestStudy"

    try:
        run_build(
            study="TestStudy",
            policies_dir=policies_dir,
            output_root=output_root,
            column_inventory=None,
        )
    except BuildCoordinatorError:
        # We only care about directory placement, not full build success.
        pass

    assert (output_root / "staging").exists() is False, (
        f"regression: staging/ created under output_root at {output_root / 'staging'}"
    )
    assert (tmp_path / "tmp" / "TestStudy" / "staging").exists() is True, (
        "build should create staging under tmp/<study>/"
    )


def test_verify_creates_no_human_review_under_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing reconciliation must write discrepancies under
    ``tmp/<study>/human_review/``, never under ``output/human_review/``."""
    import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")

    study = "Mini"
    sot_dir = tmp_path / "sot"
    sot_dir.mkdir()
    staging = tmp_path / "staging"
    audit = tmp_path / "audit"
    output_root = tmp_path / "output"

    # Force a reconciliation failure: SoT={A,B,C}, scrubbed={A}, no explanation.
    _write_policy(sot_dir, "form_a", ["A", "B", "C"])
    _write_scrubbed_jsonl(output_root, "form_a", ["A"])
    scrub_path = _write_real_phi_audit(audit, drops_by_form={})
    cleanup_path = _write_real_cleanup_audit(audit, column_drops_by_source={})

    code = run_verification(
        study=study,
        sot_dir=sot_dir,
        staging_root=staging,
        scrub_report_path=scrub_path,
        cleanup_report_path=cleanup_path,
        output_root=output_root,
    )
    assert code == 2, "fixture must produce a failing reconciliation"

    assert (output_root / "human_review").exists() is False, (
        "regression: human_review/ created under output_root"
    )
    assert (config.TMP_DIR / study / "human_review").exists() is True, (
        "expected human_review under tmp/<study>/"
    )


def test_promote_reads_schema_from_tmp_not_output(tmp_path: Path) -> None:
    """``_promote_dataset_schema`` must read the staging dataset_schema from
    the provided ``staging_dir`` (under tmp/), and ignore any file sitting
    at the legacy ``output_root/staging/`` location."""
    output_root = tmp_path / "output" / "TestStudy"
    output_root.mkdir(parents=True)
    (output_root / "llm_source").mkdir()

    # Correct (new) location: tmp/<study>/staging/llm_source/
    correct_payload = {
        "artifact_type": "study_dataset_schema",
        "entries": [{"form": "form_a", "columns": ["A", "B"]}],
    }
    staging_dir = _write_staging_dataset_schema(
        output_root,
        correct_payload,
        tmp_root=tmp_path / "tmp",
        study="TestStudy",
    ).parent

    # Booby trap: a different payload at the legacy output/staging/ path.
    legacy_payload = {
        "artifact_type": "study_dataset_schema",
        "entries": [{"form": "WRONG", "columns": ["WRONG"]}],
    }
    legacy_staging = output_root / "staging" / "llm_source"
    legacy_staging.mkdir(parents=True)
    (legacy_staging / "phi_handled_dataset_schema.json").write_text(
        json.dumps(legacy_payload), encoding="utf-8"
    )

    rc = _promote_dataset_schema(output_root=output_root, staging_dir=staging_dir)
    assert rc == 0

    promoted = output_root / "llm_source" / "dataset_schema.json"
    assert promoted.is_file()
    assert json.loads(promoted.read_text(encoding="utf-8")) == correct_payload, (
        "regression: promotion read from output/staging/ instead of tmp/.../staging/"
    )
