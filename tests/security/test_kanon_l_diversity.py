"""Phase 3.A + 3.B regression tests — k-anonymity + l-diversity gates.

Pins three contracts:

1. ``l_diversity_check`` blocks when any equivalence class shares the
   same value for a sensitive attribute across ≥ k members (homogeneity
   attack against a k-anonymous release).
2. ``guard_rows_with_kanon_and_ldiv`` runs k-anon then l-diversity in
   sequence, blocking on either gate.
3. ``query_dataset`` actually invokes the gate before serializing rows
   (regression for the 2026-04-27 audit finding that the helper
   existed but no production tool called it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ai_assistant.phi_safe import guard_rows_with_kanon_and_ldiv
from scripts.security.kanon_gate import (
    l_diversity_check,
)

# ── l_diversity_check ───────────────────────────────────────────────────────


def test_l_diversity_passes_when_classes_are_diverse() -> None:
    """5 rows in one class, 2 distinct outcomes → l=2 passes."""
    rows = [
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "DIED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "DIED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
    ]
    result = l_diversity_check(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=("OUTCOME",),
        l_threshold=2,
    )
    assert result.blocked is False
    assert result.smallest_diversity == 2


def test_l_diversity_blocks_homogeneous_class() -> None:
    """All 5 rows in one class share OUTCOME=DIED → l=2 blocks."""
    rows = [
        {"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"},
        {"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"},
        {"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"},
        {"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"},
        {"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"},
    ]
    result = l_diversity_check(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=("OUTCOME",),
        l_threshold=2,
    )
    assert result.blocked is True
    assert result.smallest_diversity == 1
    assert ("65+|M", "OUTCOME") in result.violating_classes


def test_l_diversity_empty_rows_passes() -> None:
    """No rows → not blocked (caller decides about empty)."""
    result = l_diversity_check(
        [],
        quasi_identifiers=("AGE",),
        sensitive_attributes=("OUTCOME",),
        l_threshold=2,
    )
    assert result.blocked is False


def test_l_diversity_validates_inputs() -> None:
    rows = [{"x": 1}]
    with pytest.raises(ValueError):
        l_diversity_check(rows, quasi_identifiers=(), sensitive_attributes=("x",))
    with pytest.raises(ValueError):
        l_diversity_check(rows, quasi_identifiers=("x",), sensitive_attributes=())
    with pytest.raises(ValueError):
        l_diversity_check(
            rows,
            quasi_identifiers=("x",),
            sensitive_attributes=("y",),
            l_threshold=0,
        )


# ── guard_rows_with_kanon_and_ldiv ──────────────────────────────────────────


def test_guard_passes_when_both_gates_satisfied() -> None:
    rows = [
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "DIED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "DIED"},
        {"AGE": "25-34", "SEX": "F", "OUTCOME": "CURED"},
    ]
    surfaced, kanon, ldiv = guard_rows_with_kanon_and_ldiv(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=("OUTCOME",),
        k=5,
        l_threshold=2,
        tool_name="test",
    )
    assert len(surfaced) == 5
    assert kanon.blocked is False
    assert ldiv is not None and ldiv.blocked is False


def test_guard_blocks_on_kanon() -> None:
    """3 rows in a single class with k=5 → k-anon blocks; l-diversity
    is never reached, so its result is None (we short-circuit)."""
    rows = [
        {"AGE": "70+", "SEX": "M", "OUTCOME": "CURED"},
        {"AGE": "70+", "SEX": "M", "OUTCOME": "CURED"},
        {"AGE": "70+", "SEX": "M", "OUTCOME": "DIED"},
    ]
    surfaced, kanon, ldiv = guard_rows_with_kanon_and_ldiv(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=("OUTCOME",),
        tool_name="test",
    )
    assert surfaced == []
    assert kanon.blocked is True
    assert ldiv is None  # short-circuited


def test_guard_blocks_on_ldiv_after_kanon_passes() -> None:
    """k-anon passes (5 rows, single class) but homogeneity blocks."""
    rows = [{"AGE": "65+", "SEX": "M", "OUTCOME": "DIED"} for _ in range(5)]
    surfaced, kanon, ldiv = guard_rows_with_kanon_and_ldiv(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=("OUTCOME",),
        tool_name="test",
    )
    assert surfaced == []
    assert kanon.blocked is False
    assert ldiv is not None and ldiv.blocked is True


def test_guard_skips_ldiv_when_no_sensitive_attributes() -> None:
    """``sensitive_attributes=None`` reverts to k-anon-only behavior."""
    rows = [{"AGE": "25-34", "SEX": "F"} for _ in range(5)]
    surfaced, kanon, ldiv = guard_rows_with_kanon_and_ldiv(
        rows,
        quasi_identifiers=("AGE", "SEX"),
        sensitive_attributes=None,
        tool_name="test",
    )
    assert len(surfaced) == 5
    assert kanon.blocked is False
    assert ldiv is None


# ── query_dataset integration ───────────────────────────────────────────────


def _seed_trio_with_rows(
    tmp_path: Path, rows: list[dict[str, Any]], dataset: str = "1A_ICScreening"
) -> None:
    """Write *rows* to a tmp trio_bundle/datasets/{dataset}.jsonl + monkeypatch
    config to point at it."""
    ds_dir = tmp_path / "trio_bundle" / "datasets"
    ds_dir.mkdir(parents=True)
    (ds_dir / f"{dataset}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )


def test_query_dataset_blocks_when_a_filter_returns_a_small_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for 2026-04-27 audit: ``query_dataset`` MUST invoke the
    k-anon gate. Filter to a single rare combination → result must be
    suppressed with a ``kanon_violation`` envelope, not raw rows."""
    import config

    rows = [
        {"SUBJID": f"SUBJ-{i}", "AGEY": 30, "IS_SEX": "F", "OUTCOME": "CURED"} for i in range(20)
    ] + [{"SUBJID": "SUBJ-99", "AGEY": 88, "IS_SEX": "M", "OUTCOME": "DIED"}]
    _seed_trio_with_rows(tmp_path, rows)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", tmp_path / "trio_bundle" / "datasets")
    # Patch the zone marker so the unit test isn't fighting the
    # frozen-at-import production zone (the actual zone enforcement is
    # exercised by tests/test_secure_env.py + tests/test_file_access.py).
    import scripts.ai_assistant.agent_tools as ag

    monkeypatch.setattr(ag, "assert_output_zone", lambda _p: None)
    monkeypatch.setattr(ag, "validate_agent_read", lambda p: p)

    from scripts.ai_assistant.agent_tools import query_dataset

    out = json.loads(
        query_dataset.invoke(
            {
                "dataset_name": "1A_ICScreening",
                "filter_column": "AGEY",
                "filter_value": "88",
            }
        )
    )
    assert out["kanon_violation"] is not None
    assert out["kanon_violation"]["gate"] in ("kanon", "l_diversity", "small_filter_cell")
    assert out["records"] == []
    assert "smallest_class_size" in out["kanon_violation"]


def test_query_dataset_passes_through_safe_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A query whose result has every QI class ≥ 5 must surface rows
    normally with ``kanon_violation: null``."""
    import config

    # 20 rows, all in the same QI class — k-anon class size = 20, passes.
    rows = [{"SUBJID": f"SUBJ-{i:03d}", "AGEY": 30, "IS_SEX": "F"} for i in range(20)]
    _seed_trio_with_rows(tmp_path, rows, dataset="2A_Demographics")
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", tmp_path / "trio_bundle" / "datasets")
    import scripts.ai_assistant.agent_tools as ag

    monkeypatch.setattr(ag, "assert_output_zone", lambda _p: None)
    monkeypatch.setattr(ag, "validate_agent_read", lambda p: p)

    from scripts.ai_assistant.agent_tools import query_dataset

    out = json.loads(query_dataset.invoke({"dataset_name": "2A_Demographics"}))
    assert out["kanon_violation"] is None
    assert len(out["records"]) > 0
