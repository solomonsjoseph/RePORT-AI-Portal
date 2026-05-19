"""Static checks for the dataset-to-llm-source agent skill."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "dataset-to-llm-source"
SKILL_MD = SKILL_DIR / "SKILL.md"
OPENAI_YAML = SKILL_DIR / "agents" / "openai.yaml"


def _frontmatter_and_body() -> tuple[dict[str, str], str]:
    raw = SKILL_MD.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    _, frontmatter, body = raw.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_dataset_skill_metadata_triggers_for_dataset_operations() -> None:
    frontmatter, _body = _frontmatter_and_body()

    assert frontmatter["name"] == "dataset-to-llm-source"
    description = frontmatter["description"]
    assert "dataset skill" in description
    assert "PHI-safe" in description
    assert "extract_to_llm_source" in description
    assert "one-form dataset pilots" in description


def test_dataset_skill_preserves_phi_boundary_and_cli_contract() -> None:
    _frontmatter, body = _frontmatter_and_body()

    required_phrases = [
        "Do not read raw or staged dataset values into the agent context.",
        "scripts/skills/extract_to_llm_source.py status",
        "scripts/skills/extract_to_llm_source.py run",
        "scripts/skills/extract_to_llm_source.py verify",
        "--form 6_HIV",
        "REPORTALIN_ALLOW_DISABLED_SCRUB",
        "destruction_attestation.json",
        "verifier_report.json",
    ]
    for phrase in required_phrases:
        assert phrase in body


def test_dataset_skill_openai_metadata_matches_skill_name() -> None:
    payload = yaml.safe_load(OPENAI_YAML.read_text(encoding="utf-8"))
    interface = payload["interface"]

    assert interface["display_name"] == "Dataset to LLM Source"
    assert "PHI-safe" in interface["short_description"]
    assert "$dataset-to-llm-source" in interface["default_prompt"]
