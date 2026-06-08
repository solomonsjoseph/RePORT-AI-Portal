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
| `keyword_overlap` | Fraction of lowercase tokens (≥4 chars) in the matching golden report also present in the answer.  Best-effort; `null` when no golden report is mapped |

### Caveats

- Real-model accuracy and latency numbers are only obtainable after operator key
  injection as described above.  This evaluation environment has no API keys and
  cannot run cloud providers.
- `keyword_overlap` is a weak proxy for accuracy.  It measures word-level coverage
  against a human-written reference report, not semantic correctness.
- Golden reports are in `tests/golden/chat_reports/`.  Questions without a mapped
  golden file (e.g. `Q-A2`, `Q-A3`) report `keyword_overlap: null`.
- The agent uses `MemorySaver` with per-run thread IDs (`cloud-eval-{id}`) to prevent
  cross-question memory bleed.
- For the `run_python_analysis` tool to produce real statistical output, the published
  `llm_source/` bundle must be present (`output/{STUDY}/llm_source/`).

---

*Evidence trail: `scripts/eval/cloud_eval.py` (runner) →
`docs/eval/cloud_eval_results.{md,json}` (results).
Track A offline harness: `scripts/eval/retrieval_eval.py` →
`docs/eval/retrieval_eval_results.{md,json}`.*
