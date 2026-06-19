"""Static checks for the excel-duplicate-handler agent skill."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[2]
SKILL_DIR = (
    REPO_ROOT / "plugins" / "report-ai-study-pipeline" / "skills" / "excel-duplicate-handler"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
LLM_YAML = SKILL_DIR / "agents" / "llm.yaml"


def _frontmatter_and_body() -> tuple[dict[str, str], str]:
    raw = SKILL_MD.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    _, frontmatter, body = raw.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_excel_duplicate_skill_metadata_triggers_for_duplicate_work() -> None:
    frontmatter, body = _frontmatter_and_body()

    assert frontmatter["name"] == "excel-duplicate-handler"
    description = frontmatter["description"]
    assert "LEGACY" in description
    assert "dataset-deduplication" in description
    assert "make study" in description
    assert "PHI" in body
    assert "Core Rule" in body


def test_excel_duplicate_skill_preserves_project_privacy_boundary() -> None:
    _frontmatter, body = _frontmatter_and_body()

    required_phrases = [
        "Do not read raw row values into the agent context",
        "Row-1 headers are allowed as binding input.",
        "Do not read row 2+ values.",
        "Normalize filenames conservatively:",
        "exact_ordered_headers",
        "same_header_set_different_order",
        "header_superset",
        "same_name_different_headers",
        "mixed_duplicate_signals",
        "Do not normalize away information that could distinguish forms.",
        "The superset may be a newer revision, an expanded form, or a different extract.",
        "skills/excel-duplicate-handler/scripts/merge_excel_duplicates.py",
        "produce an actual merged workbook unless the candidate set is unsafe to merge",
        "data/raw/<study>/_dataset/",
        "data/raw/<study>/datasets/<dataset>.xlsx",
        "output/<study>/audit/datasets/<dataset>/merge_report.md",
        "output/<study>/audit/datasets/<dataset>/merge_provenance.csv",
        "output/<study>/audit/dataset_duplicate_merge_report.md",
        "output/<study>/audit/human_review/excel/<candidate_group>/duplicate_review_report.md",
        "tmp/excel_duplicate_handler_test/project/data/raw/<study>/_dataset/",
        "tmp/excel_duplicate_handler_test/project/data/raw/<study>/datasets/<dataset>.xlsx",
        "tmp/excel_duplicate_handler_test/project/output/<study>/audit/human_review/excel/<candidate_group>/duplicate_review_report.md",
        "--dataset-dir data/raw/<study>/datasets",
        "--artifact-root tmp/excel_duplicate_handler_test/project",
        "It may copy row values internally only to create the merged workbook.",
        "Reports and provenance must live under the audit folder",
        "Unsafe or ambiguous candidates must write a count/header-only report",
        "The full original raw `datasets/` folder must be copied",
        "Human-review cases must not create a raw dataset replacement",
        "Never overwrite an existing `_dataset/` snapshot",
        "must not be written to `llm_source/`",
        "Reports must be count/header/provenance-only.",
        "Invalid Excel lock/temp artifacts such as `~$*.xlsx` are skipped",
        "Invalid non-lock branch workbooks must route to `audit/human_review/`",
        "Directory-level lock/temp processing must pair only `~$<dataset>.xlsx` with `<dataset>.xlsx`.",
        "The merged workbook must preserve the main workbook as the base file",
        "After a safe merge, remove the active branch duplicate file from `datasets/`",
        "Remove invalid active Excel lock/temp branch artifacts",
        "Do not put audit sheets in the merged workbook.",
        "Validate header safety before creating any backup or output workbook.",
        "Build the merge in a temporary workbook first",
        "Valid branch rows must be appended after the existing main rows.",
        "Treat every other file as a **branch** whose information must be retained",
        "the original or manifest-declared canonical file",
        "maximum trusted entry/record count",
        "Never overwrite a non-empty main value with a branch value.",
        "Branch rows not already present in main must be appended to main.",
        "Branch-only columns must be added to the merged schema, not discarded.",
        "preserve both versions or quarantine the conflict",
        "provenance to reconstruct which source file, sheet, and row contributed",
        "clean_duplicate_columns",
        "dataset_duplicate_header_combined_binding",
        "scripts/skills/extract_to_llm_source.py status",
        "tests/test_dedup.py",
        "Use `$dataset-to-llm-source`",
        "Use `$sot-lean-generator`",
    ]
    for phrase in required_phrases:
        assert phrase in body

    forbidden_phrases = [
        "print sample rows",
        "paste sample values",
        "read all workbook values",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in body


def test_excel_duplicate_skill_llm_metadata_matches_skill_name() -> None:
    payload = yaml.safe_load(LLM_YAML.read_text(encoding="utf-8"))
    interface = payload["interface"]

    assert interface["display_name"] == "Excel Duplicate Handler"
    assert "duplicate file" in interface["short_description"]
    assert "$excel-duplicate-handler" in interface["default_prompt"]
    assert "header-only comparison" in interface["default_prompt"]
