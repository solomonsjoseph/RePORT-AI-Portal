"""C1 — Stress / robustness suite (marker: ``stress``; excluded from the default gate).

Run with::

    uv run --all-groups python -m pytest tests/stress -m stress -q

Every assertion is pinned to the CURRENT trusted-code behavior of this tree (not
a guessed spec): the snapshot rejects tree-ESCAPING symlinks before copying, date
errors are PHI-safe (masked shape, never the raw value — GR-1), orphan rows are
quarantined not published, non-UTF8 staging fails closed, and a large file scrubs
within a linear memory budget. Reads/asserts metadata + counts only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import config
from scripts.security import phi_scrub

pytestmark = pytest.mark.stress


def _write_config(path: Path, **overrides: object) -> None:
    """Minimal valid phi_scrub.yaml (mirrors the canonical fixture)."""
    import yaml

    payload: dict[str, object] = {
        "compliance_posture": "safe_harbor",
        "subject_id_field": "SUBJID",
        "date_fields": ["^VISDAT$", "_DAT$"],
        "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
        "birthdate_field": "^DOB$",
        "max_jitter_days": 30,
        "orphan_quarantine_threshold": 5,
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _seed_staging(
    rows: list[dict[str, Any]] | None,
    filename: str = "1A_ICScreening.jsonl",
    *,
    raw_bytes: bytes | None = None,
) -> Path:
    staging = config.STAGING_DATASETS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / filename
    if raw_bytes is not None:
        target.write_bytes(raw_bytes)
    else:
        with target.open("w", encoding="utf-8") as fh:
            for row in rows or []:
                fh.write(json.dumps(row) + "\n")
    return target


def _clean_rows(src: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in src.read_text().splitlines() if line]


# ── snapshot: tree-escaping symlink rejection (security) ──────────────────────


class TestSnapshotEscapingSymlinkRejection:
    def test_escaping_symlink_is_rejected_fail_closed(self, tmp_path: Path) -> None:
        from scripts.utils.snapshot import SnapshotError, _assert_no_escaping_symlinks

        root = tmp_path / "llm_source"
        root.mkdir()
        (root / "ok.txt").write_text("clean", encoding="utf-8")
        # A symlink under the tree that points OUTSIDE it (at a fake raw file).
        outside = tmp_path / "raw_outside.jsonl"
        outside.write_text("phi", encoding="utf-8")
        (root / "leak").symlink_to(outside)

        with pytest.raises(SnapshotError, match="escapes the tree"):
            _assert_no_escaping_symlinks(root)

    def test_in_tree_symlink_is_allowed(self, tmp_path: Path) -> None:
        from scripts.utils.snapshot import _assert_no_escaping_symlinks

        root = tmp_path / "llm_source"
        (root / "sub").mkdir(parents=True)
        target = root / "sub" / "real.txt"
        target.write_text("clean", encoding="utf-8")
        (root / "alias").symlink_to(target)  # stays inside root → fine
        _assert_no_escaping_symlinks(root)  # must not raise


# ── PHI-safe error surfaces (GR-1): a bad date never leaks its raw value ───────


class TestMalformedDatePhiSafe:
    def test_unparseable_date_quarantines_without_leaking_value(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path
    ) -> None:
        _write_config(scrub_config_path)
        secret = "31-FEB-9999-SECRET"  # unparseable; must never appear in any output
        src = _seed_staging([{"SUBJID": "S1", "VISDAT": secret}])
        # Default partial-on-review path quarantines the bad row rather than aborting.
        phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)

        # The raw value must not survive into the published staging file.
        published = src.read_text(encoding="utf-8")
        assert secret not in published, "unparseable date value leaked into output"

        # Nor into any audit artifact (counts/shape only — GR-1).
        audit_root = Path(config.AUDIT_SCRUB_REPORT_PATH).parent
        for p in audit_root.rglob("*.json"):
            assert secret not in p.read_text(encoding="utf-8"), f"value leaked into {p.name}"


# ── orphan rows are quarantined, never published ──────────────────────────────


class TestOrphanQuarantine:
    def test_rows_without_subject_id_are_not_published(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path
    ) -> None:
        _write_config(scrub_config_path, orphan_quarantine_threshold=100)
        rows = [
            {"SUBJID": "S1", "VISDAT": "2014-07-15"},
            {"SUBJID": "", "VISDAT": "2014-07-16"},  # orphan: blank subject id
            {"VISDAT": "2014-07-17"},  # orphan: missing subject id
        ]
        src = _seed_staging(rows)
        phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)

        kept = _clean_rows(src)
        # The one valid row survives (pseudonymized); the two orphans do not.
        assert len(kept) == 1
        assert str(kept[0]["SUBJID"]).startswith("RID_SUBJ_")


# ── non-UTF8 staging input fails closed (no silent clean publish) ─────────────


class TestNonUtf8FailsClosed:
    def test_non_utf8_bytes_do_not_silently_publish(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path
    ) -> None:
        _write_config(scrub_config_path)
        # Invalid UTF-8 continuation bytes inside an otherwise JSON-ish line.
        _seed_staging(None, raw_bytes=b'{"SUBJID": "S1", "X": "\xff\xfe"}\n')
        # Must NOT complete as a clean no-error publish: either it raises, or the
        # bad row is quarantined — never silently emitted as clean output.
        raised = False
        try:
            phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)
        except (UnicodeDecodeError, ValueError, phi_scrub.PHIScrubError):
            raised = True
        staging = config.STAGING_DATASETS_DIR / "1A_ICScreening.jsonl"
        survived = staging.read_bytes() if staging.is_file() else b""
        assert raised or b"\xff\xfe" not in survived, "non-UTF8 row silently published"


# ── large file scrubs within a linear memory budget ───────────────────────────


class TestBoundedMemory:
    def test_large_file_scrub_is_linear_memory(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path
    ) -> None:
        import tracemalloc

        _write_config(scrub_config_path)
        n = 20_000
        rows = [{"SUBJID": f"S{i:06d}", "VISDAT": "2014-07-15"} for i in range(n)]
        src = _seed_staging(rows)
        assert src.stat().st_size > 500_000

        tracemalloc.start()
        try:
            phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # Generous linear budget (~4 KB peak python alloc per row). Real cost is
        # far lower; a quadratic / repeated-whole-file regression would blow past it.
        assert peak < n * 4_096, f"peak {peak} exceeds linear budget for {n} rows"
        assert len(_clean_rows(src)) == n
