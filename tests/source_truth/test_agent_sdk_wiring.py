"""Smoke test: SDK wiring resolves to a callable returning the expected
output shape on a trivial deterministic prompt.

Marked `slow`. Skipped when ANTHROPIC_API_KEY is missing.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.slow


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY required for live SDK smoke",
)
def test_extractor_sdk_returns_yaml_and_pack_keys():
    from scripts.source_truth.sot_extractor_agent import invoke_subagent

    out = invoke_subagent(
        "Return JSON with keys 'yaml' (string 'hello') and "
        "'evidence_pack' (string '{}'). Nothing else."
    )
    assert "yaml" in out
    assert "evidence_pack" in out


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY required for live SDK smoke",
)
def test_reviewer_sdk_returns_verdict_and_notes():
    from scripts.source_truth.sot_reviewer_agent import invoke_reviewer_subagent

    out = invoke_reviewer_subagent(
        "Return JSON with keys 'verdict' (string 'agree') and "
        "'notes' (string 'ok'). Nothing else."
    )
    assert out["verdict"] in {"agree", "disagree_minor", "disagree_major"}
    assert isinstance(out["notes"], str)
