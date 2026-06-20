"""Static checks for SoT Makefile target contracts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]


def test_sot_validate_targets_candidate_by_default() -> None:
    """sot-validate must validate the candidate YAML before promotion."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "CANDIDATE ?= /tmp/$(FORM)_lean.yaml" in makefile
    assert "--lean $(CANDIDATE)" in makefile
    assert "--candidate $(CANDIDATE)" in makefile
    assert "sot-verify-output:" in makefile
    # N3: policy YAML lives in the audit construction zone, not llm_source.
    assert (
        "--policy output/$(STUDY)/audit/SoT_construction/$(SOT_PAIR)/pdf/$(FORM)_policy.yaml"
        in makefile
    )
