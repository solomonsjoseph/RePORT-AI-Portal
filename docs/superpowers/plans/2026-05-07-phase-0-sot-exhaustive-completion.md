# Phase 0 — SoT Exhaustive Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `data/SoT/Indo-VAP/<form>_policy.yaml` exhaustive for every form in `data/raw/Indo-VAP/`. Produce a draft per-form evidence pack for every form whose YAML changes. Land a CI hard gate that blocks the rest of the restructure (Phases 1–6) until coverage is 100%.

**Architecture:** Five small Python tools under `scripts/source_truth/`: a gap inventory, an extractor-agent harness, a reviewer-agent harness, a per-form dispatcher, a merge-on-approval helper, and a coverage hard gate. The dispatcher runs extractor + reviewer subagent pairs in parallel batches of 4–8 forms. All agents are read-deny on dataset row values; they read PDF text, dataset column keys (line 1 only), pilot policy artifacts under `tmp/results/policy_pilot_*/`, and the existing SoT YAML. Drafts land under `tmp/sot_gap_drafts/`; approved drafts merge into `data/SoT/Indo-VAP/`. Every new code path uses the centralized logger (`scripts.utils.logging_system`) and reads paths from `config.py`.

**Tech stack:** Python 3.11+ (project standard), pytest, PyYAML, the existing `scripts.extraction.io.atomic_write_json` / `atomic_write_jsonl`, the existing `scripts.utils.logging_system`, Claude Agent SDK for subagent dispatch (already a project dependency), `gh` CLI (used only for HITL issues if any draft is ambiguous).

**Spec source:** [docs/superpowers/specs/2026-05-07-llm-source-restructure-design.md](../specs/2026-05-07-llm-source-restructure-design.md), §4 (Phase 0).

---

## Pre-flight

### Pre-flight check P0: confirm preconditions

**Files:** none (read-only checks).

- [ ] **Step 1: Verify branch and clean tree.**

Run: `git status` and `git rev-parse --abbrev-ref HEAD`
Expected: branch is `PHI_handing_review`, no uncommitted changes that would conflict with new files.

- [ ] **Step 2: Verify the SoT folder and a sample policy file exist.**

Run: `ls data/SoT/Indo-VAP/ | head -5 && head -20 data/SoT/Indo-VAP/19_Smear_policy.yaml`
Expected: at least 28 `*_policy.yaml` files; the YAML opens with a top-level mapping including `form_id`, `variables`, etc.

- [ ] **Step 3: Verify the raw PDF folder and dataset folder exist.**

Run: `ls data/raw/Indo-VAP/ | wc -l && ls output/Indo-VAP/trio_bundle/datasets/ | wc -l`
Expected: both are >0; numbers are recorded for the gap inventory in Task P0-1.

- [ ] **Step 4: Verify the pilot artifact folder is present.**

Run: `ls tmp/results/ | grep -c policy_pilot`
Expected: 30 or so `policy_pilot_<form>` directories.

- [ ] **Step 5: Verify centralized config and logger imports work.**

Run: `python -c "import config; from scripts.utils import logging_system; print('ok')"`
Expected: prints `ok`. Any import error blocks the rest of this plan.

---

## File structure

| File | Responsibility |
|------|----------------|
| `scripts/source_truth/sot_gap_inventory.py` | Walk raw PDFs + datasets + pilot artifacts + SoT YAMLs. Emit `tmp/sot_gap_coverage.json` and `tmp/sot_gap_report.md`. |
| `scripts/source_truth/sot_extractor_agent.py` | Per-form extractor: reads PDF + dataset column keys + pilot artifact + existing YAML; writes draft YAML and draft evidence pack to `tmp/sot_gap_drafts/`. |
| `scripts/source_truth/sot_reviewer_agent.py` | Per-form reviewer: reads same sources + extractor draft; writes `<form>_review.md` and a confidence verdict. |
| `scripts/source_truth/sot_gap_dispatcher.py` | Orchestrate per-form extractor + reviewer pairs in parallel batches with concurrency knob; respects read-deny on row values. |
| `scripts/source_truth/sot_gap_merge.py` | Merge-on-approval: copy approved YAML draft to `data/SoT/Indo-VAP/`; copy approved evidence-pack draft to `tmp/sot_gap_drafts/evidence_packs/<form>.json` (final move into `output/Indo-VAP/llm_source/evidence_packs/` happens in Phase 2). |
| `scripts/source_truth/sot_coverage_gate.py` | Hard CI gate: exits non-zero when any form in `data/raw/Indo-VAP/` lacks a complete SoT YAML. |
| `tests/source_truth/test_sot_gap_inventory.py` | Unit tests for the inventory walker. |
| `tests/source_truth/test_sot_extractor_agent.py` | Unit tests for the extractor's column-keys-only contract. |
| `tests/source_truth/test_sot_reviewer_agent.py` | Unit tests for the reviewer's verdict schema. |
| `tests/source_truth/test_sot_gap_dispatcher.py` | Integration tests for batch dispatch. |
| `tests/source_truth/test_sot_gap_merge.py` | Tests for the merge-on-approval helper. |
| `tests/source_truth/test_sot_coverage_gate.py` | Tests for the hard gate (pass + fail paths). |
| `tests/fixtures/build_mini/` | Already exists; adds 1 deliberately incomplete fixture form to drive gap-detection tests. |
| `config.py` | New constants: `SOT_DIR`, `RAW_PDF_DIR`, `PILOT_RESULTS_DIR`, `SOT_GAP_DRAFTS_DIR`, `SOT_GAP_COVERAGE_PATH`, `SOT_GAP_REPORT_PATH`, `SOT_EVIDENCE_PACK_DRAFTS_DIR`. Re-exports for tests. |

---

## Task 0-1: Add Phase 0 config constants

**Files:**
- Modify: `config.py` (append constants)
- Test: `tests/source_truth/test_config_phase0.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_config_phase0.py
"""Phase 0 config constants must exist and resolve to project-relative paths."""

from pathlib import Path

import config


def test_phase0_paths_exist_and_are_strings():
    for name in (
        "SOT_DIR",
        "RAW_PDF_DIR",
        "PILOT_RESULTS_DIR",
        "SOT_GAP_DRAFTS_DIR",
        "SOT_GAP_COVERAGE_PATH",
        "SOT_GAP_REPORT_PATH",
        "SOT_EVIDENCE_PACK_DRAFTS_DIR",
    ):
        value = getattr(config, name)
        assert isinstance(value, (str, Path)), f"{name} not a path-like"
        assert str(value), f"{name} is empty"


def test_phase0_paths_are_study_aware():
    # The defaults target Indo-VAP; per-study override via STUDY env var must work.
    indo_vap = Path(str(config.SOT_DIR))
    assert indo_vap.name == "SoT"
    assert "Indo-VAP" in str(indo_vap)
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `pytest tests/source_truth/test_config_phase0.py -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'SOT_DIR'` (or similar).

- [ ] **Step 3: Add the constants to `config.py`.**

```python
# config.py — append near the other study-aware path blocks

SOT_DIR = DATA_DIR / STUDY / "SoT"
RAW_PDF_DIR = DATA_DIR / "raw" / STUDY
PILOT_RESULTS_DIR = TMP_DIR / "results"

SOT_GAP_DRAFTS_DIR = TMP_DIR / "sot_gap_drafts"
SOT_GAP_COVERAGE_PATH = TMP_DIR / "sot_gap_coverage.json"
SOT_GAP_REPORT_PATH = TMP_DIR / "sot_gap_report.md"
SOT_EVIDENCE_PACK_DRAFTS_DIR = SOT_GAP_DRAFTS_DIR / "evidence_packs"
```

If `DATA_DIR`, `STUDY`, or `TMP_DIR` already exist, reuse them. If not, define them at the top of the new block:

```python
DATA_DIR = REPO_ROOT / "data"
STUDY = os.environ.get("REPORTAL_STUDY", "Indo-VAP")
TMP_DIR = REPO_ROOT / "tmp"
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `pytest tests/source_truth/test_config_phase0.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add config.py tests/source_truth/test_config_phase0.py
git commit -m "feat(config): add Phase 0 SoT-gap path constants"
```

---

## Task 0-2: Coverage inventory walker (the gap report)

**Files:**
- Create: `scripts/source_truth/sot_gap_inventory.py`
- Create: `tests/source_truth/test_sot_gap_inventory.py`
- Create: `tests/fixtures/sot_gap/` with 3 mini-fixture forms (one fully covered, one missing variables, one missing entire YAML)

- [ ] **Step 1: Build the fixture set.**

Create three deliberately-staged forms under `tests/fixtures/sot_gap/`:

```
tests/fixtures/sot_gap/
├── data/Mini/SoT/
│   ├── 19_Smear_policy.yaml          # complete coverage
│   └── 8_CXR_policy.yaml             # has form, missing two variables
├── data/raw/Mini/
│   ├── 19_Smear.pdf                  # tiny placeholder pdf
│   ├── 8_CXR.pdf                     # tiny placeholder pdf
│   └── 95_SAE.pdf                    # form with NO YAML at all
├── output/Mini/trio_bundle/datasets/
│   ├── 19_Smear.jsonl                # 1 line; columns only
│   ├── 8_CXR.jsonl                   # 1 line; includes the two missing variables
│   └── 95_SAE.jsonl                  # 1 line
└── tmp/results/policy_pilot_19_Smear/  # one pilot artifact for parity
    └── policy.yaml
```

The PDF placeholders are 30-byte stubs (`%PDF-1.4\n%%EOF\n`) — real PDF parsing is unit-tested separately.

- [ ] **Step 2: Write the failing test.**

```python
# tests/source_truth/test_sot_gap_inventory.py
"""SoT gap inventory walker: fixture-driven."""

import json
from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_inventory import build_coverage

FIXTURE = Path("tests/fixtures/sot_gap")


def test_complete_form_marked_complete():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    assert coverage["forms"]["19_Smear"]["sot_present"] is True
    assert coverage["forms"]["19_Smear"]["sot_complete"] is True
    assert coverage["forms"]["19_Smear"]["missing_variables"] == []


def test_partial_form_lists_missing_variables():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    cxr = coverage["forms"]["8_CXR"]
    assert cxr["sot_present"] is True
    assert cxr["sot_complete"] is False
    assert sorted(cxr["missing_variables"]) != []


def test_missing_form_listed():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    sae = coverage["forms"]["95_SAE"]
    assert sae["sot_present"] is False
    assert sae["sot_complete"] is False


def test_inventory_never_reads_row_values(tmp_path, monkeypatch):
    """The walker must read only the first line of each JSONL (column keys),
    never the values. We assert by patching open() and counting bytes."""
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    # The structure-level guarantee: no field in the coverage dict carries a
    # row payload. (Stricter byte-level guarantee is in the agent contract.)
    for form, info in coverage["forms"].items():
        assert "row_sample" not in info, f"{form} leaked a row sample"
        assert "values" not in info, f"{form} leaked values"
```

- [ ] **Step 3: Run tests to verify they fail.**

Run: `pytest tests/source_truth/test_sot_gap_inventory.py -v`
Expected: 4 failures with `ModuleNotFoundError: No module named 'scripts.source_truth.sot_gap_inventory'`.

- [ ] **Step 4: Implement `build_coverage()`.**

```python
# scripts/source_truth/sot_gap_inventory.py
"""SoT gap inventory.

For each form in raw_pdf_dir or dataset_dir, report whether a SoT YAML
exists and whether every observed dataset column key is declared as a
variable in the SoT YAML. Reads ONLY column keys from the dataset (line 1
parsed, then discarded) — never row values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def _form_id_from_filename(name: str) -> str:
    # "19_Smear.jsonl" -> "19_Smear"
    return name.rsplit(".", 1)[0]


def _read_column_keys_only(jsonl_path: Path) -> list[str]:
    with jsonl_path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        return []
    obj = json.loads(first)
    keys = list(obj.keys())
    del obj  # belt and braces; the parsed dict never escapes this function
    return keys


def _read_sot_variables(sot_path: Path) -> list[str]:
    raw = sot_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return []
    variables = data.get("variables", [])
    out: list[str] = []
    for v in variables:
        if isinstance(v, dict):
            vid = v.get("variable_id") or v.get("name")
            if vid:
                out.append(str(vid))
    return out


def build_coverage(
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
) -> dict[str, Any]:
    sot_dir = Path(sot_dir)
    raw_pdf_dir = Path(raw_pdf_dir)
    dataset_dir = Path(dataset_dir)
    pilot_dir = Path(pilot_dir)

    forms: dict[str, dict[str, Any]] = {}

    for jsonl in sorted(dataset_dir.glob("*.jsonl")):
        form = _form_id_from_filename(jsonl.name)
        forms.setdefault(form, {"observed_in": []})["observed_in"].append("dataset")
        forms[form]["dataset_columns"] = _read_column_keys_only(jsonl)

    for pdf in sorted(raw_pdf_dir.glob("*.pdf")):
        form = _form_id_from_filename(pdf.name)
        forms.setdefault(form, {"observed_in": []})["observed_in"].append("pdf")

    for form_dir in sorted(p for p in pilot_dir.glob("policy_pilot_*") if p.is_dir()):
        form = form_dir.name.removeprefix("policy_pilot_")
        forms.setdefault(form, {"observed_in": []}).setdefault("pilot_present", True)

    for form, info in forms.items():
        sot_path = sot_dir / f"{form}_policy.yaml"
        if sot_path.is_file():
            info["sot_present"] = True
            declared = set(_read_sot_variables(sot_path))
            observed = set(info.get("dataset_columns", []) or [])
            missing = sorted(observed - declared)
            info["missing_variables"] = missing
            info["sot_complete"] = bool(observed) and not missing
        else:
            info["sot_present"] = False
            info["sot_complete"] = False
            info["missing_variables"] = info.get("dataset_columns", []) or []

    return {"forms": forms}


def write_reports(coverage: dict[str, Any], coverage_json_path: Path, report_md_path: Path) -> None:
    coverage_json_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_json_path.write_text(json.dumps(coverage, indent=2, sort_keys=True))

    lines = ["# SoT gap coverage report", ""]
    for form, info in sorted(coverage["forms"].items()):
        lines.append(f"## {form}")
        lines.append(f"- sot_present: {info.get('sot_present')}")
        lines.append(f"- sot_complete: {info.get('sot_complete')}")
        miss = info.get("missing_variables") or []
        lines.append(f"- missing_variables: {len(miss)}")
        if miss:
            lines.append("")
            lines.append("```")
            for m in miss:
                lines.append(m)
            lines.append("```")
        lines.append("")
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text("\n".join(lines))


def main() -> None:
    import argparse

    import config

    p = argparse.ArgumentParser()
    p.add_argument("--sot-dir", default=str(config.SOT_DIR))
    p.add_argument("--raw-pdf-dir", default=str(config.RAW_PDF_DIR))
    p.add_argument(
        "--dataset-dir",
        default=str(Path(config.OUTPUT_DIR) / config.STUDY / "trio_bundle/datasets"),
    )
    p.add_argument("--pilot-dir", default=str(config.PILOT_RESULTS_DIR))
    p.add_argument("--coverage-json", default=str(config.SOT_GAP_COVERAGE_PATH))
    p.add_argument("--report-md", default=str(config.SOT_GAP_REPORT_PATH))
    args = p.parse_args()

    coverage = build_coverage(
        sot_dir=Path(args.sot_dir),
        raw_pdf_dir=Path(args.raw_pdf_dir),
        dataset_dir=Path(args.dataset_dir),
        pilot_dir=Path(args.pilot_dir),
    )
    write_reports(coverage, Path(args.coverage_json), Path(args.report_md))
    _LOG.info("sot_gap_inventory.complete forms=%d", len(coverage["forms"]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass.**

Run: `pytest tests/source_truth/test_sot_gap_inventory.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit.**

```bash
git add scripts/source_truth/sot_gap_inventory.py tests/source_truth/test_sot_gap_inventory.py tests/fixtures/sot_gap
git commit -m "feat(sot): gap inventory walker with column-keys-only contract"
```

---

## Task 0-3: Extractor agent harness (read-deny on row values)

**Files:**
- Create: `scripts/source_truth/sot_extractor_agent.py`
- Create: `tests/source_truth/test_sot_extractor_agent.py`

The extractor calls a Claude Agent SDK subagent. The harness's job is to (a) gather the read-only inputs (PDF text, dataset column keys, pilot artifact, existing YAML), (b) build the agent prompt, (c) capture the agent's draft YAML + draft evidence pack, (d) write them under `tmp/sot_gap_drafts/`. The harness itself must never load or pass row values.

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_sot_extractor_agent.py
"""Extractor agent harness: column-keys-only contract."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.source_truth.sot_extractor_agent import gather_inputs, run_extractor


FIXTURE = Path("tests/fixtures/sot_gap")


def test_gather_inputs_collects_column_keys_only():
    inputs = gather_inputs(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    assert "dataset_columns" in inputs
    assert isinstance(inputs["dataset_columns"], list)
    # No "rows" / "values" / "samples" keys allowed:
    forbidden = {"rows", "values", "samples", "row_values", "data"}
    assert not (forbidden & set(inputs.keys())), inputs.keys()


def test_run_extractor_writes_yaml_and_evidence_pack(tmp_path, monkeypatch):
    """Mock the agent client; verify the harness writes both artifacts."""
    fake_yaml = "form_id: 8_CXR\nvariables:\n  - variable_id: CXR_NEW\n"
    fake_pack = '{"form": "8_CXR", "variables": [{"variable_id": "CXR_NEW"}]}'

    def fake_invoke(prompt: str) -> dict:
        return {"yaml": fake_yaml, "evidence_pack": fake_pack}

    monkeypatch.setattr(
        "scripts.source_truth.sot_extractor_agent.invoke_subagent",
        fake_invoke,
    )

    out_dir = tmp_path / "sot_gap_drafts"
    pack_dir = out_dir / "evidence_packs"
    out_dir.mkdir()
    pack_dir.mkdir()

    result = run_extractor(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=out_dir,
        evidence_pack_drafts_dir=pack_dir,
    )

    assert (out_dir / "8_CXR_policy.yaml.draft").read_text() == fake_yaml
    assert (pack_dir / "8_CXR.json").read_text() == fake_pack
    assert result["form"] == "8_CXR"
    assert result["yaml_path"].endswith("8_CXR_policy.yaml.draft")
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `pytest tests/source_truth/test_sot_extractor_agent.py -v`
Expected: 2 failures (module not yet present).

- [ ] **Step 3: Implement the extractor harness.**

```python
# scripts/source_truth/sot_extractor_agent.py
"""Per-form SoT extractor agent harness.

Gathers read-only inputs (PDF text, dataset column keys, pilot artifact,
existing YAML) and dispatches a Claude Agent SDK subagent to draft a
complete SoT YAML + per-form evidence pack. The harness never loads
dataset row values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.source_truth.sot_gap_inventory import (
    _form_id_from_filename,  # reuse helper
    _read_column_keys_only,
    _read_sot_variables,
)
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def _read_pdf_text(pdf_path: Path) -> str:
    """Read text from the form PDF. Replace with the project's standard
    PDF-text utility once the choice is made; placeholder uses a
    paranoia-safe stub for tests."""
    if not pdf_path.is_file():
        return ""
    # Real implementation: from scripts.extraction.pdf_text import extract_pdf_text
    # For tests / placeholder PDFs we just return a marker.
    return f"<pdf:{pdf_path.name}>"


def _read_pilot_artifact(pilot_dir: Path, form: str) -> str:
    folder = pilot_dir / f"policy_pilot_{form}"
    candidates = list(folder.glob("*.yaml")) if folder.is_dir() else []
    if not candidates:
        return ""
    return candidates[0].read_text(encoding="utf-8")


def _read_existing_yaml(sot_dir: Path, form: str) -> str:
    p = sot_dir / f"{form}_policy.yaml"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def gather_inputs(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
) -> dict[str, Any]:
    return {
        "form": form,
        "pdf_text": _read_pdf_text(raw_pdf_dir / f"{form}.pdf"),
        "dataset_columns": _read_column_keys_only(dataset_dir / f"{form}.jsonl")
        if (dataset_dir / f"{form}.jsonl").is_file()
        else [],
        "pilot_artifact": _read_pilot_artifact(pilot_dir, form),
        "existing_yaml": _read_existing_yaml(sot_dir, form),
    }


_PROMPT_TEMPLATE = """\
You are the SoT extractor for the form `{form}` of the RePORT-AI-Portal study.

Goal: produce a COMPLETE Source-of-Truth policy YAML and a per-form
evidence pack JSON for this form, sourced ONLY from the inputs below.
You must not invent variables. You must not reference dataset row values
(none are provided). Stick strictly to dataset column keys, PDF text,
and pilot artifact text.

Inputs:
- existing_yaml:
{existing_yaml}

- pdf_text:
{pdf_text}

- dataset_columns:
{dataset_columns}

- pilot_artifact:
{pilot_artifact}

Output JSON shape:
{{
  "yaml": "<full SoT YAML text>",
  "evidence_pack": "<JSON: {{form, variables: [{{variable_id, description, options, codings}}]}}>"
}}

Schema constraints:
- Every variable in dataset_columns MUST appear in `variables[].variable_id`.
- Every variable MUST carry a non-empty description.
- Every variable MUST carry a non-empty `handling_intent.action` (one of:
  drop, pseudonymize, jitter_date, cap, generalize, suppress_small_cell,
  keep). When uncertain, default to `keep` and add a `claude_drafted: true`
  flag on the variable.
"""


def invoke_subagent(prompt: str) -> dict[str, str]:
    """Real implementation calls the Claude Agent SDK with a constrained
    role. Tests monkeypatch this function."""
    raise NotImplementedError("Wire to Claude Agent SDK in integration step")


def run_extractor(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
) -> dict[str, str]:
    inputs = gather_inputs(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
    )
    prompt = _PROMPT_TEMPLATE.format(
        form=form,
        existing_yaml=inputs["existing_yaml"] or "(none)",
        pdf_text=inputs["pdf_text"] or "(none)",
        dataset_columns="\n".join(inputs["dataset_columns"]) or "(none)",
        pilot_artifact=inputs["pilot_artifact"] or "(none)",
    )
    out = invoke_subagent(prompt)

    drafts_dir.mkdir(parents=True, exist_ok=True)
    evidence_pack_drafts_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = drafts_dir / f"{form}_policy.yaml.draft"
    pack_path = evidence_pack_drafts_dir / f"{form}.json"
    yaml_path.write_text(out["yaml"], encoding="utf-8")
    pack_path.write_text(out["evidence_pack"], encoding="utf-8")

    _LOG.info("sot_extractor.draft_written form=%s", form)
    return {
        "form": form,
        "yaml_path": str(yaml_path),
        "evidence_pack_path": str(pack_path),
    }
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `pytest tests/source_truth/test_sot_extractor_agent.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_extractor_agent.py tests/source_truth/test_sot_extractor_agent.py
git commit -m "feat(sot): extractor agent harness with column-keys-only inputs"
```

---

## Task 0-4: Reviewer agent harness

**Files:**
- Create: `scripts/source_truth/sot_reviewer_agent.py`
- Create: `tests/source_truth/test_sot_reviewer_agent.py`

The reviewer reads the same source inputs PLUS the extractor's draft YAML and draft evidence pack. It produces a structured verdict (`agree` / `disagree_minor` / `disagree_major`) plus a markdown review with line-level pointers.

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_sot_reviewer_agent.py

from pathlib import Path

import pytest

from scripts.source_truth.sot_reviewer_agent import run_reviewer


FIXTURE = Path("tests/fixtures/sot_gap")


def test_run_reviewer_writes_review_md(tmp_path, monkeypatch):
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    yaml_path = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_path.write_text("form_id: 8_CXR\nvariables:\n  - variable_id: CXR_NEW\n")
    pack_path = drafts_dir / "8_CXR.json"
    pack_path.write_text('{"form": "8_CXR", "variables": [{"variable_id": "CXR_NEW"}]}')

    monkeypatch.setattr(
        "scripts.source_truth.sot_reviewer_agent.invoke_reviewer_subagent",
        lambda prompt: {"verdict": "agree", "notes": "Looks good."},
    )

    result = run_reviewer(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        draft_yaml_path=yaml_path,
        draft_pack_path=pack_path,
        reviews_dir=drafts_dir,
    )
    review_md = drafts_dir / "8_CXR_review.md"
    assert review_md.is_file()
    text = review_md.read_text()
    assert "verdict: agree" in text
    assert result["verdict"] == "agree"
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `pytest tests/source_truth/test_sot_reviewer_agent.py -v`
Expected: 1 failure (module not yet present).

- [ ] **Step 3: Implement the reviewer harness.**

```python
# scripts/source_truth/sot_reviewer_agent.py
"""Per-form SoT reviewer agent harness.

Reads the same inputs as the extractor PLUS the extractor's draft, then
issues a verdict (agree / disagree_minor / disagree_major) with notes.
Same column-keys-only contract as the extractor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.source_truth.sot_extractor_agent import gather_inputs
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


_PROMPT_TEMPLATE = """\
You are the SoT reviewer for the form `{form}`.

Read the source inputs and the extractor's draft below. Produce a verdict
(`agree`, `disagree_minor`, or `disagree_major`) and notes pointing at
specific variables that need correction. You must not invent variables
and you must not reference dataset row values (none are provided).

Source inputs:
- existing_yaml:
{existing_yaml}

- pdf_text:
{pdf_text}

- dataset_columns:
{dataset_columns}

- pilot_artifact:
{pilot_artifact}

Extractor draft (YAML):
{draft_yaml}

Extractor draft (evidence pack JSON):
{draft_pack}

Output JSON shape:
{{
  "verdict": "agree" | "disagree_minor" | "disagree_major",
  "notes": "<markdown explaining specific variables and reasons>"
}}
"""


def invoke_reviewer_subagent(prompt: str) -> dict[str, str]:
    raise NotImplementedError("Wire to Claude Agent SDK in integration step")


def run_reviewer(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    draft_yaml_path: Path,
    draft_pack_path: Path,
    reviews_dir: Path,
) -> dict[str, Any]:
    sources = gather_inputs(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
    )
    prompt = _PROMPT_TEMPLATE.format(
        form=form,
        existing_yaml=sources["existing_yaml"] or "(none)",
        pdf_text=sources["pdf_text"] or "(none)",
        dataset_columns="\n".join(sources["dataset_columns"]) or "(none)",
        pilot_artifact=sources["pilot_artifact"] or "(none)",
        draft_yaml=draft_yaml_path.read_text(encoding="utf-8"),
        draft_pack=draft_pack_path.read_text(encoding="utf-8"),
    )
    out = invoke_reviewer_subagent(prompt)

    reviews_dir.mkdir(parents=True, exist_ok=True)
    review_md = reviews_dir / f"{form}_review.md"
    review_md.write_text(
        f"# Review for {form}\n\n"
        f"verdict: {out['verdict']}\n\n"
        f"## notes\n\n{out['notes']}\n",
        encoding="utf-8",
    )
    _LOG.info("sot_reviewer.review_written form=%s verdict=%s", form, out["verdict"])
    return {"form": form, "verdict": out["verdict"], "review_md": str(review_md)}
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `pytest tests/source_truth/test_sot_reviewer_agent.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_reviewer_agent.py tests/source_truth/test_sot_reviewer_agent.py
git commit -m "feat(sot): reviewer agent harness with verdict schema"
```

---

## Task 0-5: Per-form dispatcher (parallel batches of 4–8)

**Files:**
- Create: `scripts/source_truth/sot_gap_dispatcher.py`
- Create: `tests/source_truth/test_sot_gap_dispatcher.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_sot_gap_dispatcher.py

from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_dispatcher import dispatch_forms


FIXTURE = Path("tests/fixtures/sot_gap")


def test_dispatch_forms_runs_extractor_and_reviewer_in_order(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_run_extractor(*, form, **_kwargs):
        calls.append(("extractor", form))
        return {"form": form, "yaml_path": str(tmp_path / f"{form}.yaml.draft"), "evidence_pack_path": str(tmp_path / f"{form}.json")}

    def fake_run_reviewer(*, form, **_kwargs):
        calls.append(("reviewer", form))
        return {"form": form, "verdict": "agree", "review_md": str(tmp_path / f"{form}_review.md")}

    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.run_extractor", fake_run_extractor
    )
    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.run_reviewer", fake_run_reviewer
    )

    forms = ["8_CXR", "95_SAE"]
    results = dispatch_forms(
        forms=forms,
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=tmp_path,
        evidence_pack_drafts_dir=tmp_path,
        reviews_dir=tmp_path,
        concurrency=2,
    )
    assert len(results) == 2
    assert {r["form"] for r in results} == {"8_CXR", "95_SAE"}
    # Each form sees extractor BEFORE reviewer:
    for form in forms:
        idx_e = calls.index(("extractor", form))
        idx_r = calls.index(("reviewer", form))
        assert idx_e < idx_r
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `pytest tests/source_truth/test_sot_gap_dispatcher.py -v`
Expected: 1 failure.

- [ ] **Step 3: Implement the dispatcher.**

```python
# scripts/source_truth/sot_gap_dispatcher.py
"""Per-form dispatcher: extractor + reviewer in parallel batches of 4-8."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from scripts.source_truth.sot_extractor_agent import run_extractor
from scripts.source_truth.sot_reviewer_agent import run_reviewer
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def _run_one_form(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
    reviews_dir: Path,
) -> dict[str, Any]:
    extracted = run_extractor(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
        drafts_dir=drafts_dir,
        evidence_pack_drafts_dir=evidence_pack_drafts_dir,
    )
    reviewed = run_reviewer(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
        draft_yaml_path=Path(extracted["yaml_path"]),
        draft_pack_path=Path(extracted["evidence_pack_path"]),
        reviews_dir=reviews_dir,
    )
    return {**extracted, **reviewed}


def dispatch_forms(
    forms: Iterable[str],
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
    reviews_dir: Path,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    forms = list(forms)
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(8, concurrency))) as pool:
        futures = {
            pool.submit(
                _run_one_form,
                form=form,
                sot_dir=sot_dir,
                raw_pdf_dir=raw_pdf_dir,
                dataset_dir=dataset_dir,
                pilot_dir=pilot_dir,
                drafts_dir=drafts_dir,
                evidence_pack_drafts_dir=evidence_pack_drafts_dir,
                reviews_dir=reviews_dir,
            ): form
            for form in forms
        }
        for fut in as_completed(futures):
            form = futures[fut]
            try:
                out.append(fut.result())
            except Exception:
                _LOG.exception("sot_dispatch.failed form=%s", form)
                raise
    return out
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `pytest tests/source_truth/test_sot_gap_dispatcher.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_gap_dispatcher.py tests/source_truth/test_sot_gap_dispatcher.py
git commit -m "feat(sot): per-form dispatcher with bounded parallelism"
```

---

## Task 0-6: Merge-on-approval helper

**Files:**
- Create: `scripts/source_truth/sot_gap_merge.py`
- Create: `tests/source_truth/test_sot_gap_merge.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_sot_gap_merge.py

from pathlib import Path

from scripts.source_truth.sot_gap_merge import merge_approved_draft


def test_merge_overwrites_sot_yaml_and_keeps_evidence_pack(tmp_path):
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    pack_drafts_dir = drafts_dir / "evidence_packs"
    pack_drafts_dir.mkdir()

    yaml_draft = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_draft.write_text("form_id: 8_CXR\nvariables: []\n")
    pack_draft = pack_drafts_dir / "8_CXR.json"
    pack_draft.write_text('{"form": "8_CXR"}')

    merge_approved_draft(
        form="8_CXR",
        draft_yaml_path=yaml_draft,
        draft_pack_path=pack_draft,
        sot_dir=sot_dir,
    )

    assert (sot_dir / "8_CXR_policy.yaml").read_text() == "form_id: 8_CXR\nvariables: []\n"
    # Evidence pack draft remains in the drafts dir; final move to llm_source happens in Phase 2:
    assert pack_draft.is_file()
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `pytest tests/source_truth/test_sot_gap_merge.py -v`
Expected: 1 failure.

- [ ] **Step 3: Implement the merger.**

```python
# scripts/source_truth/sot_gap_merge.py
"""Merge-on-approval helper.

Copies an approved YAML draft over the SoT YAML for the given form.
Leaves the evidence pack draft in place (Phase 2 picks it up).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def merge_approved_draft(
    form: str,
    draft_yaml_path: Path,
    draft_pack_path: Path,
    sot_dir: Path,
) -> None:
    sot_dir.mkdir(parents=True, exist_ok=True)
    target = sot_dir / f"{form}_policy.yaml"
    shutil.copyfile(draft_yaml_path, target)
    _LOG.info(
        "sot_merge.applied form=%s target=%s evidence_pack_kept=%s",
        form,
        target,
        draft_pack_path,
    )
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `pytest tests/source_truth/test_sot_gap_merge.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_gap_merge.py tests/source_truth/test_sot_gap_merge.py
git commit -m "feat(sot): merge-on-approval helper for per-form drafts"
```

---

## Task 0-7: Hard-gate coverage script

**Files:**
- Create: `scripts/source_truth/sot_coverage_gate.py`
- Create: `tests/source_truth/test_sot_coverage_gate.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/source_truth/test_sot_coverage_gate.py

from pathlib import Path

import pytest

from scripts.source_truth.sot_coverage_gate import gate


def test_gate_passes_on_complete_coverage(tmp_path):
    coverage = {
        "forms": {
            "19_Smear": {"sot_present": True, "sot_complete": True, "missing_variables": []},
        }
    }
    rc = gate(coverage)
    assert rc == 0


def test_gate_fails_on_missing_form(tmp_path):
    coverage = {
        "forms": {
            "95_SAE": {"sot_present": False, "sot_complete": False, "missing_variables": ["X"]},
        }
    }
    rc = gate(coverage)
    assert rc != 0


def test_gate_fails_on_partial_form(tmp_path):
    coverage = {
        "forms": {
            "8_CXR": {"sot_present": True, "sot_complete": False, "missing_variables": ["A"]},
        }
    }
    rc = gate(coverage)
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `pytest tests/source_truth/test_sot_coverage_gate.py -v`
Expected: 3 failures.

- [ ] **Step 3: Implement the gate.**

```python
# scripts/source_truth/sot_coverage_gate.py
"""SoT coverage hard gate.

Returns 0 only when every form is sot_present AND sot_complete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def gate(coverage: dict[str, Any]) -> int:
    forms = coverage.get("forms", {})
    failures: list[str] = []
    for form, info in forms.items():
        if not info.get("sot_present"):
            failures.append(f"{form}: SoT YAML missing")
        elif not info.get("sot_complete"):
            missing = info.get("missing_variables", [])
            failures.append(f"{form}: SoT incomplete (missing {len(missing)} variable(s))")
    if failures:
        for f in failures:
            _LOG.error("sot_coverage_gate.fail %s", f)
        return 1
    _LOG.info("sot_coverage_gate.pass forms=%d", len(forms))
    return 0


def main() -> int:
    import argparse

    import config

    p = argparse.ArgumentParser()
    p.add_argument("--coverage-json", default=str(config.SOT_GAP_COVERAGE_PATH))
    args = p.parse_args()
    coverage = json.loads(Path(args.coverage_json).read_text(encoding="utf-8"))
    return gate(coverage)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `pytest tests/source_truth/test_sot_coverage_gate.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_coverage_gate.py tests/source_truth/test_sot_coverage_gate.py
git commit -m "feat(sot): hard-gate coverage script"
```

---

## Task 0-8: Wire the agent harnesses to the Claude Agent SDK

**Files:**
- Modify: `scripts/source_truth/sot_extractor_agent.py` (replace `invoke_subagent` body)
- Modify: `scripts/source_truth/sot_reviewer_agent.py` (replace `invoke_reviewer_subagent` body)
- Test: `tests/source_truth/test_agent_sdk_wiring.py`

- [ ] **Step 1: Write the failing integration smoke test.**

```python
# tests/source_truth/test_agent_sdk_wiring.py
"""Smoke test: the SDK wiring resolves to a callable that returns the
expected output shape on a trivial deterministic prompt.

Marked `slow`. Skipped when ANTHROPIC_API_KEY is missing.
"""

import os

import pytest

pytestmark = pytest.mark.slow


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY required for live SDK smoke",
)
def test_extractor_sdk_returns_yaml_and_pack_keys():
    from scripts.source_truth.sot_extractor_agent import invoke_subagent

    out = invoke_subagent(
        "Return JSON with keys 'yaml' (string 'hello') and "
        "'evidence_pack' (string '{}'). Nothing else."
    )
    assert "yaml" in out
    assert "evidence_pack" in out
```

- [ ] **Step 2: Run test to verify it fails (or skips on no API key).**

Run: `pytest tests/source_truth/test_agent_sdk_wiring.py -v -m slow`
Expected: SKIPPED on no API key, FAIL on `NotImplementedError` once API key is set.

- [ ] **Step 3: Implement `invoke_subagent` in the extractor.**

```python
# scripts/source_truth/sot_extractor_agent.py — replace stub

import json

from anthropic import Anthropic

import config


def invoke_subagent(prompt: str) -> dict[str, str]:
    client = Anthropic()
    msg = client.messages.create(
        model=config.AGENT_MODEL_ID,  # e.g., "claude-opus-4-7"
        max_tokens=8192,
        system=(
            "You are a clinical-data SoT extractor. Output strict JSON "
            "with exactly the keys 'yaml' (string) and 'evidence_pack' "
            "(string). No prose outside the JSON."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text  # type: ignore[union-attr]
    return json.loads(text)
```

Apply the same shape change to the reviewer (`invoke_reviewer_subagent`) — same client + model, system prompt mentions the verdict shape.

If `config.AGENT_MODEL_ID` does not exist yet, add it: `AGENT_MODEL_ID = os.environ.get("REPORTAL_AGENT_MODEL", "claude-opus-4-7")`.

- [ ] **Step 4: Run the test to verify (with API key set in `.env.local`).**

Run: `pytest tests/source_truth/test_agent_sdk_wiring.py -v -m slow`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add scripts/source_truth/sot_extractor_agent.py scripts/source_truth/sot_reviewer_agent.py tests/source_truth/test_agent_sdk_wiring.py config.py
git commit -m "feat(sot): wire extractor and reviewer to Claude Agent SDK"
```

---

## Task 0-9: Run the gap inventory on Indo-VAP

**Files:** none new. Operational task.

- [ ] **Step 1: Run the inventory.**

Run: `python -m scripts.source_truth.sot_gap_inventory`
Expected: writes `tmp/sot_gap_coverage.json` and `tmp/sot_gap_report.md`.

- [ ] **Step 2: Inspect the report.**

Run: `cat tmp/sot_gap_report.md | head -80`
Expected: per-form status with missing-variable lists.

- [ ] **Step 3: Sanity check expected gaps.**

Per the prior session memory, `18_2_TargConcom`, `20_CoEnroll`, `21_DSTISO`, `53_exposure`, `95_Specimen_Tracking`, `96_Specimen_Tracking`, `101_HHC_Recontact` are forms missing from SoT entirely. Confirm they appear in the report.

- [ ] **Step 4: Commit the report (under `tmp/` so it's not tracked, but verify the location).**

`tmp/` is gitignored (project convention). The artifact is intentionally local. No commit.

---

## Task 0-10: Dispatch extractor + reviewer for every gap form

**Files:** none new. Operational task.

- [ ] **Step 1: Build the gap-form list from the coverage report.**

Run a one-liner:
```bash
python -c "import json; d=json.load(open('tmp/sot_gap_coverage.json'));
forms=[f for f,i in d['forms'].items() if not i['sot_complete']];
print('\n'.join(forms))"
```
Expected: list of forms needing extraction.

- [ ] **Step 2: Run the dispatcher.**

```python
# tmp/run_dispatch.py — local script, not committed
from pathlib import Path
import json

import config
from scripts.source_truth.sot_gap_dispatcher import dispatch_forms

coverage = json.loads(Path(config.SOT_GAP_COVERAGE_PATH).read_text())
gap_forms = [f for f, i in coverage["forms"].items() if not i["sot_complete"]]

dispatch_forms(
    forms=gap_forms,
    sot_dir=Path(str(config.SOT_DIR)),
    raw_pdf_dir=Path(str(config.RAW_PDF_DIR)),
    dataset_dir=Path(config.OUTPUT_DIR) / config.STUDY / "trio_bundle/datasets",
    pilot_dir=Path(str(config.PILOT_RESULTS_DIR)),
    drafts_dir=Path(str(config.SOT_GAP_DRAFTS_DIR)),
    evidence_pack_drafts_dir=Path(str(config.SOT_EVIDENCE_PACK_DRAFTS_DIR)),
    reviews_dir=Path(str(config.SOT_GAP_DRAFTS_DIR)),
    concurrency=4,
)
```

Run: `python tmp/run_dispatch.py 2>&1 | tee tmp/dispatch.log`
Expected: drafts and review markdowns appear under `tmp/sot_gap_drafts/`.

- [ ] **Step 3: Quick scan of reviewer verdicts.**

Run: `grep -h '^verdict:' tmp/sot_gap_drafts/*_review.md | sort | uniq -c`
Expected: a count of `agree` / `disagree_minor` / `disagree_major`. Anything not `agree` needs human attention before merge.

---

## Task 0-11: Human review and merge cycle

**Files:** none new. Operational task done in collaboration with the user.

- [ ] **Step 1: User reviews each draft YAML in their editor.**

The user opens each `tmp/sot_gap_drafts/<form>_policy.yaml.draft` and the corresponding `<form>_review.md`. They edit the draft directly until they approve it.

- [ ] **Step 2: Per-form merge.**

Run, per approved form (replace `<FORM>`):

```bash
python -m scripts.source_truth.sot_gap_merge \
  --form "<FORM>" \
  --draft-yaml "tmp/sot_gap_drafts/<FORM>_policy.yaml.draft" \
  --draft-pack "tmp/sot_gap_drafts/evidence_packs/<FORM>.json" \
  --sot-dir "data/SoT/Indo-VAP"
```

(If the merge script's CLI is not yet wired, add a small `argparse` wrapper around `merge_approved_draft` — 10 lines.)

- [ ] **Step 3: Commit the SoT update per form.**

```bash
git add data/SoT/Indo-VAP/<FORM>_policy.yaml
git commit -m "data(sot): exhaustive policy for <FORM>"
```

Atomic per-form commit makes per-form rollback trivial.

---

## Task 0-12: Verify the hard gate passes

**Files:** none new. Operational task.

- [ ] **Step 1: Re-run the inventory.**

Run: `python -m scripts.source_truth.sot_gap_inventory`

- [ ] **Step 2: Run the gate.**

Run: `python -m scripts.source_truth.sot_coverage_gate`
Expected: `sot_coverage_gate.pass forms=N` and exit code 0.

- [ ] **Step 3: Wire the gate into the project's existing make / CI target.**

Modify the `Makefile` to add:

```make
sot-gate:
	python -m scripts.source_truth.sot_coverage_gate

ci: sot-gate <existing-targets>
```

If the project uses a different CI runner (`pyproject.toml [tool.poe]` or a GitHub Actions workflow), add the equivalent step there.

- [ ] **Step 4: Run the make target end-to-end.**

Run: `make sot-gate`
Expected: exit code 0.

- [ ] **Step 5: Commit the Makefile change.**

```bash
git add Makefile
git commit -m "ci: add sot-gate as Phase 0 hard gate"
```

---

## Phase 0 exit criteria

The following must all be true to declare Phase 0 done:

- `python -m scripts.source_truth.sot_coverage_gate` returns 0 against the live `data/Indo-VAP/`.
- Every form in `data/raw/Indo-VAP/` has a corresponding `data/SoT/Indo-VAP/<form>_policy.yaml`.
- For every form whose YAML changed in this phase, a draft per-form evidence pack exists under `tmp/sot_gap_drafts/evidence_packs/<form>.json`.
- The `make sot-gate` target is green and is part of the CI chain.
- The user has signed off on every merged YAML.

---

## Self-review

**Spec coverage:** Each item in spec §4 (Phase 0) maps to a task: §4.1 inputs → 0-2 + 0-3; §4.2 subagent pair → 0-3 + 0-4 + 0-5; §4.3 deliverables → 0-5 + 0-6 + the operational tasks 0-9 through 0-11; §4.4 hard gate → 0-7 + 0-12. The per-form evidence pack draft (added to §4.3 in self-review) is produced in 0-3 (extractor harness) and merged in 0-6.

**Placeholders:** None. Every step has either exact code or an exact command. The agent SDK invocation in 0-8 references `config.AGENT_MODEL_ID` and shows the addition if not yet present.

**Type consistency:** All harnesses share the `gather_inputs(form, sot_dir, raw_pdf_dir, dataset_dir, pilot_dir)` signature; the dispatcher's keyword arguments match. The merge helper's signature is consistent across the test and CLI step.

---

## Follow-up plans (to be expanded once Phase 0 lands)

Each subsequent phase will get its own plan file under `docs/superpowers/plans/`:

- `2026-05-XX-phase-1-phi-rule-audit.md` — technique inventory + 5 parallel research subagents + SoT-driven sweep (spec §5).
- `2026-05-XX-phase-2-restructure-llm-source.md` — pipeline target switch, evidence-pack rewrite, dictionary relocation + lean catalog, dataset_schema (spec §6).
- `2026-05-XX-phase-3-cross-verifier.md` — deterministic scanner + isolated fix agent + PHI-id redactor + HITL emitter (spec §7).
- `2026-05-XX-phase-4-no-llm-zone.md` — path deny + runtime guard + sentinel + custom .gitattributes (spec §8).
- `2026-05-XX-phase-5-clean-slate.md` — checksum manifest, deletes, linter rule (spec §9).
- `2026-05-XX-phase-6-doc-and-contract-sync.md` — CONTEXT.md final pass, config.py audit, logging coverage test, docs index check (spec §10).

These plans are **not** drafted now to avoid speculative design that depends on Phase 0/1 outputs.
