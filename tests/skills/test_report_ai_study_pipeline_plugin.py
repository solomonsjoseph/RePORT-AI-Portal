"""Static checks for the portable RePORT-AI study pipeline plugin.

Wave 6 re-architected the plugin so the orchestrator skill IS the pipeline:
``plugin.yaml`` now declares a 10-phase ``orchestrator`` topology plus a flat
``skills`` inventory (the old linear ``workflow`` / ``execution_model`` schema is
gone). These checks assert the new manifest contract and the stable
documentation invariants (PHI row-value boundary, the ``make study`` entry
point, held-set statuses) without pinning brittle exact prose.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "report-ai-study-pipeline"

# Every skill directory that must ship a platform-neutral SKILL.md entrypoint.
_ALL_SKILL_DIRS = [
    "report-ai-study-pipeline",
    "header-extraction",
    "dictionary-to-llm-source",
    "dataset-deduplication",
    "sot-lean-generator",
    "phi-classification",
    "phi-scrubbing",
    "dataset-to-llm-source",
    "audit-verification",
    "excel-duplicate-handler",
    "phi-rulebook",
    "study-setup",
]


def _manifest() -> dict:
    return yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def test_plugin_has_platform_neutral_manifest() -> None:
    manifest = _manifest()

    assert manifest["name"] == "report-ai-study-pipeline"
    assert manifest["kind"] == "llm-plugin-pack"
    assert manifest["platforms"]["primary"] == "generic-llm"
    assert "codex" in manifest["platforms"]["adapters"]
    assert manifest["adapters"]["generic_llm"]["agent_metadata"] == "agents/llm.yaml"
    assert manifest["adapters"]["codex"]["agent_metadata"] == "agents/llm.yaml"

    # The orchestrator skill is the single entry point, launched via `make study`.
    entry = manifest["entrypoint"]
    assert entry["skill"] == "report-ai-study-pipeline"
    assert entry["command"] == "make study STUDY=<name>"


def test_plugin_declares_ten_phase_orchestrator() -> None:
    manifest = _manifest()
    orch = manifest["orchestrator"]
    assert orch["skill"] == "report-ai-study-pipeline"
    assert orch["lock"] == "per_study_exclusive_whole_run"

    phases = orch["phases"]
    # Conceptual phases 0..10 (with 2b headers + 3b cross-form barrier) → 13 entries.
    numbers = [p["phase"] for p in phases]
    assert numbers[0] == 0 and numbers[-1] == 10
    assert "2b" in [str(n) for n in numbers]
    assert "3b" in [str(n) for n in numbers]

    # The contiguous publish phases are executed by the dataset-to-llm-source
    # supervisor; the verifier runs in phases 5 and 9.
    by_num = {str(p["phase"]): p for p in phases}
    assert "dataset-to-llm-source" in by_num["6"]["skills"]
    assert "audit-verification" in by_num["9"]["skills"]
    assert by_num["1"]["skills"] == ["dictionary-to-llm-source"]
    assert by_num["2"]["skills"] == ["dataset-deduplication"]
    assert by_num["2b"]["skills"] == ["header-extraction"]


def test_plugin_skills_inventory_is_complete_and_well_formed() -> None:
    manifest = _manifest()
    declared = {s["skill"] for s in manifest["skills"]}
    # Every DAG/preflight/shared/interactive skill except the orchestrator itself
    # is listed in the skills inventory.
    expected = set(_ALL_SKILL_DIRS) - {"report-ai-study-pipeline"}
    assert declared == expected

    for skill in manifest["skills"]:
        assert {"skill", "path", "role", "scope", "parallel", "purpose"} <= set(skill)
        assert skill["role"] in {"dag", "preflight", "legacy_preflight", "shared_module", "interactive"}


def test_plugin_defines_raw_file_set_contract() -> None:
    manifest = _manifest()

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
    # Manifest + privacy config now resolve through config/<STUDY>/.
    assert any("config/<STUDY>/_forms_manifest.yaml" in i for i in raw_file_set["inputs"])


def test_host_repo_contract_expected_outputs_include_joined_view() -> None:
    manifest = _manifest()
    outputs = manifest["host_repo_contract"]["expected_outputs"]
    assert any("joined" in o and "joined_query_view" in o for o in outputs)
    assert not any("/pdf/" in o and "_policy.yaml" in o for o in outputs)


def test_host_repo_contract_points_at_new_entrypoints() -> None:
    manifest = _manifest()
    required = manifest["host_repo_contract"]["required_paths"]
    assert "scripts/pipeline/host_pipeline.py" in required
    assert any("report-ai-study-pipeline/scripts/run.py" in p for p in required)
    assert "config/_defaults/phi_scrub.yaml" in required


def test_plugin_bundles_entrypoint_and_all_child_skills() -> None:
    for skill_dir in _ALL_SKILL_DIRS:
        assert (PLUGIN_ROOT / "skills" / skill_dir / "SKILL.md").is_file(), (
            f"missing SKILL.md for {skill_dir}"
        )

    orchestrator = (PLUGIN_ROOT / "skills" / "report-ai-study-pipeline" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    # Stable invariants of the new orchestrator entrypoint doc.
    assert "make study STUDY=" in orchestrator
    assert "Do not read raw dataset row values into the agent context." in orchestrator
    assert "$dataset-to-llm-source" in orchestrator
    assert "10-phase" in orchestrator or "10 phase" in orchestrator


def test_bundled_agent_metadata_uses_platform_neutral_filename() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "`agents/llm.yaml`" in readme
    assert "vendor-specific names such as `openai.yaml`" in readme

    # Skills that ship adapter metadata must use the neutral filename.
    for agent_dir in PLUGIN_ROOT.glob("skills/*/agents"):
        if (agent_dir / "llm.yaml").exists():
            assert not (agent_dir / "openai.yaml").exists()


def test_plugin_readme_documents_orchestrator_and_modes() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "make study STUDY=" in readme
    # PHI boundary + held-set statuses must be documented for operators.
    assert "row values" in readme
    assert "held_duplicate_review" in readme
    assert "held_sot_review" in readme
    assert "held_publish_review" in readme


def test_codex_adapter_points_to_bundled_skills_without_being_primary_manifest() -> None:
    codex_manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert codex_manifest["name"] == "report-ai-study-pipeline"
    assert codex_manifest["skills"] == "./skills/"
    assert "Codex" not in codex_manifest["description"]
    assert (PLUGIN_ROOT / "plugin.yaml").is_file()
