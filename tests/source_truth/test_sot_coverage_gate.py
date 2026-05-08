from pathlib import Path

import pytest

from scripts.source_truth.sot_coverage_gate import gate


def test_gate_passes_on_complete_coverage(tmp_path):
    coverage = {
        "forms": {
            "19_Smear": {"sot_present": True, "sot_complete": True, "missing_variables": []},
        }
    }
    rc = gate(coverage)
    assert rc == 0


def test_gate_fails_on_missing_form(tmp_path):
    coverage = {
        "forms": {
            "95_SAE": {"sot_present": False, "sot_complete": False, "missing_variables": ["X"]},
        }
    }
    rc = gate(coverage)
    assert rc != 0


def test_gate_fails_on_partial_form(tmp_path):
    coverage = {
        "forms": {
            "8_CXR": {"sot_present": True, "sot_complete": False, "missing_variables": ["A"]},
        }
    }
    rc = gate(coverage)
    assert rc != 0
