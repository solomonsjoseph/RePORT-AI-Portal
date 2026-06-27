"""Score the planted-identifier synthetic benchmark: RePORTal vs stock Presidio.

TRUSTED PRE-SCRUB CODE — reads SYNTHETIC cell values internally (fake data, no real
PHI) and emits ONLY value-free counts. Same sanctioned pattern as phi_scrub.py /
run_headtohead.py.

Method (faithful to production, deterministic, offline)
------------------------------------------------------
RePORTal arm — the two production PHI engines:
  1. `phi_review.classify_headers` (the deterministic decision engine) assigns an
     action per column from the header NAME + jurisdiction rule bundle (offline,
     pinned). This is exactly the classification the pipeline runs.
  2. The per-cell de-identification OUTCOME follows from the action:
       drop/suppress        -> column removed              (identifier gone)
       pseudonymize         -> RID_<...> replacement       (identifier gone)
       jitter_date          -> interval-preserving shift   (identifier gone)
       cap                  -> age>89 -> 90+               (identifier gone)
       keep                 -> RAW value survives -> the production residual publish
                               gate (`phi_patterns` BLOCKING + SUBJECT_ID, the SAME
                               ruler the real gate enforces) decides:
                                 matches a pattern -> form HELD (caught, not published)
                                 matches nothing   -> LEAKED into the LLM zone
  Only `keep` columns can leak; drop/pseudo/jitter/cap provably remove the original.

Presidio arm — stock `AnalyzerEngine` (default recognizers + `en_core_web_lg`) +
`AnonymizerEngine`, one raw cell value per call (its strongest off-the-shelf field
scan), exactly as in the Indo-VAP head-to-head.

Both arms are joined to `ground_truth.jsonl` per cell, so recall (leakage) and
precision (over-redaction) are computed against KNOWN truth, per category and per
placement (named / mislabeled / freetext).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl

import scripts.security.phi_review as phir
from scripts.security.phi_patterns import BLOCKING_PATTERNS, SUBJECT_ID_PATTERNS

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# Shared ruler = the production publish gate's detector, full enumeration.
RULER: list[tuple[str, Any]] = list(BLOCKING_PATTERNS) + [
    (f"SUBJECT_ID[{i}]", p) for i, p in enumerate(SUBJECT_ID_PATTERNS)
]
# Date matches are exempt for RePORTal (jittered, not leaks); no KEEP date columns
# exist in the benchmark, but exclude DATE_ISO from the gate-catch test to be safe.
_GATE_NAMES = {n for n, _ in RULER if n != "DATE_ISO"}

ARMS = {"Synth-India": ("INDIA", "USA"), "Synth-US": ("USA",)}

PROTECT = {"drop", "suppress", "pseudonymize", "jitter_date", "cap", "generalize", "band"}
REMOVE = {"drop", "suppress", "pseudonymize"}  # actions that blank/overwrite a benign cell


def gate_hits(value: str) -> bool:
    """True iff the production residual gate would flag *value* (would hold the form)."""
    for name, pat in RULER:
        if name == "DATE_ISO":
            continue
        if pat.search(value):
            return True
    return False


def load_ground_truth() -> dict[tuple[str, str, int, str], dict[str, Any]]:
    gt: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for line in (HERE / "ground_truth.jsonl").open():
        c = json.loads(line)
        gt[(c["study"], c["form"], c["row"], c["column"])] = c
    return gt


def cell_class(cell: dict[str, Any]) -> str:
    """identifier (recall axis) | benign (precision axis) | edge (excluded).

    'benign' = genuinely benign clinical data a correct tool must leave alone:
    the non_phi bait columns + age cells <90. An invalid/placeholder/null value
    sitting inside a legitimately-PHI column is 'edge' — neither a real identifier
    nor benign clinical data — so dropping it is neither a leak nor over-redaction.
    """
    if cell["is_identifier"]:
        return "identifier"
    if cell["category"] in ("non_phi", "age"):
        return "benign"
    return "edge"


def reportal_outcome(action: str, value: str, is_id: bool, gate_caught: bool) -> str:
    v = value.strip()
    if action in PROTECT:
        if is_id:
            return "protected_classification"
        # benign cell under a protecting action -> over-redaction iff it actually
        # blanks/overwrites (cap on age<=threshold and jitter on non-dates do not).
        if action in REMOVE:
            return "over_redacted"
        return "ok_untouched"  # cap/jitter no-op on a benign value
    # action == keep
    if not is_id:
        return "ok_untouched"
    if not v:
        return "ok_untouched"
    # Production publish gate is OR-combined: phi_patterns OR Presidio. A KEEP cell
    # whose value trips EITHER detector holds the form (caught, not published).
    return "protected_gate_hold" if gate_caught else "LEAKED"


def _cached(fn):
    """Wrap a per-value detector in a value cache (the corpus repeats values)."""
    cache: dict[str, bool] = {}

    def modified(value: str) -> bool:
        v = value.strip()
        if not v:
            return False
        if v in cache:
            return cache[v]
        cache[v] = bool(fn(v))
        return cache[v]

    return modified


def build_presidio():
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

    analyzer, anonymizer = AnalyzerEngine(), AnonymizerEngine()

    def fn(v: str) -> bool:
        res = analyzer.analyze(text=v, language="en")
        return bool(res) and anonymizer.anonymize(text=v, analyzer_results=res).text != v

    return _cached(fn)


def build_scrubadub():
    import scrubadub

    s = scrubadub.Scrubber()
    return _cached(lambda v: s.clean(v) != v)


def build_philter():
    import os

    import philter_lite
    from philter_lite import detect_phi, load_filters, transform_text_asterisk

    base = os.path.dirname(philter_lite.__file__)
    filters = load_filters(os.path.join(base, "configs", "philter_delta.toml"))

    def fn(v: str) -> bool:
        inc, _exc, _dt = detect_phi(v, filters)
        return transform_text_asterisk(v, inc) != v

    return _cached(fn)


def build_spacy():
    import spacy

    nlp = spacy.load("en_core_web_lg")
    # NER labels that correspond to identifying spans (a stock-NER de-id baseline).
    pii = {"PERSON", "ORG", "GPE", "LOC", "DATE", "TIME", "CARDINAL", "FAC", "NORP"}
    return _cached(lambda v: any(e.label_ in pii for e in nlp(v).ents))


def build_transformer():
    # Best-in-class open clinical de-id model (i2b2-trained RoBERTa).
    from transformers import pipeline

    ner = pipeline("ner", model="obi/deid_roberta_i2b2", aggregation_strategy="simple")
    return _cached(lambda v: len(ner(v)) > 0)


def build_llm():
    """LLM de-id (GPT-4 / Claude). Optional: needs an API key. Synthetic data only."""
    import os

    prompt = (
        "You are a PHI de-identification system. Output ONLY the input text with every "
        "identifier (names, IDs, dates, contact, locations, account/device numbers) "
        "replaced by [REDACTED]. If nothing is an identifier, return the text unchanged.\n\nTEXT: "
    )
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI

        client = OpenAI()
        model = os.environ.get("LLM_DEID_MODEL", "gpt-4o")

        def fn(v: str) -> bool:
            r = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "user", "content": prompt + v}])
            return r.choices[0].message.content.strip() != v

        return _cached(fn)
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()
        model = os.environ.get("LLM_DEID_MODEL", "claude-opus-4-8")

        def fn(v: str) -> bool:
            r = client.messages.create(
                model=model, max_tokens=512, temperature=0,
                messages=[{"role": "user", "content": prompt + v}])
            return r.content[0].text.strip() != v

        return _cached(fn)
    return None  # no key -> tool skipped, reported as not-run


# Registry of value-scanner incumbents. RePORTal is scored separately (it is
# classification-based, not a per-value scanner). Order = display order.
VALUE_SCANNER_BUILDERS = {
    "presidio": build_presidio,
    "scrubadub": build_scrubadub,
    "philter": build_philter,
    "spacy_ner": build_spacy,
    "transformer": build_transformer,
    "llm": build_llm,
}


def value_scanner_outcome(modified_fn, value: str, is_id: bool) -> str:
    v = value.strip()
    if not v:
        return "ok_untouched"
    mod = modified_fn(v)
    if is_id:
        return "protected_classification" if mod else "LEAKED"
    return "over_redacted" if mod else "ok_untouched"


def score() -> dict[str, Any]:
    gt = load_ground_truth()

    # Build every available value-scanner incumbent (skip any that need a missing key).
    scanners: dict[str, Any] = {}
    skipped: list[str] = []
    for name, builder in VALUE_SCANNER_BUILDERS.items():
        try:
            fn = builder()
        except Exception as exc:  # a tool that won't import is reported, not fatal
            skipped.append(f"{name} ({type(exc).__name__})")
            continue
        if fn is None:
            skipped.append(f"{name} (no API key)")
            continue
        scanners[name] = fn
    if skipped:
        print("skipped value scanners:", ", ".join(skipped), file=sys.stderr)
    # The production OR-combined gate's second detector is specifically Presidio.
    presidio_modified = scanners.get("presidio") or build_presidio()

    # results[tool][cell_class][category][placement][outcome] = count
    def nested() -> Any:
        return defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))

    results: dict[str, Any] = {t: nested() for t in ["reportal", *scanners]}
    form_status: dict[str, dict[str, Any]] = {}

    for arm, jurs in ARMS.items():
        priv = phir.StudyPrivacyConfig(
            study_dir=DATA / arm, jurisdictions=tuple(jurs), rule_refresh="offline",
            conflict_policy="strictest_wins", max_synthetic_attempts=5,
            approval_mode="auto", parallelism_mode="auto", data_as_of="2026-06-26")
        bundle = phir.refresh_jurisdiction_rules(priv, allow_network=False)

        for xlsx in sorted((DATA / arm / "datasets").glob("*.xlsx")):
            form = xlsx.stem
            wb = openpyxl.load_workbook(xlsx, read_only=True)
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h) for h in next(rows_iter)]
            cls = phir.classify_headers(tuple(headers), priv, bundle)
            actions = {h: str(cls[h].action) for h in headers}

            form_leaks = 0
            form_holds = 0
            for r, raw in enumerate(rows_iter):
                for col, val in zip(headers, raw):
                    cell = gt.get((arm, form, r, col))
                    if cell is None:
                        continue
                    value = "" if val is None else str(val)
                    cat, plc, is_id = cell["category"], cell["placement"], cell["is_identifier"]
                    cls = cell_class(cell)

                    # OR-combined production gate: phi_patterns OR stock Presidio.
                    gate_caught = bool(value.strip()) and (
                        gate_hits(value.strip()) or presidio_modified(value.strip()))
                    ro = reportal_outcome(actions[col], value, is_id, gate_caught)
                    results["reportal"][cls][cat][plc][ro] += 1
                    if ro == "LEAKED":
                        form_leaks += 1
                    elif ro == "protected_gate_hold":
                        form_holds += 1

                    for tname, tfn in scanners.items():
                        to = value_scanner_outcome(tfn, value, is_id)
                        results[tname][cls][cat][plc][to] += 1
            wb.close()
            form_status[f"{arm}/{form}"] = {
                "actions": actions, "reportal_leak_cells": form_leaks,
                "reportal_gate_hold_cells": form_holds,
                "reportal_publishes": form_leaks == 0 and form_holds == 0,
            }

    # Collapse defaultdicts to plain dicts.
    def plain(d: Any) -> Any:
        if isinstance(d, defaultdict):
            return {k: plain(v) for k, v in d.items()}
        return d

    return {"results": plain(results), "form_status": form_status, "skipped_tools": skipped}


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Headline recall/precision/leak per tool, plus leak detail by category+placement."""
    out: dict[str, Any] = {}
    for tool in data["results"]:
        res = data["results"][tool]
        id_total = id_protected = leaked = gate_hold = 0
        benign_total = benign_ok = over = 0
        leak_detail: dict[str, int] = defaultdict(int)
        for cls, cats in res.items():
            for cat, plcs in cats.items():
                for plc, outs in plcs.items():
                    for outcome, n in outs.items():
                        if cls == "identifier":
                            id_total += n
                            if outcome == "LEAKED":
                                leaked += n
                                leak_detail[f"{cat}/{plc}"] += n
                            else:
                                id_protected += n
                                if outcome == "protected_gate_hold":
                                    gate_hold += n
                        elif cls == "benign":
                            benign_total += n
                            if outcome == "over_redacted":
                                over += n
                            else:
                                benign_ok += n
                        # cls == "edge" -> excluded from both axes
        out[tool] = {
            "identifier_cells": id_total,
            "protected": id_protected,
            "protected_by_classification": id_protected - gate_hold,
            "protected_by_gate_hold": gate_hold,
            "leaked": leaked,
            "recall_pct": round(100.0 * id_protected / id_total, 2) if id_total else None,
            "benign_cells": benign_total,
            "benign_untouched": benign_ok,
            "over_redacted": over,
            "precision_pct": round(100.0 * benign_ok / benign_total, 2) if benign_total else None,
            "leak_by_category_placement": dict(sorted(leak_detail.items())),
        }
    return out


def main() -> None:
    print("Classifying + scoring both arms (RePORTal) and running Presidio ...", file=sys.stderr)
    data = score()
    summary = summarize(data)
    report = {
        "benchmark": "planted-identifier synthetic (Synth-US + Synth-India)",
        "method": "RePORTal = production classify_headers + residual publish gate; "
                  "Presidio = stock AnalyzerEngine + en_core_web_lg + AnonymizerEngine; "
                  "joined per-cell to ground_truth.jsonl. Counts only, no value emitted.",
        "summary": summary,
        "by_category_placement": data["results"],
        "form_status": data["form_status"],
    }
    (HERE / "score_results.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    print("\n================ HEADLINE ================")
    if data.get("skipped_tools"):
        print("(skipped:", ", ".join(data["skipped_tools"]), ")")
    for tool in summary:
        s = summary[tool]
        print(f"\n{tool.upper()}")
        print(f"  recall (identifiers protected) : {s['protected']}/{s['identifier_cells']} = {s['recall_pct']}%  | LEAKED={s['leaked']}")
        print(f"     via classification={s['protected_by_classification']}  via gate-hold={s['protected_by_gate_hold']}")
        print(f"  precision (benign untouched)   : {s['benign_untouched']}/{s['benign_cells']} = {s['precision_pct']}%  | over-redacted={s['over_redacted']}")
        if s["leaked"]:
            print(f"  leak by category/placement: {s['leak_by_category_placement']}")
    held = [k for k, v in data["form_status"].items() if not v["reportal_publishes"]]
    print(f"\nRePORTal forms that would NOT publish clean (held/leak): {len(held)}/{len(data['form_status'])}")
    print(f"wrote {HERE / 'score_results.json'}")


if __name__ == "__main__":
    main()
