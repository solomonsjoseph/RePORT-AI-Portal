"""Tests for :func:`preferred_or_installed_downgrade` — graceful qwen3 fallback.

When the operator configures a qwen3 tag they cannot actually serve
(common when `qwen3:8b` is configured but only `qwen3:1.7b` is pulled
or fits in free RAM), the helper must walk the downgrade ladder and
return the largest locally-installed tag instead of returning the
preferred-but-missing tag.
"""

from __future__ import annotations

import pytest

from scripts.ai_assistant.ui.providers import (
    QWEN3_DOWNGRADE_LADDER,
    preferred_or_installed_downgrade,
)


class TestPreferredOrInstalledDowngrade:
    def test_returns_preferred_when_installed(self) -> None:
        got = preferred_or_installed_downgrade("qwen3:8b", ["qwen3:8b", "qwen3:4b", "qwen3:1.7b"])
        assert got == "qwen3:8b"

    def test_latest_tag_matches_bare_tag(self) -> None:
        got = preferred_or_installed_downgrade("qwen3:8b", ["qwen3:8b:latest"])
        assert got == "qwen3:8b"

    def test_downgrades_from_8b_to_4b_when_only_4b_installed(self) -> None:
        got = preferred_or_installed_downgrade("qwen3:8b", ["qwen3:4b", "qwen3:1.7b"])
        assert got == "qwen3:4b"

    def test_downgrades_from_8b_to_1p7b_when_nothing_else_installed(self) -> None:
        got = preferred_or_installed_downgrade("qwen3:8b", ["qwen3:1.7b"])
        assert got == "qwen3:1.7b"

    def test_upgrades_from_1p7b_to_4b_when_larger_installed(self) -> None:
        # When preferred is SMALLEST and ladder has larger installed tags,
        # the ladder wraps-around to offer the next-best option.
        got = preferred_or_installed_downgrade("qwen3:1.7b", ["qwen3:4b"])
        assert got == "qwen3:4b"

    def test_returns_none_when_no_qwen3_installed(self) -> None:
        got = preferred_or_installed_downgrade("qwen3:8b", ["mistral:latest", "gemma3:9b"])
        assert got is None

    def test_returns_none_for_non_qwen3_preferred(self) -> None:
        got = preferred_or_installed_downgrade("mistral:latest", ["qwen3:8b", "qwen3:1.7b"])
        assert got is None

    def test_empty_inputs(self) -> None:
        assert preferred_or_installed_downgrade("", ["qwen3:8b"]) is None
        assert preferred_or_installed_downgrade("qwen3:8b", []) is None

    def test_ladder_is_ordered_largest_to_smallest(self) -> None:
        # Documents the contract: left = largest memory footprint.
        assert QWEN3_DOWNGRADE_LADDER.index("qwen3:32b") < QWEN3_DOWNGRADE_LADDER.index("qwen3:14b")
        assert QWEN3_DOWNGRADE_LADDER.index("qwen3:14b") < QWEN3_DOWNGRADE_LADDER.index("qwen3:8b")
        assert QWEN3_DOWNGRADE_LADDER.index("qwen3:8b") < QWEN3_DOWNGRADE_LADDER.index("qwen3:4b")
        assert QWEN3_DOWNGRADE_LADDER.index("qwen3:4b") < QWEN3_DOWNGRADE_LADDER.index("qwen3:1.7b")


@pytest.mark.parametrize(
    ("preferred", "installed", "expected"),
    [
        ("qwen3:14b", ["qwen3:8b"], "qwen3:8b"),
        ("qwen3:14b", ["qwen3:32b", "qwen3:8b"], "qwen3:8b"),  # downgrade first, wrap later
        ("qwen3:32b", ["qwen3:14b"], "qwen3:14b"),
    ],
)
def test_downgrade_parametrised(preferred: str, installed: list[str], expected: str) -> None:
    assert preferred_or_installed_downgrade(preferred, installed) == expected
