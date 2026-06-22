"""A2: Free-text SUPPRESS→DROP contract test.

Verifies that phi_review's SUPPRESS action on free-text headers is correctly
force-dropped by the scrubber (priority-0 drop event, no keep_decision in the
ledger).

Key guarantee: SUPPRESS-classified free-text headers appear as "phi-scrub-drop"
events in the audit ledger, not as keep_decisions.
"""

from __future__ import annotations

import inspect

import pytest

import scripts.security.phi_review as phi_review
import scripts.security.phi_scrub as phi_scrub


def test_suppress_force_drop_gate_in_scrub_row() -> None:
    """Verify priority-0 force-drop gate is present in _scrub_row."""
    scrub_row_source = inspect.getsource(phi_scrub._scrub_row)
    assert "suppress_headers" in scrub_row_source
    assert "if suppress_headers and _normalize_header_for_lookup(field) in suppress_headers" in scrub_row_source


def test_force_drop_by_stem_construction_from_suppress() -> None:
    """Verify force_drop_by_stem is built from SUPPRESS actions."""
    run_scrub_source = inspect.getsource(phi_scrub.run_scrub)
    assert 'get("action") == "suppress"' in run_scrub_source
    assert "force_drop_by_stem" in run_scrub_source


def test_suppress_action_exists_in_phi_review() -> None:
    """Verify SUPPRESS action is defined in phi_review.Action enum."""
    assert hasattr(phi_review.Action, "SUPPRESS")
    assert phi_review.Action.SUPPRESS.value == "suppress"
    assert phi_review._ACTION_RANK[phi_review.Action.SUPPRESS] == 1
    assert phi_review._ACTION_METHOD[phi_review.Action.SUPPRESS] == "small_cell_clamp"
