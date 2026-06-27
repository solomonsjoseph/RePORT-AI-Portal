# Comparative Analysis — RePORTal vs. Existing De-identification Systems

**Prepared:** 2026-06-26 · Companion to `phi_benchmark_pi_report.md`
**Sources:** peer-reviewed benchmarks + vendor documentation catalogued in
`docs/manuscript/research_dossier.md` (each row traces to a citation there).

---

## 0. The honest framing first (read this before the tables)

There are **two different de-identification problems**, and most existing tools solve the *other* one:

- **Free-text clinical notes** — discharge summaries, narratives. Probabilistic NLP/LLM territory.
  deid, MIST, NeuroNER, NLM Scrubber, CliniDeID, Philter, Azure DeID, GPT-4 all live here.
- **Structured study data** — CDISC-style tabular CRFs with typed columns (Indo-VAP's actual shape).
  This is RePORTal's target, and it is a *sparsely-mature* ecosystem (ARX, sdcMicro, μ-ARGUS).

**Consequence:** published recall numbers for free-text tools and RePORTal's 100% on structured data
are **measured on different corpora and are not directly comparable.** This document therefore
compares on two separate planes: (A) *architecture/design axes*, where any tool can be honestly
placed, and (B) *published accuracy*, kept corpus-labelled so no false equivalence is implied. The
only genuine apples-to-apples head-to-head — a value scanner on the identical tabular corpus — is the
**Presidio** run, which carries live numbers (§3).

---

## 1. Architectural comparison (the axes that actually differ)

| System | Target modality | Decision type | Raw values exposed to | Fail-closed? | Per-variable audit trail | Stated HIPAA/DPDPA claim |
|---|---|---|---|---|---|---|
| **RePORTal (this work)** | **Structured tabular** | **Deterministic (rule + header)** | **Never to LLM/cloud; row values only inside trusted scrub code** | **Yes** (un-scrubbable row → quarantined, never published) | **Yes** (assertion-enforced, every column) | **HIPAA Safe Harbor + India DPDPA**, by construction |
| Philter | Free-text notes | Probabilistic (recall-prioritised rules) | Local process | No (probabilistic) | No | No formal claim |
| MIST | Free-text notes | Statistical (CRF, trainable) | Local process | No | No | No |
| deid / NeuroNER / NLM Scrubber / CliniDeID | Free-text notes | Probabilistic (rules / NN) | Local process | No | No | No |
| Transformer de-id (e.g. RoBERTa-i2b2) | Free-text notes | Probabilistic (neural) | Local/GPU | No | No | No |
| GPT-4 / LLM de-id | Free-text notes | Probabilistic (generative) | **Raw text to the model** | No (can hallucinate) | No | No |
| AWS Comprehend Medical (DetectPHI) | Free-text | Probabilistic + confidence | **Sent to AWS cloud** | No | No | **Vendor states it does NOT meet HIPAA de-id**; recommends human review |
| Google Cloud Healthcare API | FHIR/DICOM/text | Managed transform | **Google tenancy, poss. cross-region** | No | Limited | No Safe Harbor / DPDPA claim |
| Azure Health Data Services + Presidio | (text) | Remote recognizer | **Sent to Azure service** | No | No | None established |
| Microsoft Presidio (self-hosted) | Free-text + values | Probabilistic recognizers | Local process | No | No | No |
| ARX / sdcMicro / μ-ARGUS | **Structured tabular** | Deterministic SDC (k-anon / suppression) | Local process | Partial (manual) | Partial (manual) | Tool, not a posture claim |

**What the table shows.** RePORTal is the only row that is simultaneously (i) built for *structured*
data, (ii) *deterministic*, (iii) *never* exposes row values to an LLM or cloud service, (iv)
*fail-closed*, and (v) carries a *machine-enforced per-variable audit trail* tied to a named
regulatory posture. The structured SDC tools (ARX/sdcMicro) share the modality and determinism but
are interactive utilities — they impose no fail-closed publish gate and no automated audit/posture
binding. Every probabilistic/cloud row trades a structural guarantee for a statistical estimate.

---

## 2. Published accuracy — kept corpus-labelled (no false equivalence)

These are the incumbents' **own** published numbers, on **free-text** benchmarks. They are listed
for context, **not** as a head-to-head against RePORTal's structured-data result.

| System | Published metric | Corpus | Note |
|---|---|---|---|
| deid | name recall **48.8%** | i2b2-era | lower bound of the field |
| MIST | name recall **66.9%** | i2b2-era | trainable CRF |
| NeuroNER | name recall **84.1%** | i2b2-era | neural |
| NLM Scrubber | name recall **88.1%** | i2b2-era | rules |
| CliniDeID | name recall **95.9%** | i2b2-era | best classical free-text |
| Philter | recall **99.92%**, F2 **94.77%** | i2b2 2014 | recall-prioritised; still probabilistic, not fail-closed |
| Transformer (RoBERTa-i2b2) | F1 **95.5%**, but **59.7%** on the *rare-ID* category | i2b2 2014 | degrades exactly where structured study IDs live |
| Azure DeID | F1 **0.939** | UK NHS (3,650 records) | leading task-specific transformer |
| GPT-4 | misses **~1 in 6** identifiers; can hallucinate | private oncology set | needs raw text |
| **RePORTal** | **recall 100% / precision 100% / 0 leak** | **planted-identifier synthetic, structured** | deterministic; reproducible (`docs/eval/synthetic_phi_benchmark/`) |

**The key cross-read** is the transformer row: a 95.5%-F1 model still falls to **59.7%** on the rare
structured-ID category — Aadhaar, accession numbers, device IDs — which is *precisely* the content of
a clinical CRF. Probabilistic systems are weakest where structured study data is strongest in
identifiers. A deterministic rule + fail-closed gate has no such soft spot: an identifier shape it is
configured for is removed every time, and an un-scrubbable row is held rather than guessed.

---

## 3. The one fair head-to-head: Presidio on the identical structured corpus

Because Presidio runs locally on values, it is the only incumbent that can be pointed at the *same*
tabular corpus as RePORTal. Live re-run (`docs/eval/synthetic_phi_benchmark/score_synthetic.py`,
4,680 cells / 3,422 planted identifiers):

| Metric | **RePORTal** | Presidio |
|---|---|---|
| Recall (identifiers removed) | **100%** (3422/3422) | 73.79% (2525/3422) |
| Residual leak | **0** | **897 identifiers** |
| Precision (benign untouched) | **100%** (704/704) | 85.8% (604/704; 100 over-redacted) |

Presidio's 897 leaks cluster exactly on the structured/India categories (family_id, accession,
device, PAN, GSTIN, DL, voter ID, ABHA/UHID) — the rare-ID failure mode the transformer row predicts.

---

## 4. Where RePORTal is *not* the right tool (honest limits)

- **Free-text narrative de-identification** — if the deliverable is de-identified discharge *prose*,
  a probabilistic NLP/transformer model (Philter, Azure DeID) is the correct instrument; RePORTal's
  free-text columns are *suppressed*, not surgically redacted. RePORTal protects free text by removal,
  which is safe but lossy for narrative-analysis use cases.
- **Unknown identifier shapes** — determinism means RePORTal removes what its rules + value gate are
  configured to recognise. Coverage is bounded by rule accuracy; the fail-closed gate is the
  backstop (it holds rather than leaks), but a genuinely novel identifier *format* in a benign-looking
  column is the residual risk, mitigated by the OR-combined value scan, not eliminated.
- **k-anonymity / statistical disclosure on quasi-identifiers** — RePORTal applies Safe-Harbor
  transforms (date jitter, generalisation, small-cell suppression); for formal k-anonymity tuning on
  quasi-identifier *combinations*, a dedicated SDC tool (ARX) is complementary, not replaced.

---

## 5. Bottom line for the PI

RePORTal is not "a better Presidio" — it solves a **different and largely unserved problem**:
fail-closed, deterministic, audit-complete de-identification of **structured** clinical study data
under a named HIPAA + DPDPA posture, with **no row value ever reaching an LLM or cloud service**. The
incumbents are overwhelmingly probabilistic free-text tools whose published accuracy (i) is measured
on a different problem and (ii) degrades sharply on exactly the rare structured IDs a CRF is full of.
On the single fair head-to-head — a value scanner on the same tabular corpus — RePORTal removes 100%
of identifiers with zero over-redaction where Presidio leaks 26%.
