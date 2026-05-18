"""Unit tests for validate() invariants (1.1) in sot_loader.

Each invariant (a)-(j) has:
  - At least one passing test: minimal dict that satisfies the invariant.
  - At least one failing test: minimal dict that violates ONLY that invariant.

Plus one happy-path full dict covering all invariants together.

TDD: these tests were written BEFORE validate() existed; they confirmed
red by ImportError / AttributeError before the implementation landed.
"""

from __future__ import annotations

from scripts.ai_assistant.sot_loader import ValidationError, ValidationReport, validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_valid() -> dict:
    """Synthetic dict that satisfies every invariant AND actively exercises
    invariants (b), (c), and (f) via real references — so deleting those
    invariant checks would break this helper's passing status.

    Invariant (b): COND_VAR.skip_logic references STUDY_ID (a real variable
                   with an underscore in its name) — the checker must find it.
    Invariant (c): MUT_X and MUT_Y declare reciprocal mutex with each other.
    Invariant (f): COND_VAR.skip_logic references instruction I1, which exists.
    """
    return {
        "sections": {
            "hdr": {"label": None},
            "main": {"label": "MAIN"},
            "done": {"label": None},
        },
        "variables": {
            "STUDY_ID": {
                "section": "hdr",
                "widget": "3 boxes",
                "type": "identifier",
                "phi": "pseudonymize",
            },
            "SITE_CODE": {
                "section": "hdr",
                "widget": "2 boxes",
                "type": "code",
                "phi": "pseudonymize",
                "notes": "quasi-identifier: maps to recruiting site",
            },
            "EVT_VISIT": {
                "section": "main",
                "widget": "date picker",
                "type": "date",
                "phi": "jitter_date",
            },
            "SIGN_COMPDAT": {
                "section": "done",
                "widget": "date",
                "type": "date",
                "phi": "jitter_date",
            },
            "SIG_FIELD": {
                "section": "done",
                "widget": "signature line",
                "type": "signature",
                "phi": "drop",
            },
            "INIT_FLD": {
                "section": "done",
                "widget": "initials boxes",
                "type": "initials",
                "phi": "drop",
            },
            "NOTES_FLD": {
                "section": "main",
                "widget": "free text",
                "type": "free_text",
                "notes": "no PHI expected — administrative remark only",
            },
            # Exercises invariant (b): STUDY_ID is a real variable in the dict.
            # Exercises invariant (f): I1 is a real instruction id.
            "COND_VAR": {
                "section": "main",
                "widget": "checkbox",
                "type": "boolean",
                "skip_logic": "only when STUDY_ID is assigned; follow instruction I1",
            },
            # Exercises invariant (c): reciprocal mutex pair.
            "MUT_X": {
                "section": "main",
                "widget": "radio",
                "type": "boolean",
                "skip_logic": "inferred mutually exclusive with MUT_Y",
            },
            "MUT_Y": {
                "section": "main",
                "widget": "radio",
                "type": "boolean",
                "skip_logic": "inferred mutually exclusive with MUT_X",
            },
        },
        "instructions": [
            {"id": "I1", "text": "If no, skip to bottom."},
        ],
        "arrows": [
            {"from": "STUDY_ID", "to": "SITE_CODE"},
        ],
    }


def _has_error(report: ValidationReport, code: str) -> bool:
    return any(e.code == code for e in report.errors)


# ---------------------------------------------------------------------------
# (a) section-ref-missing
# ---------------------------------------------------------------------------

def test_a_section_ref_present_passes():
    """Variable referencing a section key that exists → no section-ref-missing error."""
    data = {
        "sections": {"main": {"label": "MAIN"}},
        "variables": {
            "GOOD_VAR": {"section": "main", "widget": "box"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "section-ref-missing")


def test_a_section_ref_missing_fails():
    """Variable referencing a non-existent section → section-ref-missing error."""
    data = {
        "sections": {"main": {"label": "MAIN"}},
        "variables": {
            "BAD_VAR": {"section": "ghost", "widget": "box"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "section-ref-missing")


# ---------------------------------------------------------------------------
# (b) skip-logic-var-ref-missing  (underscore-bearing tokens only)
# ---------------------------------------------------------------------------

def test_b_skip_logic_valid_ref_passes():
    """skip_logic containing a variable name that exists → no skip-logic-var-ref-missing."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
            "VAR_B": {"section": "main", "widget": "w", "skip_logic": "only when VAR_A == Yes"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "skip-logic-var-ref-missing")


def test_b_skip_logic_missing_ref_fails():
    """skip_logic containing underscore-bearing token not in variables → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "conditional on MISSING_VAR == Yes"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "skip-logic-var-ref-missing")


def test_b_skip_logic_acronym_no_underscore_is_ignored():
    """Prose acronyms without underscore (HIV, ART, CD4) in skip_logic are not checked."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "only when HIV negative or ART started"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "skip-logic-var-ref-missing")


def test_b_skip_logic_instruction_token_is_ignored():
    """Instruction id tokens like I1, I2 in skip_logic are not treated as variable refs."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "follow instruction I1 or I2 as needed"},
        },
        "instructions": [
            {"id": "I1", "text": "skip if negative"},
            {"id": "I2", "text": "skip if done"},
        ],
    }
    report = validate(data)
    assert not _has_error(report, "skip-logic-var-ref-missing")


# ---------------------------------------------------------------------------
# (c) mutex-reciprocity-broken
# ---------------------------------------------------------------------------

def test_c_mutex_reciprocity_both_present_passes():
    """A.skip_logic says mutually exclusive with B, B.skip_logic says with A → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "inferred mutually exclusive with VAR_B"},
            "VAR_B": {"section": "main", "widget": "w",
                       "skip_logic": "inferred mutually exclusive with VAR_A"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "mutex-reciprocity-broken")


def test_c_mutex_reciprocity_missing_fails():
    """A declares mutex with B, but B has no matching declaration → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "inferred mutually exclusive with VAR_B"},
            "VAR_B": {"section": "main", "widget": "w"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "mutex-reciprocity-broken")


def test_c_mutex_no_declaration_passes():
    """Variables without any mutex declaration generate no mutex error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
            "VAR_B": {"section": "main", "widget": "w"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "mutex-reciprocity-broken")


# ---------------------------------------------------------------------------
# (d) arrow-var-ref-missing
# ---------------------------------------------------------------------------

def test_d_arrow_dict_form_valid_passes():
    """Arrow with dict form referencing existing variables → no arrow-var-ref-missing."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
            "VAR_B": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": {"variable": "VAR_A", "option": "Yes"}, "to": {"variable": "VAR_B"}},
        ],
    }
    report = validate(data)
    assert not _has_error(report, "arrow-var-ref-missing")


def test_d_arrow_string_form_valid_passes():
    """Arrow with string form 'VARNAME (option)' referencing existing variable → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
            "VAR_B": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Yes)", "to": "VAR_B"},
        ],
    }
    report = validate(data)
    assert not _has_error(report, "arrow-var-ref-missing")


def test_d_arrow_instruction_string_skipped():
    """Arrow to/from 'instruction I2' is not treated as a variable ref."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Yes)", "to": "instruction I2"},
        ],
        "instructions": [{"id": "I2", "text": "skip"}],
    }
    report = validate(data)
    assert not _has_error(report, "arrow-var-ref-missing")


def test_d_arrow_missing_var_ref_fails():
    """Arrow referencing a variable not in variables → arrow-var-ref-missing error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Yes)", "to": "GHOST_VAR"},
        ],
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "arrow-var-ref-missing")


# ---------------------------------------------------------------------------
# (e) arrow-option-ref-missing
# ---------------------------------------------------------------------------

def test_e_arrow_option_in_options_passes():
    """Arrow option that appears in source variable's options list → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w", "options": ["Yes", "No"]},
            "VAR_B": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Yes)", "to": "VAR_B"},
        ],
    }
    report = validate(data)
    assert not _has_error(report, "arrow-option-ref-missing")


def test_e_arrow_option_missing_from_options_fails():
    """Arrow option not in source variable's options → arrow-option-ref-missing error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w", "options": ["Yes", "No"]},
            "VAR_B": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Maybe)", "to": "VAR_B"},
        ],
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "arrow-option-ref-missing")


def test_e_arrow_no_options_on_source_var_skips():
    """Arrow with parens but source variable has no options field → check is skipped."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},  # no options
            "VAR_B": {"section": "main", "widget": "w"},
        },
        "arrows": [
            {"from": "VAR_A (Day box)", "to": "VAR_B"},
        ],
    }
    report = validate(data)
    assert not _has_error(report, "arrow-option-ref-missing")


# ---------------------------------------------------------------------------
# (f) instruction-id-ref-missing
# ---------------------------------------------------------------------------

def test_f_instruction_id_present_passes():
    """skip_logic referencing I1 that exists in instructions → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "follow instruction I1"},
        },
        "instructions": [{"id": "I1", "text": "skip"}],
    }
    report = validate(data)
    assert not _has_error(report, "instruction-id-ref-missing")


def test_f_instruction_id_missing_fails():
    """skip_logic referencing I9 that does not exist in instructions → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "follow instruction I9 to skip"},
        },
        "instructions": [{"id": "I1", "text": "skip"}],
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "instruction-id-ref-missing")


def test_f_no_instructions_key_no_error():
    """When there are no instructions at all and no Ixx tokens, no error raised."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {"section": "main", "widget": "w",
                       "skip_logic": "always collected"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "instruction-id-ref-missing")


# ---------------------------------------------------------------------------
# (g) free-text-phi-undeclared
# ---------------------------------------------------------------------------

def test_g_free_text_with_phi_field_passes():
    """free_text variable with explicit phi: field → no free-text-phi-undeclared."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FT_VAR": {"section": "main", "widget": "text", "type": "free_text",
                        "phi": "drop"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "free-text-phi-undeclared")


def test_g_free_text_with_no_phi_expected_note_passes():
    """free_text variable with notes containing 'no PHI expected' → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FT_VAR": {"section": "main", "widget": "text", "type": "free_text",
                        "notes": "no PHI expected — administrative only"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "free-text-phi-undeclared")


def test_g_free_text_without_phi_or_note_fails():
    """free_text variable with neither phi nor notes(no PHI expected) → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FT_VAR": {"section": "main", "widget": "text", "type": "free_text"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "free-text-phi-undeclared")


def test_g_free_text_notes_without_required_phrase_fails():
    """free_text variable with notes but without exact substring → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FT_VAR": {"section": "main", "widget": "text", "type": "free_text",
                        "notes": "this field collects sensitive information"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "free-text-phi-undeclared")


# ---------------------------------------------------------------------------
# (h) jitter-date-allowlist-violation
# ---------------------------------------------------------------------------

def test_h_jitter_date_allowlist_name_passes():
    """Variable ending in _COMPDAT with phi: jitter_date → no jitter-date-allowlist error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "HIV_COMPDAT": {"section": "main", "widget": "date", "type": "date",
                             "phi": "jitter_date"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "jitter-date-allowlist-violation")


def test_h_jitter_date_compdte_variant_passes():
    """Variable ending in _COMPDTE with phi: jitter_date is a completion-date variant."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FC_COMPDTE": {"section": "main", "widget": "date", "type": "date",
                            "phi": "jitter_date"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "jitter-date-allowlist-violation")


def test_h_jitter_date_visit_passes():
    """Variable ending in _VISIT with phi: jitter_date → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "SX_VISIT": {"section": "main", "widget": "date", "type": "date",
                          "phi": "jitter_date"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "jitter-date-allowlist-violation")


def test_h_jitter_date_bad_name_fails():
    """Variable with phi: jitter_date but name not on allowlist → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "HIV_HIVDAT": {"section": "main", "widget": "date", "type": "date",
                            "phi": "jitter_date"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "jitter-date-allowlist-violation")


# ---------------------------------------------------------------------------
# (i) drop-typing-violation
# ---------------------------------------------------------------------------

def test_i_drop_signature_passes():
    """Variable with phi: drop and type: signature → no drop-typing-violation."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "SIG_FLD": {"section": "main", "widget": "sig", "type": "signature",
                         "phi": "drop"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "drop-typing-violation")


def test_i_drop_initials_passes():
    """Variable with phi: drop and type: initials → no drop-typing-violation."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "INIT_FLD": {"section": "main", "widget": "init", "type": "initials",
                          "phi": "drop"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "drop-typing-violation")


def test_i_drop_datetime_passes():
    """Variable with phi: drop and type: datetime → no drop-typing-violation."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "TS_FIELD": {"section": "main", "widget": "ts", "type": "datetime",
                          "phi": "drop"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "drop-typing-violation")


def test_i_drop_wrong_type_fails():
    """Variable with phi: drop and type: free_text → drop-typing-violation error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "WEIRD_FLD": {"section": "main", "widget": "box", "type": "free_text",
                           "phi": "drop"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "drop-typing-violation")


# ---------------------------------------------------------------------------
# (j) pseudonymize-typing-violation
# ---------------------------------------------------------------------------

def test_j_pseudonymize_identifier_passes():
    """Variable with phi: pseudonymize and type: identifier → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "SUBJ_ID": {"section": "main", "widget": "boxes", "type": "identifier",
                         "phi": "pseudonymize"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "pseudonymize-typing-violation")


def test_j_pseudonymize_code_with_notes_passes():
    """Variable with phi: pseudonymize, type: code, and notes → no error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "SITE_CD": {"section": "main", "widget": "boxes", "type": "code",
                         "phi": "pseudonymize",
                         "notes": "quasi-identifier: maps to recruiting site"},
        },
    }
    report = validate(data)
    assert not _has_error(report, "pseudonymize-typing-violation")


def test_j_pseudonymize_code_without_notes_fails():
    """Variable with phi: pseudonymize, type: code, but no notes → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "SITE_CD": {"section": "main", "widget": "boxes", "type": "code",
                         "phi": "pseudonymize"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "pseudonymize-typing-violation")


def test_j_pseudonymize_wrong_type_fails():
    """Variable with phi: pseudonymize and type: date → error."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "WEIRD": {"section": "main", "widget": "date", "type": "date",
                       "phi": "pseudonymize"},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "pseudonymize-typing-violation")


# ---------------------------------------------------------------------------
# Happy-path full synthetic dict
# ---------------------------------------------------------------------------

def test_happy_path_full_dict_passes_all():
    """Comprehensive synthetic dict covering all invariants passes with no errors."""
    data = _minimal_valid()
    report = validate(data)
    assert report.passed, f"Expected passed=True but got errors: {report.errors}"
    assert report.errors == []


# ---------------------------------------------------------------------------
# Defensive / edge-case tests
# ---------------------------------------------------------------------------

def test_missing_sections_key_no_crash():
    """validate() does not raise when 'sections' key is missing entirely."""
    data = {
        "variables": {
            "VAR_A": {"section": "main", "widget": "w"},
        },
    }
    report = validate(data)
    # Should return errors (section-ref-missing since sections is empty), not raise
    assert isinstance(report, ValidationReport)


def test_empty_dict_no_crash():
    """validate() does not raise on a completely empty dict."""
    report = validate({})
    assert isinstance(report, ValidationReport)


def test_none_phi_field_on_free_text_fails():
    """free_text with phi: null (explicit None) is treated same as missing phi."""
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "FT_VAR": {"section": "main", "widget": "text", "type": "free_text",
                        "phi": None},
        },
    }
    report = validate(data)
    assert not report.passed
    assert _has_error(report, "free-text-phi-undeclared")


def test_validation_error_dataclass_fields():
    """ValidationError has code, path, and message fields."""
    err = ValidationError(code="test-code", path="variables.X", message="test msg")
    assert err.code == "test-code"
    assert err.path == "variables.X"
    assert err.message == "test msg"


def test_validation_report_dataclass_fields():
    """ValidationReport has passed and errors fields."""
    report = ValidationReport(passed=True)
    assert report.passed is True
    assert report.errors == []


# ---------------------------------------------------------------------------
# Issue 1 — mutex-reciprocity substring false-negative (word-boundary fix)
# ---------------------------------------------------------------------------

def test_c_mutex_reciprocity_substring_false_negative_caught():
    """Prefix-name false-negative: VAR_A declares mutex with VAR_AB, but VAR_AB
    reciprocates with a *different* name (VAR_ABC).  The old plain-substring
    check finds 'mutually exclusive with VAR_AB' inside
    'mutually exclusive with VAR_ABC' and silently skips the error.
    The word-boundary regex must catch it as mutex-reciprocity-broken.
    """
    data = {
        "sections": {"main": {"label": "M"}},
        "variables": {
            "VAR_A": {
                "section": "main",
                "widget": "w",
                "skip_logic": "inferred mutually exclusive with VAR_AB",
            },
            "VAR_AB": {
                "section": "main",
                "widget": "w",
                # Reciprocates with VAR_ABC, NOT with VAR_A — so VAR_A is unreciprocated.
                "skip_logic": "inferred mutually exclusive with VAR_ABC",
            },
            "VAR_ABC": {
                "section": "main",
                "widget": "w",
                "skip_logic": "inferred mutually exclusive with VAR_AB",
            },
        },
    }
    report = validate(data)
    assert not report.passed, (
        "Expected passed=False: VAR_A's mutex with VAR_AB is unreciprocated"
    )
    assert _has_error(report, "mutex-reciprocity-broken"), (
        "Expected mutex-reciprocity-broken error for VAR_A → VAR_AB"
    )


# ---------------------------------------------------------------------------
# Issue 2 — root-not-a-dict uses distinct error code 'malformed-root'
# ---------------------------------------------------------------------------

def test_root_not_a_dict_fails_with_malformed_root():
    """validate([]) and validate(None) must return passed=False with code
    'malformed-root', not the overloaded 'section-ref-missing' code.
    """
    for bad_root in ([], None):
        report = validate(bad_root)
        assert not report.passed, f"Expected passed=False for input {bad_root!r}"
        assert _has_error(report, "malformed-root"), (
            f"Expected 'malformed-root' error code for input {bad_root!r}, "
            f"got codes: {[e.code for e in report.errors]}"
        )
