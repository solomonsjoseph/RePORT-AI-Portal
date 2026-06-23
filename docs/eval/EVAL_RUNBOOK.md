# Eval Runbook

Operational instructions for Track A (offline retrieval) and Track B (real/cloud model)
evaluation harnesses.

---

## Track A — Offline Retrieval Evaluation

Track A measures retrieval resolvability, primitive latency, and fake-local routing
correctness.  It requires no API key and no network access.

```bash
uv run --all-groups python -m scripts.eval.retrieval_eval
```

Results: `docs/eval/retrieval_eval_results.{md,json}`

---

## Track B — Running with a Real / Cloud Model

Track B drives the full LangChain agent for each gold-table question and measures
end-to-end latency, tool-call selection, and (optionally) keyword overlap against
golden chat reports.

### Important: env vars alone do NOT feed the API key

The portal keeps API keys out of `os.environ` to prevent subprocess leakage.  Keys
must be loaded into the in-process `KeyStore`.  Two ways to do this:

**Option A — Chat UI (recommended for interactive sessions)**

Open the chat UI (`make chat`), navigate to the key field in the sidebar, and enter
your API key there.  Then run Track B from the same terminal session that launched
the UI — the process-local `KeyStore` is shared.

**Option B — Programmatic injection before calling the runner**

```python
from scripts.ai_assistant.keystore import get_keystore

# Anthropic example
get_keystore().set("anthropic", "sk-ant-...")

# OpenAI example
get_keystore().set("openai", "sk-...")

# Google / Gemini
get_keystore().set("google", "AIza...")
```

Then in the same Python process (or the same `uv run` invocation):

```bash
uv run --all-groups python -m scripts.eval.cloud_eval --model claude-opus-4-8
```

### Supported providers and model strings

| Provider | `LLM_PROVIDER` value | Example `--model` |
|---|---|---|
| Anthropic | `anthropic` | `claude-opus-4-8`, `claude-sonnet-4-6` |
| OpenAI | `openai` | `gpt-4o`, `gpt-5` |
| Google | `google-genai` | `gemini-pro-3.1`, `gemini-2.0-flash` |
| Ollama (local) | `ollama` | `qwen3:8b` (no key required) |
| NVIDIA | `nvidia-ai-endpoints` | `meta/llama-3.1-70b-instruct` |

For Ollama (operator-managed hardware) no key is required; the binary must be running
locally.

### Running Track B

```bash
# Full run — all 10 gold questions
LLM_PROVIDER=anthropic \
  uv run --all-groups python -m scripts.eval.cloud_eval --model claude-sonnet-4-6

# Quick run — first 3 questions only
LLM_PROVIDER=anthropic \
  uv run --all-groups python -m scripts.eval.cloud_eval --model claude-opus-4-8 --limit 3

# Specific questions only
LLM_PROVIDER=anthropic \
  uv run --all-groups python -m scripts.eval.cloud_eval \
  --model claude-sonnet-4-6 --questions Q-A1,Q-D2,Q-D3
```

Results land in:

- `docs/eval/cloud_eval_results.json` — machine-readable per-question records +
  aggregate latency/accuracy
- `docs/eval/cloud_eval_results.md` — human-readable table with answer excerpts

### Smoke test (no API key, no network)

To verify the runner itself is working without a real model:

```bash
REPORTAL_TEST_FAKE_LLM=1 LLM_PROVIDER=fake-local LLM_MODEL=fake-local \
  uv run --all-groups python -m scripts.eval.cloud_eval --limit 2
```

The result file will be labelled **`run_type: smoke-fake-local`** and the latency /
accuracy numbers will reflect the deterministic fake-local model, not a real LLM.

### CI test (pytest, no network)

```bash
uv run --all-groups python -m pytest tests/eval/test_cloud_eval.py -v
```

The CI tests always use `fake-local` and verify structural correctness only (field
types, file creation, preflight logic).  They never make network calls.

### What the metrics mean

| Metric | Definition |
|---|---|
| `latency_s` | `time.perf_counter()` wall-clock seconds for the full agent round-trip |
| `tools_called` | Ordered list of `ToolMessage.name` values from the agent response |
| `tools_used_ok` | At least one data/SoT tool fired (see `_DATA_TOOLS` in `cloud_eval.py`) |
| `answered` | Final `AIMessage` has non-empty content |
| `answer_score` | **Graded answer correctness (0-1).** `numeric` for statistical questions, `judge` for definitional. `null` when ungraded (no golden / no judge) |
| `answer_score_method` | `numeric` (deterministic table grade), `judge` (LLM-as-judge), `judge-skipped` (smoke run, no judge), or `none` |
| `keyword_overlap` | *Secondary, weak proxy.* Fraction of lowercase tokens (≥4 chars) in the matching golden report also present in the answer.  Retained for continuity; not the headline number |

Aggregate adds `answer_score_mean` (overall), `answer_score_mean_numeric`,
`answer_score_mean_judge`, and `answer_score_n_graded`.

### The graded answer-correctness track (`answer_score`)

`answer_score` replaces `keyword_overlap` as the real accuracy number.  It is computed
two ways depending on question kind (`scripts/eval/answer_grading.py`):

- **Statistical questions → deterministic numeric grading.**  The per-predictor
  results table (`| Predictor | n | Events | Odds ratio | 95% CI | p-value | … |`) is
  parsed out of both the golden chat report and the agent's answer, then compared
  cell-by-cell:
  - odds ratios must match within a **relative** tolerance `OR_REL_TOL` (default 5%);
  - p-values must match within an **absolute** tolerance `P_ABS_TOL` (default 0.01)
    **and** sit on the same side of the 0.05 significance threshold — a significance
    flip fails the row even at a tiny delta, because it inverts the clinical conclusion;
  - a real estimate where the golden suppressed a sub-k=5 cell is a **privacy
    regression**, scored as a miss and listed in `answer_score_detail.privacy_regressions`.
  This path needs no LLM, so it runs in CI and pins the `run_python_analysis` numbers
  against regression (`tests/eval/test_answer_grading.py`).
- **Definitional questions → LLM-as-judge.**  A rubric judge (grounded? complete?
  correct? hallucinated variable caps at 0.5) scores the prose answer 0-1.  The judge
  is provider-agnostic and only runs on a **real-model** Track B run; smoke runs leave
  the score `null` (`answer_score_method: judge-skipped`).

**Reporting discipline:** always report `answer_score_mean` *with the model id and the
date of the run* (both are in the result files).  Never quote a bare "100%": the number
is model- and corpus-specific, and the definitional half depends on a judge model.

### Reproducibility — temperature

The agent now samples at `config.AGENT_TEMPERATURE` (default **0**) so a graded eval is
meaningful: the same question yields the same answer run-to-run.  Override with the
`AGENT_TEMPERATURE` env var for exploratory use; keep it at 0 for eval.

### Caveats

- Real-model accuracy and latency numbers are only obtainable after operator key
  injection as described above.  This evaluation environment has no API keys and
  cannot run cloud providers.
- `keyword_overlap` is a weak proxy retained only as a secondary signal — read
  `answer_score` for accuracy.  Word-level coverage is not semantic correctness.
- Golden reports are in `tests/golden/chat_reports/`.  Questions without a mapped
  golden file report `keyword_overlap: null` and `answer_score: null`.
- The agent uses `MemorySaver` with per-run thread IDs (`cloud-eval-{id}`) to prevent
  cross-question memory bleed.
- For the `run_python_analysis` tool to produce real statistical output, the published
  `llm_source/` bundle must be present (`output/{STUDY}/llm_source/`).

---

*Evidence trail: `scripts/eval/cloud_eval.py` (runner) →
`docs/eval/cloud_eval_results.{md,json}` (results).
Track A offline harness: `scripts/eval/retrieval_eval.py` →
`docs/eval/retrieval_eval_results.{md,json}`.*
