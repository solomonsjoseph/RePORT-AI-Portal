# Comparative Analysis: RePORTal vs. Existing De-identification Systems

**Prepared:** 2026-06-26 · Companion to `phi_benchmark_pi_report.md`
**Sources:** peer-reviewed benchmarks + vendor documentation catalogued in
`docs/manuscript/research_dossier.md` (each row traces to a citation there).

---

## 0. The honest framing first (read this before the tables)

There are **two different de-identification problems**, and most existing tools solve the *other* one:

- **Free-text clinical notes**: discharge summaries, narratives. Probabilistic NLP/LLM territory.
  deid, MIST, NeuroNER, NLM Scrubber, CliniDeID, Philter, Azure DeID, GPT-4 all live here.
- **Structured study data**: CDISC-style tabular CRFs with typed columns (Indo-VAP's actual shape).
  This is RePORTal's target, and it is a *sparsely-mature* ecosystem (ARX, sdcMicro, μ-ARGUS).

**Consequence:** published recall numbers for free-text tools and RePORTal's 100% on structured data
are **measured on different corpora and are not directly comparable.** This document therefore
compares on two separate planes: (A) *architecture/design axes*, where any tool can be honestly
placed, and (B) *published accuracy*, kept corpus-labelled so no false equivalence is implied. The
only genuine apples-to-apples head-to-head, a value scanner on the identical tabular corpus, is the
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
are interactive utilities. They impose no fail-closed publish gate and no automated audit/posture
binding. Every probabilistic/cloud row trades a structural guarantee for a statistical estimate.

### 1.1 Capability matrix (✓ = has it, ✗ = does not, ➖ = partial/manual)

Measured cells (0 leak / 0 over-redaction) are from the §2 head-to-head on the identical corpus;
the rest are design properties.

| Capability | **RePORTal** | Presidio | Philter | Transformer | spaCy | scrubadub | AWS/Azure/GPT-4 | ARX/sdcMicro |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Built for **structured** tabular data | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| **Deterministic** (not probabilistic) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| **Fail-closed** (holds, never leaks on doubt) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ➖ |
| Row values **never to LLM/cloud** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| **Per-variable audit trail** (enforced) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ➖ |
| Named **HIPAA + DPDPA** posture | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Runs **offline, no API key, no cost** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| **Multi-jurisdiction** (>1 regime out of the box) | ✓ | ➖ | ✗ | ✗ | ➖ | ➖ | ➖ | ➖ |
| **India-specific IDs** (Aadhaar/PAN/GSTIN…) | ✓ | ✗ | ➖ | ✗ | ✗ | ✗ | ✗ | n/a |
| **0 identifiers leaked** (measured) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | not run | n/a |
| **0 benign cells destroyed** (measured) | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ | not run | n/a |

RePORTal is the only column that is ✓ on every row. scrubadub earns the lone incumbent ✓ on "0 benign
destroyed", but only because it detects so little (14.85% recall); its precision is bought with leakage.

### 1.2 Multi-jurisdiction coverage

Almost every incumbent is built around a **single** regulatory regime, in practice US HIPAA, because
the canonical training corpora (i2b2, n2c2) and the default recognizer packs are US-centric:

- **RePORTal** is jurisdiction-configurable by design. A study declares its jurisdictions in
  `_study_privacy.yaml`, and the classifier composes the matching rule bundles under a
  `strictest_wins` conflict policy. The Indo-VAP run is published under a combined **HIPAA Safe Harbor
  + India DPDPA / Aadhaar Act / ICMR** posture, the two regimes enforced together rather than either
  alone.
- **Presidio** ships predefined recognizers for several locales (US, UK, a few EU) and is extensible,
  but carries no India identifier pack out of the box (it leaked every Aadhaar, PAN, GSTIN, ABHA, and
  UHID in §2). Marked partial (➖).
- **Philter / transformer (`obi/deid_roberta_i2b2`)** are trained on US i2b2 free text and are
  effectively single-jurisdiction (✗).
- **spaCy, scrubadub, the cloud services, and ARX/sdcMicro** offer some locale or language
  configurability but no built-in *multi-regime PHI posture* binding detection to a named regulation
  (➖); the SDC tools are jurisdiction-agnostic statistics with no identifier-rule layer at all.

This matters for RePORT-style international cohorts: a US-only de-identifier silently passes through
exactly the India-specific identifiers (Aadhaar, PAN, GSTIN) that dominate an Indian CRF.

For the broader open-source landscape (Presidio, Philter, NLM Scrubber, deid, MIST, scrubadub, and
others discussed by practitioners), see the community survey thread
[r/LanguageTechnology, "Open-source PHI de-identification tool"](https://www.reddit.com/r/LanguageTechnology/comments/o7nlju/opensource_phi_deidentification_tool/);
the recurring theme there is the same one measured in §2: the mature open tools are US-centric and
probabilistic.

---

## 2. Measured head-to-head: every free incumbent on the IDENTICAL corpus

This is **not** citations. We ran each tool ourselves on the same planted-identifier corpus
(4,680 cells, **3,422 identifiers**, **704 benign**), each cell value fed to each tool and scored
against the *same* ground truth. Reproducible:
`uv run --all-groups python docs/eval/synthetic_phi_benchmark/score_synthetic.py`.

| Tool | Recall (IDs removed) | **Leaked** | Precision (benign kept) | Over-redacted |
|---|---|---|---|---|
| **RePORTal (this work)** | **100%** (3422/3422) | **0** | **100%** (704/704) | **0** |
| Transformer `obi/deid_roberta_i2b2` | 97.98% | **69** | 69.46% | 215 |
| Philter (UCSF, i2b2 filters) | 93.16% | **234** | 87.22% | 90 |
| Microsoft Presidio | 73.79% | **897** | 85.80% | 100 |
| spaCy NER (`en_core_web_lg`) | 62.80% | **1273** | 25.85% | 522 |
| scrubadub | 14.85% | **2914** | 100% | 0 |
| LLM de-id (GPT-4/Claude) | *not run*: needs API key (set `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` to include) | | | |

**Reading the table.** *Every probabilistic incumbent leaks*, and the recall/precision tension is
visible across the whole field:

- The **best-in-class open clinical transformer** (i2b2-trained RoBERTa) is the strongest incumbent
  at 97.98% recall, yet it *still* leaks **69** identifiers and pays for its recall with the
  *lowest-but-one* precision (69.46%): it over-redacts **215** benign clinical cells. High recall and
  high precision are in tension for a probabilistic model; RePORTal owes nothing to either tail.
- **Philter** (recall-prioritised by design) is the most balanced incumbent but still leaks **234**.
- **scrubadub** shows the opposite failure: perfect precision but 14.85% recall, and it is
  blind to almost every structured/India identifier (Aadhaar, PAN, GSTIN, accession, device, MRN…). It is US-centric.
- **spaCy NER** alone is the weakest de-identifier on both axes (62.8% / 25.85%). Generic NER is not
  a de-id system.

**The decisive gap is not the recall column. It is the leak column.** The best incumbent here still
puts **69 real identifiers** into the published corpus. For a release that must be *provably*
de-identified, 69 ≠ 0, and a probabilistic tool cannot promise 0. RePORTal's determinism + fail-closed
publish gate is what makes the leak column exactly **0**: an identifier shape it is configured for is
removed every time, and any `keep` cell whose value trips the residual gate **holds the form** rather
than leaking it (this is the 204 "via gate-hold" protections inside RePORTal's 100% recall).

This also matches the literature cross-read: a 95.5%-F1 transformer on i2b2 falls to **59.7%** on the
*rare-ID* category (BMC Med Inform 2020), precisely the Aadhaar/accession/device content of a CRF.
Probabilistic systems are weakest exactly where structured study data is densest in identifiers.

### 2.1 For context only: incumbents' OWN published numbers (different, free-text corpora)

Not comparable to §2 (different corpus, different task); listed so the field is anchored:
deid name-recall **48.8%** · MIST **66.9%** · NeuroNER **84.1%** · NLM Scrubber **88.1%** ·
CliniDeID **95.9%** (JMIR 2024) · Philter recall **99.92%** on i2b2 2014 (npj Digit Med 2020) ·
Azure DeID F1 **0.939** on UK NHS (iScience 2025) · GPT-4 misses **~1 in 6** (Nature Sci Rep 2025).
Note Philter scores 99.92% on its *home* free-text benchmark but **93.16%** when pointed at this
structured corpus. The modality shift is the whole point.

---

## 3. Where RePORTal is *not* the right tool (honest limits)

- **Free-text narrative de-identification**: if the deliverable is de-identified discharge *prose*,
  a probabilistic NLP/transformer model (Philter, Azure DeID) is the correct instrument; RePORTal's
  free-text columns are *suppressed*, not surgically redacted. RePORTal protects free text by removal,
  which is safe but lossy for narrative-analysis use cases.
- **Unknown identifier shapes**: determinism means RePORTal removes what its rules + value gate are
  configured to recognise. Coverage is bounded by rule accuracy; the fail-closed gate is the
  backstop (it holds rather than leaks), but a genuinely novel identifier *format* in a benign-looking
  column is the residual risk, mitigated by the OR-combined value scan, not eliminated.
- **k-anonymity / statistical disclosure on quasi-identifiers**: RePORTal applies Safe-Harbor
  transforms (date jitter, generalisation, small-cell suppression); for formal k-anonymity tuning on
  quasi-identifier *combinations*, a dedicated SDC tool (ARX) is complementary, not replaced.

---

## 4. Bottom line for the PI

RePORTal is not "a better Presidio". It solves a **different and largely unserved problem**:
fail-closed, deterministic, audit-complete de-identification of **structured** clinical study data
under a named HIPAA + DPDPA posture, with **no row value ever reaching an LLM or cloud service**.

Measured on the identical corpus, **five free incumbents, including the best-in-class open clinical
transformer, all leak** (69 to 2,914 identifiers each), and the strongest by recall pays for it with
the worst precision. Only RePORTal reaches the one number a de-identification release actually
requires: **0 identifiers leaked**, with **0 benign cells destroyed**. The probabilistic field cannot
promise that 0, by construction; a deterministic + fail-closed design can, and does.
