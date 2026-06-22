"""N10 assertion 16: PHI ledger events must carry taxonomy/jurisdictions/method."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.skills.extract_to_llm_source import _verify_assertion_16_ledger_fields_complete


def _write_ledger(audit_dir: Path, stem: str, events: list[dict]) -> None:
    path = dataset_phi_ledger_path(audit_dir, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"events": events}), encoding="utf-8")


_COMPLETE = {
    "variable_id": "DOB",
    "action": "jitter_date",
    "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": ["USA"]},
    "method": {"name": "SANT_date_jitter"},
}


def test_assertion16_passes_complete_events(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write_ledger(audit, "1_Form", [_COMPLETE])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_fails_no_taxonomy_and_no_rationale(tmp_path: Path) -> None:
    # No rulebook taxonomy AND no rationale → the "why" is undocumented → fail.
    audit = tmp_path / "audit"
    bad = {**_COMPLETE, "rule": {"taxonomy": None, "jurisdictions": ["USA"]}}
    _write_ledger(audit, "1_Form", [bad])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"
    assert "under-documented" in detail


def test_assertion16_config_drop_with_rationale_passes(tmp_path: Path) -> None:
    # A config-driven drop has no rulebook taxonomy but documents jurisdictions +
    # method + rationale — that is complete documentation, so it passes.
    audit = tmp_path / "audit"
    config_drop = {
        "variable_id": "RE_CLINIC",
        "action": "drop",
        "rule": {"taxonomy": None, "jurisdictions": ["USA", "INDIA"]},
        "method": {"name": "field_removal"},
        "rationale": "Applied by PHI scrubber per phi_scrub.yaml configuration",
    }
    _write_ledger(audit, "1_Form", [config_drop])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_fails_missing_method(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    bad = {k: v for k, v in _COMPLETE.items() if k != "method"}
    _write_ledger(audit, "1_Form", [bad])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"


def test_assertion16_vacuous_pass_when_no_events(tmp_path: Path) -> None:
    # All-keep forms have no PHI events → assertion passes vacuously.
    audit = tmp_path / "audit"
    _write_ledger(audit, "1_Form", [])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass"


# ── C3 Extension: Test all 6 non-KEEP actions ──────────────────────────────
# Assertion 16 checks that every PHI ledger event carries:
#   - method (which protection method)
#   - rule.jurisdictions (regulatory scope)
#   - WHY: either rule.taxonomy (rulebook match) OR rationale (config-driven)
#
# The 6 non-KEEP actions are: drop, jitter_date, pseudonymize, generalize,
# cap, suppress_small_cell. Test that each action type, when complete, passes.


def test_assertion16_action_drop_complete(tmp_path: Path) -> None:
    """Action: drop. Config-driven drop with rationale passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "PATIENT_NAME",
        "action": "drop",
        "rule": {"taxonomy": None, "jurisdictions": ["USA", "INDIA"]},
        "method": {"name": "field_removal"},
        "rationale": "HIPAA §164.514(b)(2)(i)(A) — identifiable person names",
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_jitter_date_complete(tmp_path: Path) -> None:
    """Action: jitter_date. HIPAA rulebook-driven event passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "VISIT_DATE",
        "action": "jitter_date",
        "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": ["USA"]},
        "method": {"name": "SANT_date_jitter"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_pseudonymize_complete(tmp_path: Path) -> None:
    """Action: pseudonymize. DPDPA-compliant ID pseudonymization passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "SUBJID",
        "action": "pseudonymize",
        "rule": {"taxonomy": "dpdpa:personal_data_identifier", "jurisdictions": ["INDIA"]},
        "method": {"name": "HMAC_SHA256", "domain_separator": "SUBJ"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_generalize_complete(tmp_path: Path) -> None:
    """Action: generalize. Value-level categorical mapping with taxonomy passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "CLINIC_TYPE",
        "action": "generalize",
        "rule": {"taxonomy": "hipaa_safe_harbor:9_geographic", "jurisdictions": ["USA"]},
        "method": {"name": "categorical_mapping", "map_key": "facility"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_cap_complete(tmp_path: Path) -> None:
    """Action: cap. Age cap (HIPAA Safe Harbor) with taxonomy passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "AGE",
        "action": "cap",
        "rule": {"taxonomy": "hipaa_safe_harbor:1_age", "jurisdictions": ["USA"]},
        "method": {"name": "numeric_cap", "threshold": 89, "label": "90+"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_suppress_small_cell_complete(tmp_path: Path) -> None:
    """Action: suppress_small_cell. K-anonymity proxy with ICMR rule passes."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "HOUSEHOLD_COUNT",
        "action": "suppress_small_cell",
        "rule": {"taxonomy": "icmr:11_7_anonymity", "jurisdictions": ["INDIA"]},
        "method": {"name": "small_cell_suppression", "threshold": 5},
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


# ── C3 Extension: Under-documented non-KEEP actions fail ──────────────────


def test_assertion16_action_drop_fails_no_rationale_or_taxonomy(tmp_path: Path) -> None:
    """Action: drop. No taxonomy AND no rationale → fail."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "PATIENT_NAME",
        "action": "drop",
        "rule": {"taxonomy": None, "jurisdictions": ["USA"]},
        "method": {"name": "field_removal"},
        # missing rationale
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"
    assert "under-documented" in detail


def test_assertion16_action_jitter_date_fails_no_jurisdictions(tmp_path: Path) -> None:
    """Action: jitter_date. No jurisdictions → fail."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "VISIT_DATE",
        "action": "jitter_date",
        "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": None},
        "method": {"name": "SANT_date_jitter"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"


def test_assertion16_action_pseudonymize_fails_no_method(tmp_path: Path) -> None:
    """Action: pseudonymize. No method → fail."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "SUBJID",
        "action": "pseudonymize",
        "rule": {"taxonomy": "dpdpa:personal_data_identifier", "jurisdictions": ["INDIA"]},
        # missing method
    }
    _write_ledger(audit, "1_Form", [event])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"


def test_assertion16_action_generalize_with_rationale_passes_no_taxonomy(
    tmp_path: Path,
) -> None:
    """Action: generalize. Config-driven (no taxonomy) but has rationale → pass."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "CLINIC_TYPE",
        "action": "generalize",
        "rule": {"taxonomy": None, "jurisdictions": ["USA"]},
        "method": {"name": "categorical_mapping", "map_key": "facility"},
        "rationale": "Coarsen facility type per operator policy",
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_action_cap_fails_empty_jurisdictions(tmp_path: Path) -> None:
    """Action: cap. Empty jurisdictions list → fail."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "AGE",
        "action": "cap",
        "rule": {"taxonomy": "hipaa_safe_harbor:1_age", "jurisdictions": []},
        "method": {"name": "numeric_cap", "threshold": 89, "label": "90+"},
    }
    _write_ledger(audit, "1_Form", [event])
    result, _ = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"


def test_assertion16_action_suppress_small_cell_with_rationale_passes(tmp_path: Path) -> None:
    """Action: suppress_small_cell. Rationale suffices when taxonomy absent."""
    audit = tmp_path / "audit"
    event = {
        "variable_id": "HOUSEHOLD_COUNT",
        "action": "suppress_small_cell",
        "rule": {"taxonomy": None, "jurisdictions": ["INDIA"]},
        "method": {"name": "small_cell_suppression", "threshold": 5},
        "rationale": "K-anonymity floor per study protocol",
    }
    _write_ledger(audit, "1_Form", [event])
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


# ── C3 Extension: Multiple actions in one ledger ──────────────────────────


def test_assertion16_multiple_actions_all_complete_passes(tmp_path: Path) -> None:
    """Multiple different actions, all complete → pass."""
    audit = tmp_path / "audit"
    events = [
        {
            "variable_id": "VISIT_DATE",
            "action": "jitter_date",
            "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": ["USA"]},
            "method": {"name": "SANT_date_jitter"},
        },
        {
            "variable_id": "SUBJID",
            "action": "pseudonymize",
            "rule": {"taxonomy": "dpdpa:personal_data_identifier", "jurisdictions": ["INDIA"]},
            "method": {"name": "HMAC_SHA256", "domain_separator": "SUBJ"},
        },
        {
            "variable_id": "PATIENT_NAME",
            "action": "drop",
            "rule": {"taxonomy": None, "jurisdictions": ["USA", "INDIA"]},
            "method": {"name": "field_removal"},
            "rationale": "HIPAA §164.514(b)(2)(i)(A)",
        },
    ]
    _write_ledger(audit, "1_Form", events)
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "pass", detail


def test_assertion16_multiple_actions_one_incomplete_fails(tmp_path: Path) -> None:
    """Multiple actions, one incomplete → fail."""
    audit = tmp_path / "audit"
    events = [
        {
            "variable_id": "VISIT_DATE",
            "action": "jitter_date",
            "rule": {"taxonomy": "hipaa_safe_harbor:3_dates", "jurisdictions": ["USA"]},
            "method": {"name": "SANT_date_jitter"},
        },
        {
            "variable_id": "AGE",
            "action": "cap",
            "rule": {"taxonomy": "hipaa_safe_harbor:1_age", "jurisdictions": ["USA"]},
            # missing method
        },
    ]
    _write_ledger(audit, "1_Form", events)
    result, detail = _verify_assertion_16_ledger_fields_complete(audit)
    assert result == "fail"
    assert "2" in detail or "1" in detail  # count of failures shown
