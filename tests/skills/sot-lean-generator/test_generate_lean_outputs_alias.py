"""N3 strict: alias discrepancies with differing labels block joined-view publish."""

from __future__ import annotations

from pathlib import Path

from scripts.source_truth import generate_lean_outputs
from scripts.source_truth.generate_lean_outputs import generate_form


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_generate_form_holds_alias_discrepancy_for_review(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "11_Alias"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "11 Alias v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text(
                "study: Test-Study\nform: {number: '11'}\n"
                "discrepancies:\n"
                "  - kind: pdf_annotation_alias_to_dataset_header\n"
                "    pdf_annotation_says:\n"
                "      - label: PDF_LABEL\n"
                "        dataset_column: DATA_COL\n"
                "variables: {DATA_COL: {}}\nsections: {main: {}}\n",
                encoding="utf-8",
            )

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return Path(kwargs["out_root"]) / str(kwargs["form"]) / "joined" / f"{kwargs['form']}.yaml"

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    result = generate_form(repo_root, study, form, out_dir)

    assert "human_review" in result.parts
    assert form in result.parts
    assert len(published) == 0
