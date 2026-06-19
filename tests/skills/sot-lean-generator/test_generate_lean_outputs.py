"""Tests for the repo-level lean SoT generation wrapper."""

# ruff: noqa: S108

from __future__ import annotations

import json
from pathlib import Path

from scripts.source_truth import generate_lean_outputs
from scripts.source_truth.generate_lean_outputs import (
    discover_pdf_backed_forms_with_reviews,
    generate_form,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_discovers_pdf_backed_forms_with_indo_vap_duplicate_overrides(tmp_path: Path) -> None:
    study_dir = tmp_path / "Indo-VAP"
    _touch(study_dir / "annotated_pdfs" / "14 Case Control v1.0.pdf")
    _touch(study_dir / "datasets" / "14_CaseControl.xlsx")
    _touch(study_dir / "datasets" / "14_Case_Control.xlsx")

    forms, reviews = discover_pdf_backed_forms_with_reviews(tmp_path, study_dir, "Indo-VAP")
    assert forms == ["14_CaseControl"]
    assert reviews == []


def test_ambiguous_pdf_code_without_override_is_reported(tmp_path: Path) -> None:
    study_dir = tmp_path / "Other"
    _touch(study_dir / "annotated_pdfs" / "1 Screening v1.0.pdf")
    _touch(study_dir / "datasets" / "1_A.xlsx")
    _touch(study_dir / "datasets" / "1_B.xlsx")

    forms, reviews = discover_pdf_backed_forms_with_reviews(tmp_path, study_dir, "Other")
    assert forms == []
    assert len(reviews) == 1
    assert reviews[0].exists()
    assert "ambiguous_dataset" in reviews[0].read_text(encoding="utf-8")


def test_generate_form_rejects_novel_anchored_candidate_and_promotes_gold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Anchored forms must reject novel candidates and preserve verified gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "6_HIV"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "6 HIV v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")
    _touch(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml")

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        run_calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    diff_calls: list[list[str]] = []

    def fake_run_result(
        cmd: list[str], *, cwd: Path
    ) -> generate_lean_outputs.subprocess.CompletedProcess[str]:
        diff_calls.append(cmd)
        return generate_lean_outputs.subprocess.CompletedProcess(cmd, 1, "novel=1", "")

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return (
            Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"
        )

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_run_result", fake_run_result)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    generate_form(repo_root, study, form, out_dir)

    assert any("diff_against_gold.py" in " ".join(cmd) for cmd in diff_calls)
    assert any(
        str(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml") in cmd
        for cmd in run_calls
    )
    assert (
        published[0]["verified_policy"]
        == repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml"
    )
    assert published[0]["out_root"] == out_dir


def test_generate_form_skips_gold_diff_when_no_gold_exists(monkeypatch, tmp_path: Path) -> None:
    """Unanchored forms keep verifier-only promotion until they have gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "7_Culture"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "7 Culture v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return (
            Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"
        )

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    generate_form(repo_root, study, form, out_dir)

    assert not any("diff_against_gold.py" in " ".join(cmd) for cmd in calls)
    assert published[0]["verified_policy"] == Path(f"/tmp/{form}_lean.yaml")
    assert published[0]["out_root"] == out_dir


def test_publish_verified_sot_outputs_writes_policy_schema_and_joined_view(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "6_HIV"
    dataset = repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx"
    policy = tmp_path / f"{form}_policy.yaml"
    source_pack = tmp_path / f"sot_source_pack_{form}.json"
    out_root = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(dataset)
    policy.write_text(
        """
study: Test-Study
form:
  number: "6"
  title: HIV
variables:
  HIV_CD4DAT:
    section: main
    pdf_question: CD4 Test Date
    type: date
    phi: jitter_date
""".lstrip(),
        encoding="utf-8",
    )
    source_pack.write_text(json.dumps({"headers": ["HIV_CD4DAT"]}), encoding="utf-8")

    result = generate_lean_outputs._publish_verified_sot_outputs(
        repo_root=repo_root,
        study=study,
        form=form,
        dataset=dataset,
        source_pack=source_pack,
        verified_policy=policy,
        out_root=out_root,
    )

    pair_dir = out_root / form
    # N2/N3/N17: the joined view is the SOLE LLM-facing SoT file; it is the return.
    joined = pair_dir / "joined" / f"{form}_joined_query_view.yaml"
    assert result == joined
    assert joined.is_file()
    # Construction material (policy YAML + dataset schema) is fenced into the AUDIT
    # zone, NOT published into llm_source.
    construction = out_root.parents[1] / "audit" / "SoT_construction" / form
    assert (construction / "pdf" / f"{form}_policy.yaml").is_file()
    assert (construction / "dataset" / f"{form}_schema.json").is_file()
    # llm_source/SoT/<pair>/ holds ONLY joined/ — no pdf/ or dataset/ subdirs.
    assert not (pair_dir / "pdf").exists()
    assert not (pair_dir / "dataset").exists()
    joined_text = joined.read_text(encoding="utf-8")
    assert "CD4 Test Date" in joined_text
    assert "source_order" not in joined_text


def test_generate_form_routes_missing_pdf_to_sot_review(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "8_MissingPdf"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    result = generate_form(repo_root, study, form, out_dir)

    expected = (
        repo_root
        / "output"
        / study
        / "audit"
        / "human_review"
        / form
        / "review_report.md"
    )
    assert result == expected
    assert expected.is_file()
    text = expected.read_text(encoding="utf-8")
    assert "missing_pdf" in text
    assert (
        "no Source Truth policy, dataset schema, joined view, or source pack was generated" in text
    )


def test_batch_main_routes_ambiguous_discovery_to_sot_review(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Other"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "1 Screening v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / "1_A.xlsx")
    _touch(repo_root / "data" / "raw" / study / "datasets" / "1_B.xlsx")

    rc = generate_lean_outputs.main(["--study", study, "--repo-root", str(repo_root)])

    report = (
        repo_root
        / "output"
        / study
        / "audit"
        / "human_review"
        / "1"
        / "review_report.md"
    )
    assert rc == 0
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "ambiguous_dataset" in text
    assert "1_A.xlsx" in text
    assert "1_B.xlsx" in text


def test_discrepancy_review_reason_detects_binding_conflict(tmp_path: Path) -> None:
    """_discrepancy_review_reason detects binding_conflict discrepancy."""
    policy_path = tmp_path / "policy_conflict.yaml"
    policy_path.write_text(
        """
study: Test-Study
form:
  number: "9"
  title: DupHeaders
discrepancies:
  - kind: dataset_duplicate_header_binding_conflict
    note: human reviewed this one
variables:
  A: {}
""".lstrip(),
        encoding="utf-8",
    )

    result = generate_lean_outputs._discrepancy_review_reason(policy_path)
    assert result == "dataset_duplicate_header_binding_conflict"

    policy_path_combined = tmp_path / "policy_combined.yaml"
    policy_path_combined.write_text(
        """
study: Test-Study
form:
  number: "9"
  title: DupHeaders
discrepancies:
  - kind: dataset_duplicate_header_combined_binding
    note: human combined them
variables:
  A: {}
""".lstrip(),
        encoding="utf-8",
    )

    result = generate_lean_outputs._discrepancy_review_reason(policy_path_combined)
    assert result is None, "Should return None for combined_binding discrepancy"

    policy_path_none = tmp_path / "policy_none.yaml"
    policy_path_none.write_text(
        """
study: Test-Study
form:
  number: "9"
  title: DupHeaders
variables:
  A: {}
""".lstrip(),
        encoding="utf-8",
    )

    result = generate_lean_outputs._discrepancy_review_reason(policy_path_none)
    assert result is None, "Should return None when no discrepancies exist"

    policy_path_malformed = tmp_path / "policy_malformed.yaml"
    policy_path_malformed.write_text("{ invalid: yaml content [", encoding="utf-8")

    result = generate_lean_outputs._discrepancy_review_reason(policy_path_malformed)
    assert result is None, "Should return None for malformed YAML"

    nonexistent = tmp_path / "nonexistent.yaml"
    result = generate_lean_outputs._discrepancy_review_reason(nonexistent)
    assert result is None, "Should return None for nonexistent file"


def test_discrepancy_review_reason_holds_pdf_missing_and_field_count(tmp_path: Path) -> None:
    for kind in (
        "printed_widget_without_dataset_header",
        "pdf_field_count_column_count_mismatch",
    ):
        policy_path = tmp_path / f"policy_{kind}.yaml"
        policy_path.write_text(
            f"""
study: Test-Study
discrepancies:
  - kind: {kind}
variables:
  A: {{}}
""".lstrip(),
            encoding="utf-8",
        )
        assert generate_lean_outputs._discrepancy_review_reason(policy_path) == kind


def test_discrepancy_review_reason_holds_alias_when_names_differ(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_alias.yaml"
    policy_path.write_text(
        """
study: Test-Study
discrepancies:
  - kind: pdf_annotation_alias_to_dataset_header
    pdf_annotation_says:
      - label: PDF_LABEL
        dataset_column: DATA_COL
variables:
  DATA_COL: {}
""".lstrip(),
        encoding="utf-8",
    )
    assert (
        generate_lean_outputs._discrepancy_review_reason(policy_path)
        == "pdf_annotation_alias_to_dataset_header"
    )

    policy_path_case_only = tmp_path / "policy_alias_case.yaml"
    policy_path_case_only.write_text(
        """
study: Test-Study
discrepancies:
  - kind: pdf_annotation_alias_to_dataset_header
    pdf_annotation_says:
      - label: hiv_cd4
        dataset_column: HIV_CD4
variables:
  HIV_CD4: {}
""".lstrip(),
        encoding="utf-8",
    )
    assert generate_lean_outputs._discrepancy_review_reason(policy_path_case_only) is None

    policy_path_curated = tmp_path / "policy_alias_curated.yaml"
    policy_path_curated.write_text(
        """
study: Test-Study
discrepancies:
  - kind: pdf_annotation_alias_to_dataset_header
    pdf_annotation_says:
      - label: FC_PARAS3_4
        dataset_column: FC_PARAS4_4
        curated: true
variables:
  FC_PARAS4_4: {}
""".lstrip(),
        encoding="utf-8",
    )
    assert generate_lean_outputs._discrepancy_review_reason(policy_path_curated) is None


def test_duplicate_binding_review_reason_detects_conflict(tmp_path: Path) -> None:
    """Backward-compat alias: test renamed to _discrepancy_review_reason."""
    test_discrepancy_review_reason_detects_binding_conflict(tmp_path)


def test_generate_form_holds_duplicate_binding_conflict_for_review(
    monkeypatch, tmp_path: Path
) -> None:
    """Test that generate_form routes binding_conflict candidates to SoT review.

    When the verified candidate carries a binding_conflict discrepancy,
    the form should be HELD for review (returned path is review_report.md)
    and publishing should NOT be called.
    """
    repo_root = tmp_path
    study = "Test-Study"
    form = "9_DupHeaders"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "9 DupHeaders v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        run_calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            # Write candidate with binding_conflict discrepancy
            Path(f"/tmp/{form}_lean.yaml").write_text(
                """
study: Test-Study
form:
  number: "9"
  title: DupHeaders
discrepancies:
  - kind: dataset_duplicate_header_binding_conflict
    note: duplicate headers need human review
variables:
  A: {}
  B: {}
sections:
  main: {}
""".lstrip(),
                encoding="utf-8",
            )

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return (
            Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"
        )

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    result = generate_form(repo_root, study, form, out_dir)

    # Assert form is routed to SoT review (not published)
    expected_review_path = (
        repo_root
        / "output"
        / study
        / "audit"
        / "human_review"
        / form
        / "review_report.md"
    )
    assert result == expected_review_path, (
        f"Expected review path {expected_review_path}, got {result}"
    )
    assert expected_review_path.is_file(), f"Review report must exist at {expected_review_path}"

    review_text = expected_review_path.read_text(encoding="utf-8")
    assert "dataset_duplicate_header_binding_conflict" in review_text, (
        f"Review report must mention binding_conflict, got: {review_text}"
    )

    # Assert publishing was NOT called
    assert len(published) == 0, (
        f"Publishing must not be called for binding_conflict, but got: {published}"
    )


def test_generate_form_publishes_field_count_mismatch_with_review(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "9_FieldCount"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "9 FieldCount v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text(
                "study: Test-Study\nform: {number: '9'}\n"
                "discrepancies:\n  - kind: pdf_field_count_column_count_mismatch\n"
                "variables: {A: {}}\nsections: {main: {}}\n",
                encoding="utf-8",
            )

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    result = generate_form(repo_root, study, form, out_dir)

    assert "human_review" in result.parts
    assert form in result.parts  # Note 22: form-first review dir
    assert len(published) == 1
