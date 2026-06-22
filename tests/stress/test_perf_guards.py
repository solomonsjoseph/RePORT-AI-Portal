"""C4 — Performance guard tests (marker: ``stress``; excluded from the default gate).

Run with::

    uv run --all-groups python -m pytest tests/stress -m stress -q

These guard against two cheap-to-regress performance properties of the scrub:
compiled regex patterns are built ONCE (at config load), not per row, and a
large scrub completes within a generous wall-clock floor. No new benchmarking
framework — a re.compile spy + a loose timing budget.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

import config
from scripts.security import phi_scrub

pytestmark = pytest.mark.stress


def _write_config(path: Path) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump(
            {
                "compliance_posture": "safe_harbor",
                "subject_id_field": "SUBJID",
                "date_fields": ["^VISDAT$", "_DAT$"],
                "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
                "birthdate_field": "^DOB$",
                "max_jitter_days": 30,
                "orphan_quarantine_threshold": 100000,
            }
        ),
        encoding="utf-8",
    )


def _seed(n: int) -> Path:
    staging = config.STAGING_DATASETS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / "1A_ICScreening.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"SUBJID": f"S{i:06d}", "VISDAT": "2014-07-15"}) + "\n")
    return target


def _run_counting_compiles(
    n_rows: int, scrub_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """Run a scrub over *n_rows* and return how many times re.compile was called."""
    _write_config(scrub_config_path)
    _seed(n_rows)
    calls = {"n": 0}
    real_compile = re.compile

    def _counting(pattern: Any, flags: int = 0):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_compile(pattern, flags)

    monkeypatch.setattr(re, "compile", _counting)
    try:
        phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)
    finally:
        monkeypatch.setattr(re, "compile", real_compile)
    return calls["n"]


class TestCompileOnce:
    def test_compile_count_is_independent_of_row_count(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path, monkeypatch
    ) -> None:
        """If patterns were recompiled per row, the re.compile count would scale
        with the number of rows (hundreds of compiles for 400 rows). It must not:
        compilation happens once at config load, so the count stays tiny and does
        NOT grow with row count. A small fixed variance (incidental one-time
        compiles in dependencies) is allowed; row-proportional growth is not."""
        small = _run_counting_compiles(10, scrub_config_path, monkeypatch)
        large = _run_counting_compiles(400, scrub_config_path, monkeypatch)
        # 400 rows scrubbed with fewer than 50 total compiles is only possible if
        # patterns are compiled once, not per row; and the 40x row increase must
        # not drive a proportional compile increase.
        assert large < 50, f"{large} compiles for 400 rows — patterns recompiled per row"
        assert large <= small + 5, (
            f"compile count grew with rows ({small}→{large}) — per-row recompilation"
        )


class TestThroughputFloor:
    def test_large_scrub_completes_within_generous_budget(
        self, monkeypatch_config: Path, sidecar_key: Path, scrub_config_path: Path
    ) -> None:
        """A 5k-row scrub must finish well within a generous wall-clock floor — a
        loose guard against an accidental super-linear slowdown, not a benchmark."""
        _write_config(scrub_config_path)
        src = _seed(5_000)
        start = time.monotonic()
        phi_scrub.run_scrub(study_name="TEST", partial_on_review=True)
        elapsed = time.monotonic() - start
        assert elapsed < 60.0, f"5k-row scrub took {elapsed:.1f}s (>60s floor)"
        kept = [json.loads(line) for line in src.read_text().splitlines() if line]
        assert len(kept) == 5_000
