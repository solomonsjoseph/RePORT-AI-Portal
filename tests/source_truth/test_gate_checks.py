from __future__ import annotations

from scripts.source_truth.gate_checks import (
    check_c_phi_ledger_alignment,
    check_d_phi_action_mismatch,
    check_g_phi_dropped_vars_absent,
)


def _entry(form: str, variable_id: str, action: str = "drop") -> dict:
    return {
        "form": form,
        "variable_id": variable_id,
        "action": action,
        "rule": {"taxonomy": "hipaa:1", "project_category": "name"},
        "rationale": "test",
        "where": {"dataset_file": "1A.xlsx", "pdf_source": None},
        "count": 1,
    }


# ---------------------------------------------------------------------------
# check_c tests
# ---------------------------------------------------------------------------


def test_c_no_findings_when_keys_match():
    declared = [_entry("FormA", "VAR1"), _entry("FormA", "VAR2")]
    as_written = [_entry("FormA", "VAR1"), _entry("FormA", "VAR2")]
    assert check_c_phi_ledger_alignment(declared, as_written) == []


def test_c_finding_declared_only():
    declared = [_entry("FormA", "VAR1")]
    as_written = []
    findings = check_c_phi_ledger_alignment(declared, as_written)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "C"
    assert f.form == "FormA"
    assert f.variable_id == "VAR1"
    assert "no as-written counterpart" in f.issue


def test_c_finding_as_written_only():
    declared = []
    as_written = [_entry("FormA", "VAR2")]
    findings = check_c_phi_ledger_alignment(declared, as_written)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "C"
    assert f.form == "FormA"
    assert f.variable_id == "VAR2"
    assert "no declared counterpart" in f.issue


def test_c_returns_sorted():
    declared = [_entry("FormB", "VAR1"), _entry("FormA", "VAR2")]
    as_written = []
    findings = check_c_phi_ledger_alignment(declared, as_written)
    keys = [(f.form, f.variable_id) for f in findings]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# check_d tests
# ---------------------------------------------------------------------------


def test_d_no_findings_when_actions_match():
    declared = [_entry("FormA", "VAR1", action="drop")]
    as_written = [_entry("FormA", "VAR1", action="drop")]
    assert check_d_phi_action_mismatch(declared, as_written) == []


def test_d_finding_on_action_mismatch():
    declared = [_entry("FormA", "VAR1", action="drop")]
    as_written = [_entry("FormA", "VAR1", action="pseudonymize")]
    findings = check_d_phi_action_mismatch(declared, as_written)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "D"
    assert f.form == "FormA"
    assert f.variable_id == "VAR1"
    assert "drop" in f.issue
    assert "pseudonymize" in f.issue


def test_d_skips_when_key_not_in_as_written():
    declared = [_entry("FormA", "VAR1", action="drop")]
    as_written = []
    findings = check_d_phi_action_mismatch(declared, as_written)
    assert findings == []


def test_d_returns_sorted():
    declared = [
        _entry("FormB", "VAR1", action="drop"),
        _entry("FormA", "VAR2", action="drop"),
    ]
    as_written = [
        _entry("FormB", "VAR1", action="pseudonymize"),
        _entry("FormA", "VAR2", action="pseudonymize"),
    ]
    findings = check_d_phi_action_mismatch(declared, as_written)
    keys = [(f.form, f.variable_id) for f in findings]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# check_g tests
# ---------------------------------------------------------------------------


def test_g_no_findings_when_dropped_var_absent():
    events = [_entry("FormA", "VAR1", action="drop")]
    scrubbed = {"FormA": frozenset({"OTHER_VAR"})}
    assert check_g_phi_dropped_vars_absent(events, scrubbed) == []


def test_g_finding_when_dropped_var_present():
    events = [_entry("FormA", "VAR1", action="drop")]
    scrubbed = {"FormA": frozenset({"VAR1"})}
    findings = check_g_phi_dropped_vars_absent(events, scrubbed)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "G"
    assert f.form == "FormA"
    assert f.variable_id == "VAR1"


def test_g_finding_for_birthdate_drop_action():
    events = [_entry("FormA", "DOB", action="birthdate_drop")]
    scrubbed = {"FormA": frozenset({"DOB"})}
    findings = check_g_phi_dropped_vars_absent(events, scrubbed)
    assert len(findings) == 1
    assert findings[0].check == "G"


def test_g_ignores_non_drop_actions():
    events = [_entry("FormA", "VAR1", action="pseudonymize")]
    scrubbed = {"FormA": frozenset({"VAR1"})}
    assert check_g_phi_dropped_vars_absent(events, scrubbed) == []


def test_g_ignores_form_not_in_scrubbed_cols():
    events = [_entry("FormA", "VAR1", action="drop")]
    scrubbed = {}
    assert check_g_phi_dropped_vars_absent(events, scrubbed) == []
