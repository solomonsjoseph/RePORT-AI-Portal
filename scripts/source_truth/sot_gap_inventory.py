"""SoT gap inventory.

For each form in raw_pdf_dir or dataset_dir, report whether a SoT YAML
exists and whether every observed dataset column key is declared as a
variable in the SoT YAML. Reads ONLY column keys from the dataset (line 1
parsed, then discarded) — never row values.

Phase 0 additions:
- alias map: runtime form-id variants resolve to canonical form-ids.
- dataset_policies subdir: forms with no PDF but a dataset-only YAML.
- exclusions registry: deprecated / out-of-scope forms skip gap failures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)

DEFAULT_PIPELINE_METADATA_COLUMNS: frozenset[str] = frozenset({
    "source_file",
    "_provenance",
    "_phi_scrubbed",
})


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def _form_id_from_filename(name: str) -> str:
    return name.rsplit(".", 1)[0]


def _read_column_keys_only(jsonl_path: Path) -> list[str]:
    with jsonl_path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        return []
    return list(json.loads(first).keys())


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


def _load_alias_map(alias_map_path: Path) -> dict[str, str]:
    """Return {variant: canonical} from aliases.yaml, or empty dict if missing."""
    if not alias_map_path.is_file():
        return {}
    raw = alias_map_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return {}
    aliases = data.get("aliases") or {}
    return {str(k): str(v) for k, v in aliases.items()} if isinstance(aliases, dict) else {}


def _load_exclusions(excluded_path: Path) -> dict[str, dict[str, Any]]:
    """Return {form_id: {reason, notes, ...}} from excluded_from_sot.yaml, or empty dict."""
    if not excluded_path.is_file():
        return {}
    raw = excluded_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return {}
    exclusions = data.get("exclusions") or {}
    return dict(exclusions) if isinstance(exclusions, dict) else {}


def _resolve_pdf_for_form(raw_pdf_dir: Path, form_id: str) -> Path | None:
    """Find the PDF for a form by matching its leading token.

    Form IDs look like '10_TST', '1A_ICScreening', '99B_FSB'.
    PDFs are named like '10 TST screening v1.0.pdf', '1A Index Case Screening v1.0.pdf'.
    The leading token (before the first underscore in form_id, or the
    first space in the PDF name) is the matching key.
    """
    if not raw_pdf_dir.is_dir():
        return None
    leading = form_id.split("_", 1)[0]  # "10", "1A", "99B"
    for pdf in raw_pdf_dir.rglob("*.pdf"):
        first_token = pdf.stem.split(" ", 1)[0]
        if first_token == leading:
            return pdf
    return None


def build_coverage(
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    pipeline_metadata_columns: frozenset[str] = DEFAULT_PIPELINE_METADATA_COLUMNS,
    alias_map_path: Path | None = None,
    excluded_path: Path | None = None,
) -> dict[str, Any]:
    sot_dir = Path(sot_dir)
    raw_pdf_dir = Path(raw_pdf_dir)
    dataset_dir = Path(dataset_dir)
    pilot_dir = Path(pilot_dir)

    # Resolve default paths from sot_dir parent (or sot_dir itself for exclusions).
    if alias_map_path is None:
        alias_map_path = sot_dir.parent / "aliases.yaml"
        if not alias_map_path.is_file():
            alias_map_path = sot_dir / "aliases.yaml"
    if excluded_path is None:
        excluded_path = sot_dir / "excluded_from_sot.yaml"

    alias_map = _load_alias_map(Path(alias_map_path))
    exclusions = _load_exclusions(Path(excluded_path))
    dataset_policies_dir = sot_dir / "dataset_policies"

    forms: dict[str, dict[str, Any]] = {}
    observed_cols: dict[str, list[str]] = {}

    if dataset_dir.is_dir():
        for jsonl in sorted(dataset_dir.glob("*.jsonl")):
            form = _form_id_from_filename(jsonl.name)
            forms.setdefault(form, {"observed_in": []})
            forms[form]["observed_in"].append("dataset")
            observed_cols[form] = _read_column_keys_only(jsonl)

    if pilot_dir.is_dir():
        for form_dir in sorted(p for p in pilot_dir.glob("policy_pilot_*") if p.is_dir()):
            form = form_dir.name.removeprefix("policy_pilot_")
            forms.setdefault(form, {"observed_in": []})
            forms[form]["pilot_present"] = True

    # Resolve PDFs for all known forms using fuzzy leading-token matching (recursive).
    for form, info in forms.items():
        pdf_path = _resolve_pdf_for_form(raw_pdf_dir, form)
        if pdf_path:
            info.setdefault("observed_in", []).append("pdf")
            info["pdf_path"] = str(pdf_path)

    for form, info in forms.items():
        cols = observed_cols.get(form, [])

        # --- Exclusions check ---
        if form in exclusions:
            exc = exclusions[form]
            info["excluded"] = True
            info["exclusion_reason"] = exc.get("reason", "")
            info["sot_present"] = True   # vacuously satisfied
            info["sot_complete"] = True  # vacuously satisfied
            info["missing_variables"] = []
            _LOG.info("sot_gap_inventory.excluded form=%s reason=%s", form, info["exclusion_reason"])
            continue

        # --- Alias resolution ---
        canonical = alias_map.get(form)
        if canonical is not None:
            info["alias_of"] = canonical
            # Check whether the canonical form has a policy in either location.
            canonical_path = sot_dir / f"{canonical}_policy.yaml"
            canonical_dataset_path = (
                dataset_policies_dir / f"{canonical}_policy.yaml"
                if dataset_policies_dir.is_dir()
                else None
            )
            canonical_present = canonical_path.is_file() or (
                canonical_dataset_path is not None and canonical_dataset_path.is_file()
            )
            info["sot_present"] = canonical_present
            info["sot_complete"] = canonical_present  # alias: presence implies complete
            info["missing_variables"] = []
            if canonical_present:
                policy_src = (
                    "dataset_columns_only"
                    if canonical_dataset_path is not None and canonical_dataset_path.is_file()
                    else "pdf_derived"
                )
                info["policy_source"] = policy_src
            _LOG.info(
                "sot_gap_inventory.alias form=%s canonical=%s present=%s",
                form, canonical, canonical_present,
            )
            continue

        # --- Normal form: look in sot_dir then dataset_policies ---
        sot_path = sot_dir / f"{form}_policy.yaml"
        dataset_policy_path = (
            dataset_policies_dir / f"{form}_policy.yaml"
            if dataset_policies_dir.is_dir()
            else None
        )

        if sot_path.is_file():
            active_path = sot_path
            policy_source = "pdf_derived"
        elif dataset_policy_path is not None and dataset_policy_path.is_file():
            active_path = dataset_policy_path
            policy_source = "dataset_columns_only"
        else:
            active_path = None
            policy_source = None

        if active_path is not None:
            info["sot_present"] = True
            info["policy_source"] = policy_source
            declared = set(_read_sot_variables(active_path))
            observed = set(cols) - pipeline_metadata_columns
            missing = sorted(observed - declared)
            info["missing_variables"] = missing
            # Complete when no observed column is undeclared.  If the dataset
            # has no columns (e.g. empty JSONL), the policy vacuously covers
            # everything — treat as complete so PDF-only forms don't block the gate.
            info["sot_complete"] = not missing
        else:
            info["sot_present"] = False
            info["sot_complete"] = False
            info["missing_variables"] = list(cols)

    return {"forms": forms}


def write_reports(
    coverage: dict[str, Any],
    coverage_json_path: Path,
    report_md_path: Path,
) -> None:
    _atomic_write_text(coverage_json_path, json.dumps(coverage, indent=2, sort_keys=True))

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
    _atomic_write_text(report_md_path, "\n".join(lines))


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
