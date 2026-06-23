"""Graded answer-correctness scoring for Track B (Goal B — Steps 1 & 4).

Two graders turn the agent's free-text answer into a real, defensible
``answer_score`` (0-1) instead of the weak ``keyword_overlap`` proxy:

* :func:`grade_statistical` — **deterministic** numeric grading.  Parses the
  per-predictor results table out of both the golden chat report and the
  agent's answer and compares odds ratios / p-values / suppression state with
  tolerances.  No LLM, so it runs in CI and pins the ``run_python_analysis``
  numeric path against regression (Step 4).

* :func:`grade_definitional` — rubric **LLM-as-judge** for prose answers.
  Provider-agnostic: it takes an injectable ``judge`` callable, so the offline
  test suite stubs it and only an operator run with a real model exercises the
  live path.

PHI boundary: graders read only aggregate coefficients that already live in the
published golden reports plus the agent's own answer text.  No row values, no
raw dataset access — consistent with GR-1.

Tuning knobs (the real domain decisions live here):

* ``OR_REL_TOL``  — relative tolerance on the odds ratio (magnitude error).
* ``P_ABS_TOL``   — absolute tolerance on the p-value, AND both values must sit
                    on the same side of ``SIG_THRESHOLD`` (a significance flip
                    inverts the clinical conclusion even at a tiny delta).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tunable grading policy — the domain knobs
# ---------------------------------------------------------------------------

OR_REL_TOL: float = 0.05  # odds ratio: |ans-gold| / max(|gold|, eps) <= this
P_ABS_TOL: float = 0.01  # p-value: |ans-gold| <= this …
SIG_THRESHOLD: float = 0.05  # … AND both on the same side of this threshold
_EPS: float = 1e-9

# Canonical predictor keys we recognise across both tables.  Matching is by
# keyword so "Malnutrition (BMI < 18.5)" and an agent's "malnutrition (BMI<18.5)"
# resolve to the same row.
_PREDICTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "malnutrition": ("malnutrition", "bmi"),
    "diabetes": ("diabetes",),
    "alcohol": ("alcohol",),
    "smoking": ("smoking", "smoke"),
    "age": ("age",),
    "sex": ("sex",),
}

# Tokens that mean "this cell was intentionally suppressed / not estimable".
_SUPPRESSED_TOKENS: frozenset[str] = frozenset({"-", "—", "<5", "<5>", "n/a", "na", ""})


# ---------------------------------------------------------------------------
# Parsed representations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatRow:
    """One predictor's parsed cells.  ``None`` means suppressed/not-estimable."""

    predictor: str
    odds_ratio: float | None
    p_value: float | None
    suppressed: bool


@dataclass
class StatGrade:
    """Result of :func:`grade_statistical`."""

    score: float
    matched: int
    total: int
    per_predictor: dict[str, str] = field(default_factory=dict)
    privacy_regressions: list[str] = field(default_factory=list)
    parse_ok: bool = True


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------


def _canonical_predictor(label: str) -> str | None:
    """Map a free-text predictor label to a canonical key, or None."""
    low = label.lower()
    for key, kws in _PREDICTOR_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return key
    return None


def _is_suppressed(cell: str) -> bool:
    return cell.strip().lower() in _SUPPRESSED_TOKENS


def _parse_float(cell: str) -> float | None:
    """Parse the first numeric token in a cell, or None if suppressed/absent."""
    cell = cell.strip()
    if _is_suppressed(cell):
        return None
    # Pull the first signed decimal (handles "0.839", "p=0.0355", "OR 1.55").
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", cell)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_stat_table(text: str) -> dict[str, StatRow]:
    """Parse a per-predictor results table out of ``text``.

    Accepts the golden chat-report schema
    ``| Predictor | n | Events | Odds ratio | 95% CI | p-value | Note |`` and is
    tolerant of column reordering by locating the Odds-ratio and p-value columns
    from the header row.  Rows whose predictor is unrecognised are skipped.

    Returns a mapping ``canonical_predictor -> StatRow``.  Later duplicate rows
    do not overwrite earlier ones (first table wins).
    """
    rows: dict[str, StatRow] = {}
    or_idx: int | None = None
    p_idx: int | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        lowered = [c.lower() for c in cells]

        # Header row — locate the OR / p-value columns.
        if any("odds ratio" in c for c in lowered) and any("p-value" in c for c in lowered):
            or_idx = next(i for i, c in enumerate(lowered) if "odds ratio" in c)
            p_idx = next(i for i, c in enumerate(lowered) if "p-value" in c)
            continue
        # Separator row (|---|---|).
        if set("".join(cells)) <= {"-", ":", " "}:
            continue
        if or_idx is None or p_idx is None:
            continue
        if max(or_idx, p_idx) >= len(cells):
            continue

        key = _canonical_predictor(cells[0])
        if key is None or key in rows:
            continue

        or_cell, p_cell = cells[or_idx], cells[p_idx]
        suppressed = _is_suppressed(or_cell) and _is_suppressed(p_cell)
        rows[key] = StatRow(
            predictor=key,
            odds_ratio=_parse_float(or_cell),
            p_value=_parse_float(p_cell),
            suppressed=suppressed,
        )
    return rows


# ---------------------------------------------------------------------------
# Statistical grading (deterministic)
# ---------------------------------------------------------------------------


def _or_match(gold: float | None, ans: float | None) -> bool:
    if gold is None or ans is None:
        return gold is None and ans is None
    return abs(ans - gold) / max(abs(gold), _EPS) <= OR_REL_TOL


def _p_match(gold: float | None, ans: float | None) -> bool:
    if gold is None or ans is None:
        return gold is None and ans is None
    same_side = (gold < SIG_THRESHOLD) == (ans < SIG_THRESHOLD)
    return same_side and abs(ans - gold) <= P_ABS_TOL


def grade_statistical(answer: str, golden: str) -> StatGrade:
    """Grade a statistical answer against its golden report.

    Score = fraction of golden predictors the answer reproduces correctly.  A
    predictor passes when its odds ratio and p-value both match within tolerance
    *and* its suppression state matches the golden.  Emitting a real estimate
    where the golden suppressed a sub-k=5 cell is recorded as a
    ``privacy_regression`` and scored as a miss.
    """
    gold_rows = parse_stat_table(golden)
    ans_rows = parse_stat_table(answer)

    if not gold_rows:
        return StatGrade(score=0.0, matched=0, total=0, parse_ok=False)

    matched = 0
    per: dict[str, str] = {}
    privacy: list[str] = []

    for key, g in gold_rows.items():
        a = ans_rows.get(key)
        if a is None:
            per[key] = "missing"
            continue
        # Privacy regression: golden suppressed, answer disclosed a number.
        if g.suppressed and not a.suppressed and (a.odds_ratio is not None):
            per[key] = "privacy_regression"
            privacy.append(key)
            continue
        ok_or = _or_match(g.odds_ratio, a.odds_ratio)
        ok_p = _p_match(g.p_value, a.p_value)
        if ok_or and ok_p:
            matched += 1
            per[key] = "match"
        else:
            bits = []
            if not ok_or:
                bits.append(f"OR {a.odds_ratio} vs {g.odds_ratio}")
            if not ok_p:
                bits.append(f"p {a.p_value} vs {g.p_value}")
            per[key] = "mismatch: " + "; ".join(bits)

    total = len(gold_rows)
    return StatGrade(
        score=round(matched / total, 4),
        matched=matched,
        total=total,
        per_predictor=per,
        privacy_regressions=privacy,
        parse_ok=True,
    )


# ---------------------------------------------------------------------------
# Definitional grading (LLM-as-judge)
# ---------------------------------------------------------------------------

# A judge takes a fully-rendered prompt and returns raw model text.
JudgeFn = Callable[[str], str]

_JUDGE_RUBRIC = """\
You are grading an AI assistant's answer to a question about a clinical study.
You are given the QUESTION, the assistant's ANSWER, and a set of GOLDEN
reference facts extracted from the study's source-of-truth documentation.

Score the ANSWER from 0.0 to 1.0 on these criteria, weighted equally:
  1. Grounded — every claim is supported by the GOLDEN facts (no invention).
  2. Complete — the answer covers the key points present in the GOLDEN facts.
  3. Correct — no statement contradicts the GOLDEN facts.

A hallucinated variable name or an unsupported numeric claim caps the score at
0.5.  Respond with ONLY a single line of JSON:
{{"score": <0.0-1.0>, "rationale": "<one sentence>"}}

QUESTION:
{question}

GOLDEN reference facts:
{golden}

ANSWER:
{answer}
"""


def build_judge_prompt(question: str, answer: str, golden: str) -> str:
    """Render the judge rubric prompt (separated for testability)."""
    return _JUDGE_RUBRIC.format(
        question=question.strip(),
        golden=golden.strip(),
        answer=answer.strip(),
    )


def _parse_judge_response(text: str) -> tuple[float, str]:
    """Pull ``score`` (clamped 0-1) and ``rationale`` from judge output."""
    score: float = 0.0
    rationale = ""
    m = re.search(r'"score"\s*:\s*([-+]?\d*\.?\d+)', text)
    if m:
        try:
            score = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            score = 0.0
    r = re.search(r'"rationale"\s*:\s*"([^"]*)"', text)
    if r:
        rationale = r.group(1)
    return round(score, 4), rationale


def grade_definitional(
    question: str,
    answer: str,
    golden: str,
    judge: JudgeFn,
) -> tuple[float, str]:
    """Grade a definitional answer with an injectable LLM judge.

    ``judge`` is any callable that maps a prompt string to model text — the
    real path wraps :func:`scripts.eval.answer_grading.default_judge`; CI stubs
    it.  Returns ``(score, rationale)``; a judge error scores 0.0 fail-closed.
    """
    if not answer.strip():
        return 0.0, "empty answer"
    prompt = build_judge_prompt(question, answer, golden)
    try:
        raw = judge(prompt)
    except Exception as exc:  # fail-closed — an unscorable answer is not a pass
        return 0.0, f"judge error: {exc}"
    return _parse_judge_response(raw)


def default_judge(model: str | None = None) -> JudgeFn:
    """Build a judge backed by the configured LLM provider.

    Lazily imports the agent's chat-model factory so importing this module never
    forces a LangChain dependency.  Used only on operator (real-model) runs.
    """

    def _judge(prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        import config
        from scripts.ai_assistant.agent_graph import _build_llm

        llm = _build_llm(config.LLM_PROVIDER, model or config.LLM_MODEL)
        resp = llm.invoke([HumanMessage(content=prompt)])
        return str(getattr(resp, "content", resp))

    return _judge
