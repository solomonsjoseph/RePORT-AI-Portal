# Track B — Cloud Eval Results

**Run type**: SMOKE (fake-local)  
**Provider**: fake-local  
**Model**: qwen3:8b  

> **Note**: This run used `fake-local` (no network, no API key).  Latency and accuracy numbers are smoke-test artefacts only and do NOT reflect real model performance.  See `docs/eval/EVAL_RUNBOOK.md` to run with a real model.

## Aggregate

- Questions run: **3**
- Latency p50: **9.3 ms**
- Latency p95: **327.2 ms**
- Tool-usage rate: **100.0%**
- Answered rate: **100.0%**

## Per-question Results

| ID | Kind | Latency (s) | Tools called | Data tool? | KW overlap | Answered | Error |
|----|------|-------------|--------------|------------|------------|----------|-------|
| Q-A1 | statistical | 0.327 | answer_catalog_question | YES | 0.02 | YES | — |
| Q-A2 | statistical | 0.009 | answer_catalog_question | YES | 0.00 | YES | — |
| Q-A3 | statistical | 0.008 | answer_catalog_question | YES | 0.00 | YES | — |

## Answer Excerpts

### Q-A1 — statistical

> Fake local LLM final answer after tool use. Tool `answer_catalog_question` returned the study evidence needed for this query.

### Q-A2 — statistical

> Fake local LLM final answer after tool use. Tool `answer_catalog_question` returned the study evidence needed for this query.

### Q-A3 — statistical

> Fake local LLM final answer after tool use. Tool `answer_catalog_question` returned the study evidence needed for this query.


---
*Track B runner: `scripts/eval/cloud_eval.py` — results written to `docs/eval/cloud_eval_results.{md,json}`*
