"""Conformance test: every bundled ``SKILL.md`` follows the 8-section template.

The canonical 8-section template is exemplified by the two reference skills
``dataset-deduplication`` and ``dictionary-to-llm-source``. Each conforming
``SKILL.md`` carries:

1. an ``# <Title>`` H1 (the title block), and the seven required H2 sections
2. ``## Core Rule``
3. ``## What This Skill Does``
4. ``## CLI``
5. ``## Result Contract``
6. ``## Portability``
7. ``## Exit Codes``
8. ``## What This Skill Does NOT Do``

H2 headings are matched by *prefix* so a section may carry a parenthetical
qualifier (e.g. ``## Core Rule (GR-1 + Note 4)``) and still conform.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "plugins" / "report-ai-study-pipeline" / "skills"

# The seven required H2 sections (matched by prefix to tolerate parentheticals).
REQUIRED_H2_SECTIONS: tuple[str, ...] = (
    "Core Rule",
    "What This Skill Does",
    "CLI",
    "Result Contract",
    "Portability",
    "Exit Codes",
    "What This Skill Does NOT Do",
)

SKILL_MD_PATHS: list[Path] = sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _skill_id(path: Path) -> str:
    return path.parent.name


def _strip_fenced_code(text: str) -> str:
    """Drop fenced code blocks so ``#``-prefixed shell comments inside ```` ``` ````
    fences are not mistaken for Markdown headings."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _h1_titles(text: str) -> list[str]:
    body = _strip_fenced_code(text)
    return [m.group(1).strip() for m in re.finditer(r"^#\s+(.+?)\s*$", body, re.MULTILINE)]


def _h2_titles(text: str) -> list[str]:
    body = _strip_fenced_code(text)
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE)]


def test_at_least_one_skill_discovered() -> None:
    """Guard: the glob must actually find the bundled skills."""
    assert SKILL_MD_PATHS, f"no SKILL.md found under {SKILLS_DIR}"


@pytest.mark.parametrize("skill_md", SKILL_MD_PATHS, ids=_skill_id)
def test_skill_md_has_title_block(skill_md: Path) -> None:
    """Every SKILL.md carries exactly one H1 title block."""
    text = skill_md.read_text(encoding="utf-8")
    titles = _h1_titles(text)
    assert len(titles) == 1, (
        f"{_skill_id(skill_md)}: expected exactly one '# <Title>' H1, found {len(titles)}: {titles}"
    )


@pytest.mark.parametrize("skill_md", SKILL_MD_PATHS, ids=_skill_id)
def test_skill_md_has_required_sections(skill_md: Path) -> None:
    """Every SKILL.md carries all seven required H2 sections (prefix match)."""
    text = skill_md.read_text(encoding="utf-8")
    h2 = _h2_titles(text)
    missing = [
        section
        for section in REQUIRED_H2_SECTIONS
        if not any(heading == section or heading.startswith(f"{section} ") for heading in h2)
    ]
    assert not missing, (
        f"{_skill_id(skill_md)}: missing required H2 section(s) {missing}. "
        f"Present H2 headings: {h2}"
    )
