"""GR-1 lint: every plugin SKILL.md must state the global no-row-values rule.

The spec's global rule (GR-1) requires *every* skill to explicitly state the
prohibition as a hard rule. This test is the positive presence check that guards
against drift — a new or edited skill that drops the statement fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins/report-ai-study-pipeline/skills"
# The canonical, greppable GR-1 token every SKILL.md must contain.
_GR1_TOKEN = "read dataset row values"

_SKILL_FILES = sorted(_SKILLS_DIR.glob("*/SKILL.md"))


def test_there_are_skill_files():
    assert _SKILL_FILES, f"no SKILL.md files found under {_SKILLS_DIR}"


@pytest.mark.parametrize("skill_md", _SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_states_gr1(skill_md: Path):
    text = skill_md.read_text(encoding="utf-8")
    assert _GR1_TOKEN in text, (
        f"{skill_md.parent.name}/SKILL.md is missing the GR-1 global-rule statement "
        f"(must contain {_GR1_TOKEN!r})"
    )
