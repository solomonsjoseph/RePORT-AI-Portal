"""Track B — end-to-end cloud (or fake-local smoke) evaluation runner.

Reuses the same EVAL_QUESTIONS gold table as the offline harness
(scripts.eval.retrieval_eval, Track A) and drives the real LangChain
agent (scripts.ai_assistant.agent_graph.get_agent) for each question.

Usage::

    # Smoke test — no API key required
    REPORTAL_TEST_FAKE_LLM=1 LLM_PROVIDER=fake-local LLM_MODEL=fake-local \\
        uv run --all-groups python -m scripts.eval.cloud_eval --limit 2

    # Real model — inject key first (env vars alone do NOT feed the key;
    # see docs/eval/EVAL_RUNBOOK.md)
    uv run --all-groups python -m scripts.eval.cloud_eval \\
        --model claude-opus-4-8 --limit 5

Produces
--------
docs/eval/cloud_eval_results.json
docs/eval/cloud_eval_results.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root on sys.path when run with -m
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402 — must come after sys.path patch
from scripts.eval.eval_questions import EVAL_QUESTIONS, EvalQuestion  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCS_EVAL_DIR = _REPO_ROOT / "docs" / "eval"

# Tools that count as a valid data/SoT signal
_DATA_TOOLS: frozenset[str] = frozenset(
    {
        "query_dataset",
        "get_dataset_stats",
        "run_python_analysis",
        "answer_catalog_question",
        "cite_source",
        "search_llm_source",
        "read_llm_source_file",
        "list_available_datasets",
        "search_variables",
        # included in case a future agent version exposes it
        "get_study_variable_map",
    }
)

# Cloud providers that require a KeyStore key
_KEY_REQUIRED_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "google-genai", "google", "gemini", "nvidia-ai-endpoints", "nvidia"}
)

# Map question ID to golden-report stem prefix (Q-A1 → "01_", Q-D1 → "05_", etc.)
_QID_TO_GOLDEN_STEM: dict[str, str] = {
    "Q-A1": "01_",
    "Q-A2": "02_",
    "Q-A3": "02_",  # both multivariate/interaction questions share report 02
    "Q-B1": "03_",
    "Q-D1": "05_",
    "Q-D2": "06_",
    "Q-D3": "07_",
    "Q-D4": "08_",
    "Q-D5": "09_",
    "Q-D6": "10_",
}

_GOLDEN_DIR = _REPO_ROOT / "tests" / "golden" / "chat_reports"


# ---------------------------------------------------------------------------
# Preflight: key / provider check
# ---------------------------------------------------------------------------


def _is_fake_local(provider: str) -> bool:
    return provider.strip().lower() == "fake-local"


def _preflight_check(provider: str) -> bool:
    """Return True if it is safe to proceed; False if we must abort.

    Prints a diagnostic message when the key is missing for a cloud provider.
    The gate is intentionally placed before any agent import so that a missing
    key produces a clear operator message rather than an SDK-level exception.
    """
    if _is_fake_local(provider):
        return True

    if provider in _KEY_REQUIRED_PROVIDERS:
        from scripts.ai_assistant.keystore import get_keystore, provider_slug_for

        slug = provider_slug_for(provider)
        if slug and not get_keystore().has(slug):
            # Check whether a conventional env-var fallback is present
            from scripts.ai_assistant.keystore import ENV_VAR_BY_PROVIDER

            env_var = ENV_VAR_BY_PROVIDER.get(slug, "")
            if env_var and os.environ.get(env_var):
                # Env-var is set — the SDK may auto-pick it up depending on
                # the LangChain version, but it bypasses KeyStore.  Warn and
                # continue rather than blocking.
                print(
                    f"[preflight] WARNING: {env_var} is set in os.environ but NOT in the "
                    f"KeyStore.  Env-var injection bypasses the in-process security boundary.  "
                    f"Prefer: KeyStore.set({slug!r}, key) or load via the chat UI key field."
                )
                return True

            print(
                f"\n[preflight] ABORT: provider={provider!r} requires an API key but none is "
                f"loaded in the KeyStore.\n"
                f"\nTo fix (env vars alone do NOT feed the KeyStore):\n"
                f"  Option A — load key via the chat UI key field before running this script.\n"
                f"  Option B — inject programmatically before invoking the runner:\n"
                f"\n"
                f"    from scripts.ai_assistant.keystore import get_keystore\n"
                f"    get_keystore().set({slug!r}, '<YOUR_API_KEY>')\n"
                f"\nThen re-run:\n"
                f"    uv run --all-groups python -m scripts.eval.cloud_eval "
                f"--model {config.LLM_MODEL}\n"
                f"\nSee docs/eval/EVAL_RUNBOOK.md for full instructions.\n"
            )
            return False

    return True


# ---------------------------------------------------------------------------
# Golden-report keyword overlap (best-effort, no PHI)
# ---------------------------------------------------------------------------


def _golden_keywords(qid: str) -> set[str] | None:
    """Return a set of lowercase words from the matching golden report, or None."""
    stem = _QID_TO_GOLDEN_STEM.get(qid)
    if stem is None or not _GOLDEN_DIR.is_dir():
        return None
    matches = sorted(_GOLDEN_DIR.glob(f"{stem}*.md"))
    if not matches:
        return None
    text = matches[0].read_text(encoding="utf-8", errors="replace").lower()
    # Strip markdown syntax; keep alphanumeric tokens ≥4 chars
    tokens = set(re.findall(r"[a-z0-9_]{4,}", text))
    return tokens if tokens else None


def _keyword_overlap(answer: str, golden: set[str]) -> float:
    """Fraction of golden keywords present in the answer text (0-1)."""
    if not golden:
        return 0.0
    answer_tokens = set(re.findall(r"[a-z0-9_]{4,}", answer.lower()))
    hits = golden & answer_tokens
    return round(len(hits) / len(golden), 4)


# ---------------------------------------------------------------------------
# Per-question runner
# ---------------------------------------------------------------------------


def _run_one(q: EvalQuestion, *, thread_prefix: str = "cloud-eval") -> dict[str, Any]:
    """Invoke the agent for one question; return per-question metrics dict."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from scripts.ai_assistant import agent_graph as ag

    agent = ag.get_agent()
    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=q.question)]},
            config={"configurable": {"thread_id": f"{thread_prefix}-{q.id}"}},
        )
        latency_s = time.perf_counter() - t0
    except Exception as exc:
        latency_s = time.perf_counter() - t0
        return {
            "id": q.id,
            "kind": q.kind,
            "latency_s": round(latency_s, 4),
            "tools_called": [],
            "answered": False,
            "tools_used_ok": False,
            "keyword_overlap": None,
            "answer_excerpt": "",
            "error": str(exc),
        }

    # Extract ordered tool-call names from ToolMessages
    tool_names = [msg.name for msg in result["messages"] if isinstance(msg, ToolMessage)]

    # Final answer text (last AIMessage with non-empty content)
    final_answer = ""
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            final_answer = str(msg.content)
            break

    answered = bool(final_answer.strip())
    tools_used_ok = bool(_DATA_TOOLS & set(tool_names))

    # Golden keyword overlap
    golden_kw = _golden_keywords(q.id)
    kw_overlap: float | None = None
    if golden_kw and final_answer:
        kw_overlap = _keyword_overlap(final_answer, golden_kw)

    return {
        "id": q.id,
        "kind": q.kind,
        "latency_s": round(latency_s, 4),
        "tools_called": tool_names,
        "answered": answered,
        "tools_used_ok": tools_used_ok,
        "keyword_overlap": kw_overlap,
        "answer_excerpt": final_answer[:300].replace("\n", " "),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p / 100)
    idx = min(idx, len(sorted_v) - 1)
    return round(sorted_v[idx], 4)


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_report(
    results: list[dict[str, Any]],
    *,
    model: str,
    provider: str,
    is_smoke: bool,
) -> None:
    DOCS_EVAL_DIR.mkdir(parents=True, exist_ok=True)

    latencies = [r["latency_s"] for r in results if r.get("error") is None]
    tool_ok_rate = (
        sum(1 for r in results if r.get("tools_used_ok")) / len(results) if results else 0.0
    )
    answered_rate = sum(1 for r in results if r.get("answered")) / len(results) if results else 0.0

    aggregate: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "run_type": "smoke-fake-local" if is_smoke else "real-model",
        "n_questions": len(results),
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p95_s": _percentile(latencies, 95),
        "tool_usage_rate": round(tool_ok_rate, 4),
        "answered_rate": round(answered_rate, 4),
    }

    payload: dict[str, Any] = {
        "aggregate": aggregate,
        "per_question": results,
    }

    json_path = DOCS_EVAL_DIR / "cloud_eval_results.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # --- Markdown ----------------------------------------------------------
    run_label = "SMOKE (fake-local)" if is_smoke else f"REAL MODEL: {model}"
    lines: list[str] = [
        "# Track B — Cloud Eval Results\n",
        f"\n**Run type**: {run_label}  \n",
        f"**Provider**: {provider}  \n",
        f"**Model**: {model}  \n",
        "\n",
    ]

    if is_smoke:
        lines.append(
            "> **Note**: This run used `fake-local` (no network, no API key).  "
            "Latency and accuracy numbers are smoke-test artefacts only and do NOT "
            "reflect real model performance.  See `docs/eval/EVAL_RUNBOOK.md` to run "
            "with a real model.\n\n"
        )

    lines.append("## Aggregate\n\n")
    lines.append(f"- Questions run: **{aggregate['n_questions']}**\n")
    lines.append(f"- Latency p50: **{aggregate['latency_p50_s'] * 1000:.1f} ms**\n")
    lines.append(f"- Latency p95: **{aggregate['latency_p95_s'] * 1000:.1f} ms**\n")
    lines.append(f"- Tool-usage rate: **{aggregate['tool_usage_rate'] * 100:.1f}%**\n")
    lines.append(f"- Answered rate: **{aggregate['answered_rate'] * 100:.1f}%**\n\n")

    lines.append("## Per-question Results\n\n")
    lines.append(
        "| ID | Kind | Latency (s) | Tools called | Data tool? | KW overlap | Answered | Error |\n"
    )
    lines.append(
        "|----|------|-------------|--------------|------------|------------|----------|-------|\n"
    )
    for r in results:
        tools_str = ", ".join(r["tools_called"]) if r["tools_called"] else "—"
        kw = f"{r['keyword_overlap']:.2f}" if r["keyword_overlap"] is not None else "—"
        err = r.get("error") or "—"
        lines.append(
            f"| {r['id']} | {r['kind']} | {r['latency_s']:.3f} "
            f"| {tools_str[:50]} | {'YES' if r['tools_used_ok'] else 'NO'} "
            f"| {kw} | {'YES' if r['answered'] else 'NO'} | {err[:40]} |\n"
        )

    lines.append("\n## Answer Excerpts\n\n")
    for r in results:
        lines.append(f"### {r['id']} — {r['kind']}\n\n")
        if r.get("error"):
            lines.append(f"**Error**: {r['error']}\n\n")
        elif r["answer_excerpt"]:
            lines.append(f"> {r['answer_excerpt']}\n\n")
        else:
            lines.append("_(no answer text)_\n\n")

    lines.append("\n---\n")
    lines.append(
        "*Track B runner: `scripts/eval/cloud_eval.py` — "
        "results written to `docs/eval/cloud_eval_results.{md,json}`*\n"
    )

    md_path = DOCS_EVAL_DIR / "cloud_eval_results.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    print(f"Results written to:\n  {json_path}\n  {md_path}")


# ---------------------------------------------------------------------------
# Public runner (importable by tests)
# ---------------------------------------------------------------------------


def run_cloud_eval(
    *,
    questions: list[EvalQuestion] | None = None,
    limit: int | None = None,
    thread_prefix: str = "cloud-eval",
) -> dict[str, Any]:
    """Run Track B evaluation and return the result payload.

    Parameters
    ----------
    questions:
        Override the gold table with a custom list (used by tests).
    limit:
        Cap on number of questions to evaluate (applied after filtering).
    thread_prefix:
        Prefix for LangGraph thread IDs (avoids cross-run memory bleed).

    Returns
    -------
    dict with keys ``aggregate`` and ``per_question``.
    """
    provider = config.LLM_PROVIDER
    model = config.LLM_MODEL
    is_smoke = _is_fake_local(provider)

    # Preflight — abort early for cloud providers with no key loaded
    if not _preflight_check(provider):
        sys.exit(1)

    target_questions: list[EvalQuestion] = (
        questions if questions is not None else list(EVAL_QUESTIONS)
    )
    if limit is not None and limit > 0:
        target_questions = target_questions[:limit]

    print(
        f"[cloud_eval] provider={provider!r} model={model!r} "
        f"run_type={'smoke-fake-local' if is_smoke else 'real-model'} "
        f"questions={len(target_questions)}"
    )
    if is_smoke:
        print("[cloud_eval] SMOKE MODE — fake-local provider, no API key required")

    results: list[dict[str, Any]] = []
    for i, q in enumerate(target_questions, 1):
        print(f"  [{i}/{len(target_questions)}] {q.id} ({q.kind}) ... ", end="", flush=True)
        rec = _run_one(q, thread_prefix=thread_prefix)
        status = "OK" if rec.get("error") is None else f"ERROR: {rec['error'][:60]}"
        print(f"{rec['latency_s']:.3f}s  tools={rec['tools_called']}  {status}")
        results.append(rec)

    _write_report(results, model=model, provider=provider, is_smoke=is_smoke)

    latencies = [r["latency_s"] for r in results if r.get("error") is None]
    aggregate: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "run_type": "smoke-fake-local" if is_smoke else "real-model",
        "n_questions": len(results),
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p95_s": _percentile(latencies, 95),
        "tool_usage_rate": round(
            sum(1 for r in results if r.get("tools_used_ok")) / len(results), 4
        )
        if results
        else 0.0,
        "answered_rate": round(sum(1 for r in results if r.get("answered")) / len(results), 4)
        if results
        else 0.0,
    }

    print(
        f"\n[cloud_eval] Done — p50={aggregate['latency_p50_s'] * 1000:.1f}ms  "
        f"p95={aggregate['latency_p95_s'] * 1000:.1f}ms  "
        f"tool_ok={aggregate['tool_usage_rate'] * 100:.0f}%  "
        f"answered={aggregate['answered_rate'] * 100:.0f}%"
    )

    return {"aggregate": aggregate, "per_question": results}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Track B cloud evaluation runner.  "
            "Requires an API key loaded via KeyStore.set() (not os.environ) "
            "unless running with fake-local for smoke testing."
        )
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default=None,
        help=(
            "Override config.LLM_MODEL (e.g. claude-opus-4-8, claude-sonnet-4-6).  "
            "Defaults to config.LLM_MODEL from environment/config.yaml."
        ),
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Evaluate at most N questions (useful for quick smoke tests).",
    )
    parser.add_argument(
        "--questions",
        metavar="IDS",
        default=None,
        help=(
            "Comma-separated question IDs to evaluate (e.g. Q-A1,Q-D1).  "
            "Defaults to all EVAL_QUESTIONS."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.model:
        config.LLM_MODEL = args.model  # type: ignore[attr-defined]
        # Re-infer provider when model is overridden via CLI
        if not os.environ.get("LLM_PROVIDER"):
            config.LLM_PROVIDER = config._infer_provider(args.model)  # type: ignore[attr-defined]

    # Reset the agent singleton so it picks up any config changes
    from scripts.ai_assistant import agent_graph as ag

    ag._agent = None  # type: ignore[attr-defined]
    ag._checkpointer = None  # type: ignore[attr-defined]

    # Filter to requested question IDs if provided
    questions: list[EvalQuestion] | None = None
    if args.questions:
        requested_ids = {qid.strip() for qid in args.questions.split(",") if qid.strip()}
        questions = [q for q in EVAL_QUESTIONS if q.id in requested_ids]
        if not questions:
            print(
                f"[cloud_eval] No matching questions found for IDs: {args.questions}",
                file=sys.stderr,
            )
            sys.exit(1)

    run_cloud_eval(questions=questions, limit=args.limit)


if __name__ == "__main__":
    main()
