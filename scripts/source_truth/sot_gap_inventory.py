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
    return name.rsplit(".", 1)[0]


def _read_column_keys_only(jsonl_path: Path) -> list[str]:
    with jsonl_path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        return []
    obj = json.loads(first)
    keys = list(obj.keys())
    del obj
    return keys


def _read_sot_variables(sot_path: Path) -> list[str]:
    raw = sot_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return []
    variables = data.get("variables") or []
    out: list[str] = []
    if isinstance(variables, dict):
        # Real SoT YAML format: variables is a mapping keyed by variable name
        out = [str(k) for k in variables.keys()]
    elif isinstance(variables, list):
        # Fixture / new-style format: variables is a list of dicts with variable_id
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

    if dataset_dir.is_dir():
        for jsonl in sorted(dataset_dir.glob("*.jsonl")):
            form = _form_id_from_filename(jsonl.name)
            forms.setdefault(form, {"observed_in": []})
            forms[form]["observed_in"].append("dataset")
            forms[form]["dataset_columns"] = _read_column_keys_only(jsonl)

    if raw_pdf_dir.is_dir():
        for pdf in sorted(raw_pdf_dir.glob("*.pdf")):
            form = _form_id_from_filename(pdf.name)
            forms.setdefault(form, {"observed_in": []})
            forms[form]["observed_in"].append("pdf")

    if pilot_dir.is_dir():
        for form_dir in sorted(p for p in pilot_dir.glob("policy_pilot_*") if p.is_dir()):
            form = form_dir.name.removeprefix("policy_pilot_")
            forms.setdefault(form, {"observed_in": []})
            forms[form]["pilot_present"] = True

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
            info["missing_variables"] = list(info.get("dataset_columns", []) or [])

    return {"forms": forms}


def write_reports(
    coverage: dict[str, Any],
    coverage_json_path: Path,
    report_md_path: Path,
) -> None:
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

    p = argparse.ArgumentParser(description="Build SoT gap coverage inventory.")
    p.add_argument("--sot-dir", default=str(config.SOT_DIR))
    p.add_argument("--raw-pdf-dir", default=str(config.RAW_PDF_DIR))
    p.add_argument(
        "--dataset-dir",
        default=str(config.TRIO_BUNDLE_DIR / "datasets"),
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
