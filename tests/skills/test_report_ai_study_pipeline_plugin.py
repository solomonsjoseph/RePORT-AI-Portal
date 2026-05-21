"""Static checks for the portable RePORT-AI study pipeline plugin."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "report-ai-study-pipeline"


def test_plugin_has_platform_neutral_manifest_and_ordered_workflow() -> None:
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["name"] == "report-ai-study-pipeline"
    assert manifest["kind"] == "llm-plugin-pack"
    assert manifest["platforms"]["primary"] == "generic-llm"
    assert "codex" in manifest["platforms"]["adapters"]
    assert manifest["adapters"]["generic_llm"]["agent_metadata"] == "agents/llm.yaml"
    assert manifest["adapters"]["codex"]["agent_metadata"] == "agents/llm.yaml"

    workflow = manifest["workflow"]
    assert [step["skill"] for step in workflow] == [
        "excel-duplicate-handler",
        "sot-lean-generator",
        "dataset-to-llm-source",
    ]
    assert [step["order"] for step in workflow] == [1, 2, 3]
    assert [step["scope"] for step in workflow] == [
        "study",
        "raw_file_set",
        "raw_file_set_or_lock_aware_study_run",
    ]
    assert [step["execution"] for step in workflow] == [
        "once_per_study",
        "single_or_parallel_per_set",
        "single_or_controlled_parallel_per_set",
    ]


def test_plugin_defines_raw_file_set_and_parallel_execution_contract() -> None:
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    raw_file_set = manifest["raw_file_set"]
    assert raw_file_set["identity"] == ["study", "form_id"]
    assert raw_file_set["statuses"] == [
        "ready",
        "held_duplicate_review",
        "held_sot_review",
        "held_publish_review",
        "complete",
    ]
    assert "matching printed PDF" in raw_file_set["definition"]

    execution_model = manifest["execution_model"]
    assert sorted(execution_model["modes"]) == ["batch_parallel", "single_set"]

    duplicate_preflight = execution_model["duplicate_preflight"]
    assert duplicate_preflight["scope"] == "study"
    assert duplicate_preflight["run_count"] == "once_per_study"
    assert duplicate_preflight["parallel_allowed"] is False
    assert duplicate_preflight["must_complete_before"] == ["sot", "dataset_publish"]

    sot = execution_model["sot"]
    assert sot["scope"] == "raw_file_set"
    assert sot["parallel_allowed"] is True
    assert "Do not read dataset row 2+ values." in sot["safety_rules"]

    dataset_publish = execution_model["dataset_publish"]
    assert dataset_publish["parallel_allowed"] == "lock_aware_only"
    assert "Use the host repo's lock-aware extraction/publish CLI." in dataset_publish[
        "safety_rules"
    ]
    assert "Never force concurrent writes into the same study output directory." in dataset_publish[
        "safety_rules"
    ]


def test_plugin_bundles_entrypoint_and_child_skills() -> None:
    expected_skill_dirs = [
        "report-ai-study-pipeline",
        "excel-duplicate-handler",
        "sot-lean-generator",
        "dataset-to-llm-source",
    ]

    for skill_dir in expected_skill_dirs:
        assert (PLUGIN_ROOT / "skills" / skill_dir / "SKILL.md").is_file()

    orchestrator = (
        PLUGIN_ROOT / "skills" / "report-ai-study-pipeline" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "1. `$excel-duplicate-handler`" in orchestrator
    assert "2. `$sot-lean-generator`" in orchestrator
    assert "3. `$dataset-to-llm-source`" in orchestrator
    assert "This plugin is not Codex-only." in orchestrator
    assert "Do not read raw dataset row values into the agent context." in orchestrator
    assert "This phase runs once for the study" in orchestrator
    assert "Source Truth may run in parallel across independent ready sets." in orchestrator
    assert "controlled parallel wrapper that respects the host repo's pipeline locks" in orchestrator
    assert "Never\nforce concurrent writes into the same study output tree." in orchestrator


def test_bundled_agent_metadata_uses_platform_neutral_filename() -> None:
    copied_skills = [
        "excel-duplicate-handler",
        "sot-lean-generator",
        "dataset-to-llm-source",
    ]

    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "`agents/llm.yaml`" in readme
    assert "vendor-specific names such as `openai.yaml`" in readme

    for skill_name in copied_skills:
        agent_dir = PLUGIN_ROOT / "skills" / skill_name / "agents"
        assert (agent_dir / "llm.yaml").is_file()
        assert not (agent_dir / "openai.yaml").exists()


def test_plugin_readme_documents_single_and_batch_modes() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Duplicate handling is a **single study-level preflight execution**." in readme
    assert "A raw-file set is one canonical form/work unit after duplicate preflight." in readme
    assert "**Single-set mode:**" in readme
    assert "**Batch-parallel mode:**" in readme
    assert "Dataset publishing may be parallel only through the host repo's lock-aware" in readme
    assert "held_duplicate_review" in readme
    assert "held_sot_review" in readme
    assert "held_publish_review" in readme


def test_bundled_child_skills_match_repo_level_skills() -> None:
    copied_skills = [
        "excel-duplicate-handler",
        "sot-lean-generator",
        "dataset-to-llm-source",
    ]

    for skill_name in copied_skills:
        repo_skill_dir = REPO_ROOT / "skills" / skill_name
        plugin_skill_dir = PLUGIN_ROOT / "skills" / skill_name
        repo_files = sorted(
            path.relative_to(repo_skill_dir)
            for path in repo_skill_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        plugin_files = sorted(
            path.relative_to(plugin_skill_dir)
            for path in plugin_skill_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        expected_plugin_files = sorted(
            Path("agents/llm.yaml") if path == Path("agents/openai.yaml") else path
            for path in repo_files
        )
        assert plugin_files == expected_plugin_files
        for relative_path in repo_files:
            plugin_relative_path = (
                Path("agents/llm.yaml")
                if relative_path == Path("agents/openai.yaml")
                else relative_path
            )
            assert (plugin_skill_dir / plugin_relative_path).read_bytes() == (
                repo_skill_dir / relative_path
            ).read_bytes()


def test_codex_adapter_points_to_bundled_skills_without_being_primary_manifest() -> None:
    codex_manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert codex_manifest["name"] == "report-ai-study-pipeline"
    assert codex_manifest["skills"] == "./skills/"
    assert "Codex" not in codex_manifest["description"]
    assert (PLUGIN_ROOT / "plugin.yaml").is_file()
