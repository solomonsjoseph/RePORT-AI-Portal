"""Generate and verify runtime lean SoT YAMLs for PDF-backed forms.

This is the repo-level orchestration wrapper around the sot-lean-generator
helper scripts. It keeps the Source Truth runtime build reproducible without
moving row-2+ dataset values into the SoT path:

1. Resolve each PDF-backed form.
2. Build a source pack from the PDF plus dataset row-1 headers only.
3. Generate a lean YAML candidate into ``/tmp``.
4. Verify the candidate against the source pack.
5. Promote only verified YAMLs into ``output/<study>/llm_source/source_truth``.
"""

# ruff: noqa: S108

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from scripts.source_truth.study_intake import _find_dataset, _find_pdf, _form_code

SUPPORTED_DATASET_SUFFIXES = (".xlsx", ".xlsm", ".csv")

# Indo-VAP has a few raw datasets sharing the same leading form code. The SoT
# runtime policy is generated for the dataset that corresponds to the printed
# annotated CRF. The other datasets remain published under dataset_schema/files.
PDF_FORM_DATASET_OVERRIDES: dict[str, dict[str, str]] = {
    "Indo-VAP": {
        "2A": "2A_ICBaseline",
        "14": "14_CaseControl",
        "18": "18_NonConsent",
        "95": "95_SAE",
    }
}


def _natural_code_key(code: str) -> tuple[int, str]:
    digits = "".join(ch for ch in code if ch.isdigit())
    suffix = code[len(digits):]
    return (int(digits or 0), suffix)


def discover_pdf_backed_forms(study_dir: Path, study: str) -> list[str]:
    """Return dataset-form ids that have an annotated PDF authority."""

    pdf_dir = study_dir / "annotated_pdfs"
    dataset_dir = study_dir / "datasets"
    if not pdf_dir.is_dir():
        raise FileNotFoundError(f"annotated PDF directory not found: {pdf_dir}")
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"dataset directory not found: {dataset_dir}")

    pdf_codes = {_form_code(path.stem) for path in pdf_dir.glob("*.pdf")}
    datasets_by_code: dict[str, list[Path]] = defaultdict(list)
    for suffix in SUPPORTED_DATASET_SUFFIXES:
        for dataset in dataset_dir.glob(f"*{suffix}"):
            datasets_by_code[_form_code(dataset.stem)].append(dataset)

    overrides = PDF_FORM_DATASET_OVERRIDES.get(study, {})
    forms: list[str] = []
    problems: list[str] = []
    for code in sorted(pdf_codes, key=_natural_code_key):
        override = overrides.get(code)
        if override:
            if (dataset_dir / f"{override}.xlsx").exists() or any(
                (dataset_dir / f"{override}{suffix}").exists()
                for suffix in SUPPORTED_DATASET_SUFFIXES
            ):
                forms.append(override)
                continue
            problems.append(f"override for form code {code} points to missing dataset {override}")
            continue

        candidates = sorted({path.stem for path in datasets_by_code.get(code, [])})
        if len(candidates) == 1:
            forms.append(candidates[0])
        elif not candidates:
            problems.append(f"no dataset found for annotated PDF form code {code}")
        else:
            problems.append(
                f"ambiguous datasets for annotated PDF form code {code}: {', '.join(candidates)}"
            )

    if problems:
        joined = "\n  - ".join(problems)
        raise RuntimeError(f"Could not discover PDF-backed forms:\n  - {joined}")
    return forms


def _run_result(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)  # noqa: S603


def _run(cmd: list[str], *, cwd: Path) -> None:
    result = _run_result(cmd, cwd=cwd)
    if result.returncode == 0:
        if result.stdout.strip():
            print(result.stdout.strip())
        return
    message = [
        f"command failed with exit {result.returncode}: {' '.join(cmd)}",
        result.stdout.strip(),
        result.stderr.strip(),
    ]
    raise RuntimeError("\n".join(part for part in message if part))


def _print_result_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


def generate_form(repo_root: Path, study: str, form: str, out_dir: Path) -> Path:
    """Generate, verify, and promote one form's lean YAML."""

    study_dir = repo_root / "data" / "raw" / study
    pdf = _find_pdf(study_dir, form)
    dataset = _find_dataset(study_dir, form)
    if pdf is None:
        raise FileNotFoundError(f"no annotated PDF found for {study}/{form}")
    if dataset is None:
        raise FileNotFoundError(f"no dataset found for {study}/{form}")

    extract_script = repo_root / "skills" / "sot-lean-generator" / "scripts" / "extract_sources.py"
    generator_script = (
        repo_root / "skills" / "sot-lean-generator" / "scripts" / "generate_pdf_aware_candidate.py"
    )
    checker_script = repo_root / "skills" / "sot-lean-generator" / "scripts" / "check_lean_policy.py"
    diff_script = repo_root / "scripts" / "source_truth" / "diff_against_gold.py"

    source_pack = Path(f"/tmp/sot_source_pack_{form}.json")
    render_dir = Path(f"/tmp/sot_render_{form}")
    candidate = Path(f"/tmp/{form}_lean.yaml")
    promoted = out_dir / f"{form}_policy.lean.yaml"
    gold = repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml"

    _run(
        [
            sys.executable,
            str(extract_script),
            "--repo-root",
            str(repo_root),
            "--pdf",
            str(pdf),
            "--dataset",
            str(dataset),
            "--out",
            str(source_pack),
            "--render-dir",
            str(render_dir),
        ],
        cwd=repo_root,
    )
    _run(
        [
            sys.executable,
            str(generator_script),
            "--repo-root",
            str(repo_root),
            "--form",
            form,
            "--source-pack",
            str(source_pack),
            "--out",
            str(candidate),
        ],
        cwd=repo_root,
    )
    _run(
        [
            sys.executable,
            str(checker_script),
            "--lean",
            str(candidate),
            "--source-pack",
            str(source_pack),
            "--repo-root",
            str(repo_root),
        ],
        cwd=repo_root,
    )
    if gold.exists():
        diff_cmd = [
            sys.executable,
            str(diff_script),
            "--study",
            study,
            "--form",
            form,
            "--candidate",
            str(candidate),
            "--repo-root",
            str(repo_root),
        ]
        diff_result = _run_result(diff_cmd, cwd=repo_root)
        _print_result_output(diff_result)
        if diff_result.returncode == 1:
            print(
                f"  anchored candidate rejected for {study}/{form}; "
                "verifying and promoting anchored gold instead",
                flush=True,
            )
            _run(
                [
                    sys.executable,
                    str(checker_script),
                    "--lean",
                    str(gold),
                    "--source-pack",
                    str(source_pack),
                    "--repo-root",
                    str(repo_root),
                ],
                cwd=repo_root,
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gold, promoted)
            return promoted
        if diff_result.returncode != 0:
            message = [
                f"command failed with exit {diff_result.returncode}: {' '.join(diff_cmd)}",
                diff_result.stdout.strip(),
                diff_result.stderr.strip(),
            ]
            raise RuntimeError("\n".join(part for part in message if part))
    else:
        print(f"  gold diff skipped for {study}/{form}: no anchored gold at {gold}", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, promoted)
    return promoted


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, help="Study folder name, e.g. Indo-VAP")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--form",
        action="append",
        dest="forms",
        help="Form id to generate. Repeat for multiple forms. Defaults to all PDF-backed forms.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory. Defaults to output/<study>/llm_source/source_truth.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    study_dir = repo_root / "data" / "raw" / args.study
    out_dir = args.out_dir or repo_root / "output" / args.study / "llm_source" / "source_truth"
    forms = args.forms or discover_pdf_backed_forms(study_dir, args.study)

    failures: list[tuple[str, str]] = []
    generated: list[Path] = []
    for form in forms:
        print(f"FORM {form}", flush=True)
        try:
            generated.append(generate_form(repo_root, args.study, form, out_dir))
        except Exception as exc:
            failures.append((form, str(exc)))
            print(f"  FAIL {exc}", flush=True)
        else:
            print("  OK", flush=True)

    print(f"SUMMARY generated={len(generated)} failed={len(failures)} out={out_dir}")
    if failures:
        for form, message in failures:
            print(f"\n[{form}]\n{message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
