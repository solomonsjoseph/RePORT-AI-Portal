# Retrieval Evaluation Results
Bundle present: **True**
## (a) Retrieval Resolvability
- **Overall mean score**: 1.0000 (10 questions)
- **Statistical questions**: 1.0000 (4 questions)
- **Definitional questions**: 1.0000 (6 questions)

### Per-question breakdown
| ID | Kind | File score | Col score | Overall | Missing files | Missing cols |
|----|------|-----------|-----------|---------|---------------|-------------|
| Q-A1 | statistical | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-A2 | statistical | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-A3 | statistical | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-B1 | statistical | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D1 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D2 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D3 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D4 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D5 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |
| Q-D6 | definitional | 1.0000 | 1.0000 | 1.0000 | — | — |

## (b) Retrieval-Primitive Latency
### Direct read (schema-JSON column scan + JSONL header-key read)
- p50: **0.624 ms**
- p95: **0.899 ms**
- Iterations: 20

### Simulated RAG baseline (illustrative — see caveats)
- Index build (one-time): **3.723 ms**
- Per-query p50: **0.0140 ms**
- Per-query p95: **0.3160 ms**

> **Headline finding**: direct read returns EXACT computable values (enabling p-values/regression) with NO index-build or re-embed-on-republish cost.  The simulated vector search above is an in-memory numpy illustration of per-query cosine-search cost only; it does NOT include embedding model inference, network, or serialisation costs, and CANNOT yield exact statistics.  Speed comparison between the two approaches is therefore not meaningful — they answer fundamentally different question types.

## (c) Tool-Call Routing Correctness (fake-local)
Accuracy: **3/3** (100.0%)

| Probe | Question | Expected | Fired | Pass |
|-------|----------|----------|-------|------|
| R1 | list available datasets | list_available_datasets | list_available_datasets | YES |
| R2 | how many records and stats are in the Cohort A baseline data | get_dataset_stats | get_dataset_stats | YES |
| R3 | What is HIV_HIV and how is it coded? | answer_catalog_question | answer_catalog_question | YES |

> fake-local exercises the ROUTING CONTRACT (3 deterministic branches). Full 10-tool selection accuracy requires a real model (Track B, operator-run).

