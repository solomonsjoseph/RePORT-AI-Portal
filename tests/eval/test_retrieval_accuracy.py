"""R5 — labeled retrieval-accuracy regression gate (R0 gold set).

Drives the R0 gold set (``retrieval_gold_set.jsonl``) through the live resolver
(R2 concept index + R3/R4 ``answer_catalog_question``) and requires ZERO misses:

  * concept / outcome / derivation / model questions  → every ``expected_column``
    must be surfaced by the cohort-aware concept index (R2);
  * ``ambiguous`` (cohort unspecified) → ``answer_catalog_question`` must return
    ``needs_clarification`` with the competing columns as candidates (R4);
  * ``definitional`` → at least one ``expected_any`` variable must be surfaced.

The gold set is the labeled denominator that makes "100% retrieval" provable
rather than survivorship bias. Each new miss is fixed by a concept-map entry, a
scoping tweak, or an explicit disambiguation — never by deleting the question.

Skips cleanly when no study is published (CI without ``make study``): the
concept index + catalog need ``output/<study>/llm_source/study_metadata`` and
the SoT joined views. Reads column NAMES / metadata only (GR-1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
import scripts.ai_assistant.agent_tools as agent_tools

_GOLD = Path(__file__).with_name("retrieval_gold_set.jsonl")
# Real published Indo-VAP tree, resolved from the repo root so it is INDEPENDENT
# of any global config repoint a prior suite test may have left behind.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_LS = _REPO_ROOT / "output" / "Indo-VAP" / "llm_source"
_REAL_META = _REAL_LS / "study_metadata"


@pytest.fixture(autouse=True)
def _pin_real_indovap(monkeypatch: pytest.MonkeyPatch):
    """Pin the agent-read config at the real Indo-VAP publish + clear caches.

    The module-level constants and the concept/joined-view/tool caches are
    process-global; an earlier suite test that repoints config or warms a cache
    under a fixture study would otherwise make this integration gate flaky. We
    re-pin to the real publish (repo-root absolute), bust the caches, and skip
    cleanly when no study is published (CI without ``make study``).
    """
    if not (_REAL_META / "study_variable_map.yaml").is_file():
        pytest.skip("Indo-VAP not published (run make study STUDY=Indo-VAP)")
    monkeypatch.setattr(config, "REPO_ROOT", _REPO_ROOT, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", _REAL_META, raising=False)
    monkeypatch.setattr(
        config, "TRIO_DATASETS_DIR", _REAL_LS / "dataset_schema" / "files", raising=False
    )
    agent_tools._CONCEPT_INDEX_CACHE.clear()
    agent_tools._JOINED_VIEW_SUMMARIES_CACHE.clear()
    agent_tools.tool_cache.clear()
    yield
    agent_tools._CONCEPT_INDEX_CACHE.clear()


def _load_gold() -> list[dict]:
    rows: list[dict] = []
    for line in _GOLD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


_GOLD_ROWS = _load_gold()


def test_gold_set_is_wellformed() -> None:
    """The gold set parses and every row has the required shape (runs always)."""
    assert _GOLD_ROWS, "gold set is empty"
    ids = [r["gold_id"] for r in _GOLD_ROWS]
    assert len(ids) == len(set(ids)), "duplicate gold_id"
    for r in _GOLD_ROWS:
        assert r["question"].strip()
        assert r["kind"] in {
            "concept",
            "outcome",
            "derivation",
            "model",
            "ambiguous",
            "definitional",
        }
        assert r.get("expected_columns") or r.get("expected_any")


def _answer(question: str) -> dict:
    fn = agent_tools.answer_catalog_question
    try:
        raw = fn.invoke({"question": question})
    except Exception:
        raw = fn.func(question)  # type: ignore[attr-defined]
    return json.loads(raw)


def _miss(row: dict) -> str | None:
    """Return a miss description for *row*, or None on a hit."""
    q = row["question"]
    kind = row["kind"]
    if kind in {"concept", "outcome", "derivation", "model"}:
        resolved = {c.upper() for c in agent_tools._concept_columns_for_query(q)}
        want = {c.upper() for c in row["expected_columns"]}
        missing = want - resolved
        return f"unresolved columns {sorted(missing)}" if missing else None
    if kind == "ambiguous":
        d = _answer(q)
        if not d.get("needs_clarification"):
            return "expected needs_clarification (ambiguous), got a single pick"
        surfaced = {str(v).upper() for v in d.get("variable_ids", [])}
        surfaced |= {str(c.get("variable_id", "")).upper() for c in d.get("candidates", [])}
        missing = {c.upper() for c in row["expected_columns"]} - surfaced
        return f"ambiguity did not surface {sorted(missing)}" if missing else None
    if kind == "definitional":
        d = _answer(q)
        surfaced = {str(v).upper() for v in d.get("variable_ids", [])}
        surfaced |= {str(c.get("variable_id", "")).upper() for c in d.get("candidates", [])}
        if any(v.upper() in surfaced for v in row["expected_any"]):
            return None
        return f"none of {row['expected_any']} surfaced (got {sorted(surfaced)[:6]})"
    return f"unknown kind {kind}"


@pytest.mark.parametrize("row", _GOLD_ROWS, ids=[r["gold_id"] for r in _GOLD_ROWS])
def test_gold_entry_resolves(row: dict) -> None:
    miss = _miss(row)
    assert miss is None, f"{row['gold_id']} ({row['question']!r}): {miss}"


def test_zero_misses_on_gold_set() -> None:
    """The headline gate: misses across the whole labeled set must be ZERO."""
    misses = [(r["gold_id"], _miss(r)) for r in _GOLD_ROWS]
    failing = [(gid, m) for gid, m in misses if m is not None]
    assert not failing, f"{len(failing)}/{len(_GOLD_ROWS)} gold misses: {failing}"


def test_concept_resolution_is_cohort_scoped() -> None:
    """Index-case vs household-contact concepts must NOT collide (R2 + R3)."""
    a = {
        c.upper() for c in agent_tools._concept_columns_for_query("smoking in index case cohort A")
    }
    b = {
        c.upper()
        for c in agent_tools._concept_columns_for_query("smoking in household contact cohort B")
    }
    assert "IC_SMOKHX" in a and "HC_SMOKHX" not in a
    assert "HC_SMOKHX" in b and "IC_SMOKHX" not in b
