"""Offline retrieval-evaluation harness — CLI entry point.

Usage::

    uv run --all-groups python -m scripts.eval.retrieval_eval

Produces
--------
docs/eval/retrieval_eval_results.md
docs/eval/retrieval_eval_results.json

Three measurements
------------------
(a) RETRIEVAL RESOLVABILITY
    For each question confirm — METADATA ONLY — that every source_file
    exists under config.STUDY_LLM_SOURCE_DIR and every backing_column
    NAME appears in the corresponding dataset schema JSON, SoT joined-view
    YAML, or study_variable_map.yaml.  All reads are routed through
    scripts.ai_assistant.file_access.validate_agent_read.

(b) RETRIEVAL-PRIMITIVE LATENCY (time.perf_counter, >=20 iters)
    Direct read: schema-JSON column grep + JSONL header-key scan
                 (first-line keys only — NEVER prints values).
    Simulated RAG baseline: in-memory numpy embedding matrix
    (fixed seed, ~500 chunks x 384 dims) — illustrative only.

(c) TOOL-CALL ROUTING CORRECTNESS (fake-local, deterministic, no network)
    3 probe questions targeting the 3 routing branches in
    _FakeLocalChatModel._generate.  Reports accuracy = correct/total.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root is on sys.path when run with -m
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402 — must come after sys.path patch
from scripts.ai_assistant.file_access import validate_agent_read  # noqa: E402
from scripts.eval.eval_questions import EVAL_QUESTIONS, EvalQuestion  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCS_EVAL_DIR = _REPO_ROOT / "docs" / "eval"
_ITERS = 20  # minimum iterations for latency benchmarks


# ---------------------------------------------------------------------------
# (a) Resolvability helpers
# ---------------------------------------------------------------------------


def _bundle_present() -> bool:
    """Return True when the published llm_source/ bundle exists."""
    return config.STUDY_LLM_SOURCE_DIR.is_dir()


def _schema_columns(form_stem: str) -> set[str]:
    """Return the column-name set from SoT/<form>/dataset/<form>_schema.json.

    Falls back to reading the FIRST LINE KEYS of the dataset JSONL when no
    schema JSON exists for the form.  Never reads row values.
    """
    sot_schema = (
        config.STUDY_LLM_SOURCE_DIR / "SoT" / form_stem / "dataset" / f"{form_stem}_schema.json"
    )
    if sot_schema.exists():
        validate_agent_read(sot_schema)
        data = json.loads(sot_schema.read_text(encoding="utf-8"))
        return {col["name"] for col in data.get("columns", [])}

    # Fallback: first-line KEYS of the dataset JSONL (header metadata only)
    jsonl_path = config.TRIO_DATASETS_DIR / f"{form_stem}.jsonl"
    if jsonl_path.exists():
        validate_agent_read(jsonl_path)
        with jsonl_path.open(encoding="utf-8") as fh:
            first_line = fh.readline()
        if first_line.strip():
            return set(json.loads(first_line).keys())
    return set()


def _sot_joined_columns(form_stem: str) -> set[str]:
    """Return variable IDs from a SoT joined-query-view YAML."""
    joined_dir = config.STUDY_LLM_SOURCE_DIR / "SoT" / form_stem / "joined"
    if not joined_dir.exists():
        return set()
    cols: set[str] = set()
    for yaml_path in joined_dir.glob("*.yaml"):
        validate_agent_read(yaml_path)
        text = yaml_path.read_text(encoding="utf-8")
        # Extract variable keys: lines starting with two spaces + a token + ":"
        # This is metadata-only string scanning, no YAML library needed for names.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.endswith(":") and " " not in stripped.rstrip(":"):
                cols.add(stripped.rstrip(":"))
    return cols


def _study_variable_map_columns() -> set[str]:
    """Return all column names mentioned in study_variable_map.yaml."""
    svm_path = config.LLM_SOURCE_STUDY_METADATA_DIR / "study_variable_map.yaml"
    if not svm_path.exists():
        return set()
    validate_agent_read(svm_path)
    text = svm_path.read_text(encoding="utf-8")
    cols: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("column:"):
            val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                cols.add(val)
    return cols


def _dictionary_columns(form_stem: str) -> set[str]:
    """Return column-name tokens from the dictionary JSONL (header keys only)."""
    dict_path = config.DICTIONARY_JSON_OUTPUT_DIR / f"{form_stem}.jsonl"
    if not dict_path.exists():
        return set()
    validate_agent_read(dict_path)
    cols: set[str] = set()
    with dict_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    rec = json.loads(line)
                    # Dictionary records typically have a 'variable' or 'field' key
                    for key in ("variable", "field", "var_name", "column"):
                        if key in rec:
                            cols.add(str(rec[key]))
                except json.JSONDecodeError:
                    pass
    return cols


def _resolve_question(q: EvalQuestion) -> dict[str, Any]:
    """Check one question's resolvability.  Returns a per-question result dict."""
    resolved_files: list[str] = []
    missing_files: list[str] = []
    resolved_cols: list[str] = []
    missing_cols: list[str] = []

    # --- File existence checks -------------------------------------------
    for rel in q.source_files:
        abs_path = config.STUDY_LLM_SOURCE_DIR / rel
        try:
            validate_agent_read(abs_path)
            if abs_path.exists():
                resolved_files.append(rel)
            else:
                missing_files.append(rel)
        except Exception:
            missing_files.append(rel)

    # --- Column presence checks ------------------------------------------
    # Build a combined column universe from all source files
    col_universe: set[str] = set()
    col_universe |= _study_variable_map_columns()

    for rel in q.source_files:
        # Determine form stem from path
        abs_path = config.STUDY_LLM_SOURCE_DIR / rel
        if not abs_path.exists():
            continue

        # dataset JSONL or schema JSON
        if rel.startswith("dataset_schema/files/") and rel.endswith(".jsonl"):
            stem = Path(rel).stem
            col_universe |= _schema_columns(stem)
            col_universe |= _sot_joined_columns(stem)
            col_universe |= _dictionary_columns(stem)
        elif rel.startswith("SoT/") and "joined" in rel:
            parts = rel.split("/")
            if len(parts) >= 2:
                form_stem = parts[1]
                col_universe |= _sot_joined_columns(form_stem)
                col_universe |= _schema_columns(form_stem)
        elif rel.startswith("dictionary_mapping/jsonl/"):
            validate_agent_read(abs_path)
            with abs_path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        try:
                            rec = json.loads(line)
                            for key in ("variable", "field", "var_name", "column"):
                                if key in rec:
                                    col_universe.add(str(rec[key]))
                        except json.JSONDecodeError:
                            pass
        elif rel.endswith(".yaml") and "study_metadata" in rel:
            col_universe |= _study_variable_map_columns()

    for col in q.backing_columns:
        if col in col_universe:
            resolved_cols.append(col)
        else:
            missing_cols.append(col)

    total_files = len(q.source_files)
    total_cols = len(q.backing_columns)
    file_score = len(resolved_files) / total_files if total_files else 1.0
    col_score = len(resolved_cols) / total_cols if total_cols else 1.0
    overall = (file_score + col_score) / 2.0

    return {
        "id": q.id,
        "kind": q.kind,
        "file_score": round(file_score, 4),
        "col_score": round(col_score, 4),
        "overall_score": round(overall, 4),
        "resolved_files": resolved_files,
        "missing_files": missing_files,
        "resolved_cols": resolved_cols,
        "missing_cols": missing_cols,
    }


# ---------------------------------------------------------------------------
# (b) Latency benchmarks
# ---------------------------------------------------------------------------


def _bench_direct_read() -> dict[str, Any]:
    """Measure direct-read cost: schema-JSON column grep + JSONL header scan.

    Returns p50 and p95 in seconds across _ITERS repetitions.
    Strategy: pick a representative dataset schema and scan it N times.
    NO row values are read — only the first JSON line (schema) or the first
    JSONL line (header keys).
    """

    # Two operations interleaved: (1) read schema JSON and scan column names;
    # (2) open a JSONL and read the first-line keys only.
    schema_path = (
        config.STUDY_LLM_SOURCE_DIR
        / "SoT"
        / "2A_ICBaseline"
        / "dataset"
        / "2A_ICBaseline_schema.json"
    )
    jsonl_path = config.TRIO_DATASETS_DIR / "2A_ICBaseline.jsonl"

    schema_ok = schema_path.exists()
    jsonl_ok = jsonl_path.exists()

    if not schema_ok and not jsonl_ok:
        return {"p50_s": None, "p95_s": None, "note": "bundle absent"}

    timings: list[float] = []
    for _ in range(_ITERS):
        t0 = time.perf_counter()
        # (1) schema JSON column scan
        if schema_ok:
            validate_agent_read(schema_path)
            data = json.loads(schema_path.read_text(encoding="utf-8"))
            col_names = {c["name"] for c in data.get("columns", [])}
            _ = "IC_WEIGHT" in col_names
        # (2) JSONL header-keys read (first line only)
        if jsonl_ok:
            validate_agent_read(jsonl_path)
            with jsonl_path.open(encoding="utf-8") as fh:
                keys = list(json.loads(fh.readline()).keys())
            _ = "IC_WEIGHT" in keys
        timings.append(time.perf_counter() - t0)

    timings.sort()
    n = len(timings)
    p50 = timings[n // 2]
    p95 = timings[min(int(n * 0.95), n - 1)]
    return {"p50_s": round(p50, 6), "p95_s": round(p95, 6)}


def _bench_simulated_rag() -> dict[str, Any]:
    """Simulate a vector-search RAG baseline — illustrative, NOT production code.

    Builds an in-memory numpy embedding matrix (~500 chunks x 384 dims) with
    a fixed seed and measures:
      - index_build_s: one-time matrix construction + L2-normalise
      - per_query_p50_s / per_query_p95_s: cosine search (random query vec,
        argpartition top-5) across _ITERS repetitions

    These numbers are ILLUSTRATIVE.  A real RAG stack pays additional costs
    for embedding model inference, network, and serialisation.  The direct-read
    path cannot be meaningfully compared on per-query speed alone because it
    returns EXACT computable values while a vector search returns approximate
    nearest neighbours.
    """

    try:
        import numpy as np
    except ImportError:
        return {
            "index_build_s": None,
            "per_query_p50_s": None,
            "per_query_p95_s": None,
            "note": "numpy not available",
        }

    n_chunks = 500
    dim = 384

    # Index build (one-time cost proxy)
    t0 = time.perf_counter()
    rng = np.random.default_rng(0)
    matrix = rng.standard_normal((n_chunks, dim)).astype(np.float32)
    # L2-normalise (required for cosine similarity)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    matrix /= norms
    index_build_s = time.perf_counter() - t0

    # Per-query cost (random query vector, top-5 cosine search)
    query_rng = np.random.default_rng(42)
    timings: list[float] = []
    for _ in range(_ITERS):
        q_vec = query_rng.standard_normal(dim).astype(np.float32)
        q_vec /= np.linalg.norm(q_vec) or 1.0
        t0 = time.perf_counter()
        scores = matrix @ q_vec
        _ = np.argpartition(scores, -5)[-5:]
        timings.append(time.perf_counter() - t0)

    timings.sort()
    n = len(timings)
    p50 = timings[n // 2]
    p95 = timings[min(int(n * 0.95), n - 1)]

    return {
        "index_build_s": round(index_build_s, 6),
        "per_query_p50_s": round(p50, 6),
        "per_query_p95_s": round(p95, 6),
        "note": (
            "ILLUSTRATIVE ONLY — in-memory numpy simulation of embedding-matrix "
            "construction and cosine search.  Does NOT include embedding model "
            "inference, network, or serialisation costs.  Cannot yield exact "
            "statistics; direct read cannot be meaningfully compared on speed alone."
        ),
    }


# ---------------------------------------------------------------------------
# (c) Fake-local routing correctness
# ---------------------------------------------------------------------------

_ROUTING_PROBES = [
    {
        "id": "R1",
        "question": "list available datasets",
        "expected_tool": "list_available_datasets",
        "branch": "dataset + list|available|show",
    },
    {
        "id": "R2",
        "question": "how many records and stats are in the Cohort A baseline dataset?",
        "expected_tool": "get_dataset_stats",
        "branch": "stat|record|row",
    },
    {
        "id": "R3",
        "question": "What is HIV_HIV and how is it coded?",
        "expected_tool": "answer_catalog_question",
        "branch": "non-empty fallback (catalog)",
    },
]


def _run_routing_eval(monkeypatch_env: bool = True) -> dict[str, Any]:
    """Drive the fake-local agent on 3 probes and report routing accuracy.

    All three probes correspond to deterministic branches in
    _FakeLocalChatModel._generate (agent_graph.py lines 179/181/183).
    No network calls are made.
    """
    try:
        from langchain_core.messages import HumanMessage, ToolMessage

        import config as _config
    except ImportError as exc:
        return {
            "probes": [],
            "correct": 0,
            "total": 0,
            "accuracy": None,
            "error": f"Import error: {exc}",
        }

    # Set the env-var gate that allows the fake-local provider
    os.environ["REPORTAL_TEST_FAKE_LLM"] = "1"

    # Use the live llm_source bundle when present so security zone checks
    # (assert_output_zone, validate_agent_read) resolve against the real
    # output/ tree.  When the bundle is absent we still run the routing
    # probe — the tool may return an error message, but we measure
    # whether the tool WAS CALLED, not whether it succeeded.
    import scripts.ai_assistant.agent_graph as ag

    orig_provider = _config.LLM_PROVIDER
    orig_model = _config.LLM_MODEL

    _config.LLM_PROVIDER = "fake-local"
    _config.LLM_MODEL = "fake-local"

    # Force agent_graph module to re-build with the fake provider
    ag._agent = None  # type: ignore[attr-defined]
    ag._checkpointer = None  # type: ignore[attr-defined]

    probe_results = []
    correct = 0

    try:
        for probe in _ROUTING_PROBES:
            try:
                agent = ag.get_agent()
                result = agent.invoke(
                    {"messages": [HumanMessage(content=probe["question"])]},
                    config={"configurable": {"thread_id": f"eval-routing-{probe['id']}"}},
                )
                # ToolMessage.name is set to the tool name even on tool-execution
                # errors — we measure WHETHER the tool was called, not its return.
                tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
                fired = [msg.name for msg in tool_messages]
                fired_primary = fired[0] if fired else None
                match = fired_primary == probe["expected_tool"]
                if match:
                    correct += 1
                probe_results.append(
                    {
                        "id": probe["id"],
                        "question": probe["question"],
                        "expected_tool": probe["expected_tool"],
                        "fired_tool": fired_primary,
                        "pass": match,
                        "branch": probe["branch"],
                    }
                )
            except Exception as exc:
                probe_results.append(
                    {
                        "id": probe["id"],
                        "question": probe["question"],
                        "expected_tool": probe["expected_tool"],
                        "fired_tool": None,
                        "pass": False,
                        "error": str(exc),
                        "branch": probe["branch"],
                    }
                )
    finally:
        # Always restore
        _config.LLM_PROVIDER = orig_provider
        _config.LLM_MODEL = orig_model
        ag._agent = None  # type: ignore[attr-defined]
        ag._checkpointer = None  # type: ignore[attr-defined]

    total = len(_ROUTING_PROBES)
    return {
        "probes": probe_results,
        "correct": correct,
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "note": (
            "fake-local exercises the ROUTING CONTRACT (3 deterministic branches). "
            "Full 10-tool selection accuracy requires a real model (Track B, "
            "operator-run)."
        ),
    }


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_report(
    resolvability: list[dict[str, Any]] | None,
    latency_direct: dict[str, Any],
    latency_rag: dict[str, Any],
    routing: dict[str, Any],
    bundle_present: bool,
) -> None:
    DOCS_EVAL_DIR.mkdir(parents=True, exist_ok=True)

    # --- JSON output --------------------------------------------------------
    payload: dict[str, Any] = {
        "bundle_present": bundle_present,
        "resolvability": resolvability,
        "latency_direct_read": latency_direct,
        "latency_simulated_rag": latency_rag,
        "routing_correctness": routing,
    }
    json_path = DOCS_EVAL_DIR / "retrieval_eval_results.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # --- Markdown report ----------------------------------------------------
    lines: list[str] = []
    lines.append("# Retrieval Evaluation Results\n")
    lines.append(f"Bundle present: **{bundle_present}**\n")

    # -- (a) Resolvability ---------------------------------------------------
    lines.append("## (a) Retrieval Resolvability\n")
    if not bundle_present or resolvability is None:
        lines.append("_bundle absent — resolvability skipped_\n")
    else:
        stat_scores = [r["overall_score"] for r in resolvability if r["kind"] == "statistical"]
        def_scores = [r["overall_score"] for r in resolvability if r["kind"] == "definitional"]
        all_scores = [r["overall_score"] for r in resolvability]
        overall_mean = sum(all_scores) / len(all_scores) if all_scores else 0.0
        stat_mean = sum(stat_scores) / len(stat_scores) if stat_scores else 0.0
        def_mean = sum(def_scores) / len(def_scores) if def_scores else 0.0

        lines.append(
            f"- **Overall mean score**: {overall_mean:.4f} ({len(all_scores)} questions)\n"
        )
        lines.append(
            f"- **Statistical questions**: {stat_mean:.4f} ({len(stat_scores)} questions)\n"
        )
        lines.append(
            f"- **Definitional questions**: {def_mean:.4f} ({len(def_scores)} questions)\n"
        )
        lines.append("\n### Per-question breakdown\n")
        lines.append(
            "| ID | Kind | File score | Col score | Overall | Missing files | Missing cols |\n"
        )
        lines.append(
            "|----|------|-----------|-----------|---------|---------------|-------------|\n"
        )
        for r in resolvability:
            mf = ", ".join(r["missing_files"]) or "—"
            mc = ", ".join(r["missing_cols"]) or "—"
            lines.append(
                f"| {r['id']} | {r['kind']} | {r['file_score']:.4f} | "
                f"{r['col_score']:.4f} | {r['overall_score']:.4f} | "
                f"{mf} | {mc} |\n"
            )
        lines.append("\n")

    # -- (b) Latency --------------------------------------------------------
    lines.append("## (b) Retrieval-Primitive Latency\n")
    if latency_direct.get("p50_s") is not None:
        lines.append("### Direct read (schema-JSON column scan + JSONL header-key read)\n")
        lines.append(f"- p50: **{latency_direct['p50_s'] * 1000:.3f} ms**\n")
        lines.append(f"- p95: **{latency_direct['p95_s'] * 1000:.3f} ms**\n")
        lines.append(f"- Iterations: {_ITERS}\n\n")
    else:
        lines.append("Direct read: _bundle absent — skipped_\n\n")

    lines.append("### Simulated RAG baseline (illustrative — see caveats)\n")
    if latency_rag.get("index_build_s") is not None:
        lines.append(
            f"- Index build (one-time): **{latency_rag['index_build_s'] * 1000:.3f} ms**\n"
        )
        lines.append(f"- Per-query p50: **{latency_rag['per_query_p50_s'] * 1000:.4f} ms**\n")
        lines.append(f"- Per-query p95: **{latency_rag['per_query_p95_s'] * 1000:.4f} ms**\n")
    else:
        lines.append(f"- {latency_rag.get('note', 'unavailable')}\n")

    lines.append(
        "\n> **Headline finding**: direct read returns EXACT computable values "
        "(enabling p-values/regression) with NO index-build or re-embed-on-republish "
        "cost.  The simulated vector search above is an in-memory numpy illustration "
        "of per-query cosine-search cost only; it does NOT include embedding model "
        "inference, network, or serialisation costs, and CANNOT yield exact statistics.  "
        "Speed comparison between the two approaches is therefore not meaningful — "
        "they answer fundamentally different question types.\n\n"
    )

    # -- (c) Routing --------------------------------------------------------
    lines.append("## (c) Tool-Call Routing Correctness (fake-local)\n")
    if routing.get("accuracy") is not None:
        lines.append(
            f"Accuracy: **{routing['correct']}/{routing['total']}** "
            f"({routing['accuracy'] * 100:.1f}%)\n\n"
        )
        lines.append("| Probe | Question | Expected | Fired | Pass |\n")
        lines.append("|-------|----------|----------|-------|------|\n")
        for p in routing.get("probes", []):
            fired = p.get("fired_tool") or p.get("error", "ERROR")
            lines.append(
                f"| {p['id']} | {p['question'][:60]} | "
                f"{p['expected_tool']} | {fired} | "
                f"{'YES' if p['pass'] else 'NO'} |\n"
            )
        lines.append(f"\n> {routing.get('note', '')}\n\n")
    else:
        err = routing.get("error", "unknown error")
        lines.append(f"_Routing eval failed: {err}_\n\n")

    md_path = DOCS_EVAL_DIR / "retrieval_eval_results.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    print(f"Results written to:\n  {json_path}\n  {md_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_eval() -> dict[str, Any]:
    """Run all three measurements and return the result payload."""
    present = _bundle_present()
    print(f"Bundle present: {present}")

    # (a) Resolvability
    resolvability: list[dict[str, Any]] | None
    if present:
        print("Running resolvability scan...")
        resolvability = [_resolve_question(q) for q in EVAL_QUESTIONS]
        stat_scores = [r["overall_score"] for r in resolvability if r["kind"] == "statistical"]
        def_scores = [r["overall_score"] for r in resolvability if r["kind"] == "definitional"]
        all_scores = [r["overall_score"] for r in resolvability]
        print(
            f"  Overall: {sum(all_scores) / len(all_scores):.4f}  "
            f"Statistical: {sum(stat_scores) / len(stat_scores):.4f}  "
            f"Definitional: {sum(def_scores) / len(def_scores):.4f}"
        )
    else:
        print("bundle absent — resolvability skipped")
        resolvability = None

    # (b) Latency
    print("Running direct-read latency benchmark...")
    latency_direct: dict[str, Any]
    if present:
        latency_direct = _bench_direct_read()
        print(
            f"  Direct read p50={latency_direct['p50_s'] * 1000:.3f}ms  "
            f"p95={latency_direct['p95_s'] * 1000:.3f}ms"
        )
    else:
        latency_direct = {"p50_s": None, "p95_s": None, "note": "bundle absent"}

    print("Running simulated RAG latency benchmark...")
    latency_rag = _bench_simulated_rag()
    if latency_rag.get("index_build_s") is not None:
        print(
            f"  RAG index build: {latency_rag['index_build_s'] * 1000:.3f}ms  "
            f"per-query p50={latency_rag['per_query_p50_s'] * 1000:.4f}ms"
        )

    # (c) Routing
    print("Running fake-local routing eval...")
    routing = _run_routing_eval()
    if routing.get("accuracy") is not None:
        print(
            f"  Routing accuracy: {routing['correct']}/{routing['total']} "
            f"({routing['accuracy'] * 100:.1f}%)"
        )
    else:
        print(f"  Routing eval error: {routing.get('error')}")

    _write_report(resolvability, latency_direct, latency_rag, routing, present)

    return {
        "bundle_present": present,
        "resolvability": resolvability,
        "latency_direct_read": latency_direct,
        "latency_simulated_rag": latency_rag,
        "routing_correctness": routing,
    }


if __name__ == "__main__":
    run_eval()
