from scripts.source_truth.sot_coverage_gate import gate


def test_gate_passes_on_complete_coverage():
    coverage = {
        "forms": {
            "19_Smear": {"sot_present": True, "sot_complete": True, "missing_variables": []},
        }
    }
    rc = gate(coverage)
    assert rc == 0


def test_gate_fails_on_missing_form():
    coverage = {
        "forms": {
            "95_SAE": {"sot_present": False, "sot_complete": False, "missing_variables": ["X"]},
        }
    }
    rc = gate(coverage)
    assert rc == 1


def test_gate_fails_on_partial_form():
    coverage = {
        "forms": {
            "8_CXR": {"sot_present": True, "sot_complete": False, "missing_variables": ["A"]},
        }
    }
    rc = gate(coverage)
    assert rc == 1


def test_gate_fails_on_empty_forms():
    assert gate({"forms": {}}) == 1


def test_gate_fails_on_missing_forms_key():
    assert gate({}) == 1


def test_gate_fails_on_malformed_form_entry():
    coverage = {"forms": {"X": None}}
    assert gate(coverage) == 1


def test_gate_passes_with_excluded_form():
    """A form marked excluded=True should not block the gate."""
    coverage = {
        "forms": {
            "19_Smear": {"sot_present": True, "sot_complete": True, "missing_variables": []},
            "30_Air_Quality": {
                "excluded": True,
                "exclusion_reason": "deprecated_stub",
                "sot_present": True,
                "sot_complete": True,
                "missing_variables": [],
            },
        }
    }
    rc = gate(coverage)
    assert rc == 0


def test_gate_passes_when_alias_resolves_to_complete_canonical():
    """A form with alias_of set and sot_complete=True should not block the gate."""
    coverage = {
        "forms": {
            "19_Smear": {"sot_present": True, "sot_complete": True, "missing_variables": []},
            "19_Smear_alias": {
                "alias_of": "19_Smear",
                "sot_present": True,
                "sot_complete": True,
                "missing_variables": [],
            },
        }
    }
    rc = gate(coverage)
    assert rc == 0


def test_gate_fails_when_alias_canonical_missing():
    """An alias whose canonical policy is absent should fail the gate."""
    coverage = {
        "forms": {
            "14_Case_Control": {
                "alias_of": "14_CaseControl",
                "sot_present": False,
                "sot_complete": False,
                "missing_variables": [],
            },
        }
    }
    rc = gate(coverage)
    assert rc == 1
