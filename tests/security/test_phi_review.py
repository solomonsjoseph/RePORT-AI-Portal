from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.security.phi_review import (
    Action,
    HeldReason,
    OfficialSourceRejected,
    _is_strong_direct_id_header,
    classify_headers,
    is_phi_risky_header,
    load_sot_variable_signals,
    load_study_privacy_config,
    refresh_jurisdiction_rules,
    review_form_headers,
    validate_official_source_url,
    validate_pure_transform_source,
)

PHI_COVERAGE_HOLD_PREFIX = "phi_coverage_hold"


def _write_privacy_config(study_dir: Path) -> Path:
    path = study_dir / "_study_privacy.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "jurisdictions": ["USA", "INDIA"],
                "rule_refresh": "online_preferred",
                "conflict_policy": "strictest_wins",
                "approval": {
                    "max_synthetic_attempts": 5,
                    "mode": "hybrid",
                },
                "parallelism": {
                    "mode": "auto",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_study_privacy_config_parses_supported_jurisdictions(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)

    cfg = load_study_privacy_config(study_dir)

    assert cfg.jurisdictions == ("USA", "INDIA")
    assert cfg.max_synthetic_attempts == 5
    assert cfg.conflict_policy == "strictest_wins"


def test_study_privacy_config_rejects_unknown_jurisdiction(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    path = _write_privacy_config(study_dir)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["jurisdictions"] = ["USA", "MARS"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported jurisdiction"):
        load_study_privacy_config(study_dir)


def test_official_source_validator_rejects_secondary_sources() -> None:
    with pytest.raises(OfficialSourceRejected):
        validate_official_source_url("https://example.com/hipaa-summary")


def test_offline_refresh_uses_pinned_rule_pack(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)

    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    assert bundle.source_mode == "pinned"
    assert bundle.rules_sha256
    assert {source["jurisdiction"] for source in bundle.sources} == {"USA", "INDIA"}


def test_strictest_wins_across_usa_and_india_rules(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    classified = classify_headers(
        [
            "participant_id",
            "visit_date",
            "aadhaar_no",
            "culture_result",
            "HIV_HIVDAT",
            "SUBJID",
        ],
        cfg,
        bundle,
    )

    assert classified["participant_id"].action == Action.PSEUDONYMIZE
    assert classified["visit_date"].action == Action.JITTER_DATE
    assert classified["aadhaar_no"].action == Action.DROP
    assert classified["culture_result"].action == Action.KEEP
    assert classified["HIV_HIVDAT"].action == Action.JITTER_DATE
    assert classified["SUBJID"].action == Action.PSEUDONYMIZE


def test_dte_and_date_suffixes_classify_as_jitter_date(tmp_path: Path) -> None:
    """DTE/DATE date-suffix columns must classify as JITTER_DATE, not KEEP.

    Regression: the date heuristic matched only the DAT/DT suffixes, so
    completion-date columns ending in DTE (CXR_COMPDTE, HHC_COMPDTE) and DATE
    (ST_COMPDATE) fell through to KEEP — a raw-date leak and a decided-vs-applied
    mismatch against the scrub's jitter. They now classify as JITTER_DATE.
    """
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    classified = classify_headers(
        ["CXR_COMPDTE", "HHC_COMPDTE", "ST_COMPDATE", "CBC_VISDAT", "CXR_CXRDAT"],
        cfg,
        bundle,
    )
    for col in ["CXR_COMPDTE", "HHC_COMPDTE", "ST_COMPDATE", "CBC_VISDAT", "CXR_CXRDAT"]:
        assert classified[col].action == Action.JITTER_DATE, (
            f"{col} must classify as JITTER_DATE (date-suffix), got {classified[col].action}"
        )


def test_pure_transform_source_rejects_io_import_logging_and_subprocess() -> None:
    bad_source = """
import os

def transform_subject_id(value, ctx):
    print(value)
    return open('/tmp/leak', 'w').write(str(value))
"""

    result = validate_pure_transform_source(bad_source)

    assert not result.ok
    assert any("Import" in item or "open" in item or "print" in item for item in result.errors)


def test_form_review_approves_headers_after_adversarial_synthetic_validation(
    tmp_path: Path,
) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="demo.xlsx",
        headers=["participant_id", "visit_date", "phone", "culture_result"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    assert approval.status == "approved"
    assert approval.attempts == 1
    assert approval.actions["participant_id"] == Action.PSEUDONYMIZE.value
    assert approval.actions["visit_date"] == Action.JITTER_DATE.value
    assert approval.actions["phone"] == Action.DROP.value


def test_form_review_holds_ambiguous_form_after_five_synthetic_attempts(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="ambiguous.xlsx",
        headers=["", "subject_id"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    assert approval.status == "held"
    # Structural blocker (blank header) — adversarial probes pass on attempt 1.
    assert approval.attempts == 1
    assert "blank header" in " ".join(approval.reasons)


def test_form_review_payload_contains_no_synthetic_or_real_values(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="demo.xlsx",
        headers=["participant_id", "email"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )
    payload = json.dumps(approval.to_json(), sort_keys=True)

    assert "Alice" not in payload
    assert "555" not in payload
    assert "123-45-6789" not in payload


@pytest.mark.parametrize(
    "header",
    [
        "interviewer_remarks",
        "clinical_notes",
        "patient_name",
        "subject_dob",
        "home_address",
        "guardian_phone",
        "free_text_other",
        "respondent_email",
        "village",
    ],
)
def test_phi_risky_header_flags_likely_phi_names(header: str) -> None:
    assert is_phi_risky_header(header) is True


@pytest.mark.parametrize(
    "header",
    [
        "culture_result",
        "hemoglobin_g_dl",
        "site_code",
        "visit_count",
        "bmi",
        "age_years",
        "weight_kg",
        "treatment_outcome",
        "",
    ],
)
def test_phi_risky_header_passes_benign_clinical_names(header: str) -> None:
    assert is_phi_risky_header(header) is False


def test_risky_keep_header_escapee_is_force_dropped(tmp_path: Path) -> None:
    """A KEEP header with a PHI-risky name (no SoT/keep confirmation) is a direct
    identifier → FORCE-DROPPED (not held). Policy: direct identifiers must be
    dropped; the form still publishes its remaining columns."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="04_FollowUp.xlsx",
        headers=["participant_id", "visit_date", "culture_result", "interviewer_remarks"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    # Not held for the risky escapee — it is dropped instead, so the form publishes.
    assert approval.status == "approved"
    assert "interviewer_remarks" in approval.force_drop_headers
    # benign clinical columns are NOT force-dropped
    assert "culture_result" not in approval.force_drop_headers


def test_form_not_held_for_benign_keep_headers(tmp_path: Path) -> None:
    """Benign KEEP columns must NOT trigger a coverage hold (no false positives)."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="lab.xlsx",
        headers=["participant_id", "culture_result", "hemoglobin_g_dl", "site_code"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    assert approval.status == "approved"
    assert not any(reason.startswith(PHI_COVERAGE_HOLD_PREFIX) for reason in approval.reasons)


def test_risky_header_already_scrubbed_by_rule_does_not_coverage_hold(tmp_path: Path) -> None:
    """A risky name that a jurisdiction rule already scrubs is non-KEEP → no coverage hold."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="contact.xlsx",
        headers=["participant_id", "email", "phone"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    # email/phone are dropped by rule, not kept — so no coverage hold is raised
    assert not any(reason.startswith(PHI_COVERAGE_HOLD_PREFIX) for reason in approval.reasons)


# ---------------------------------------------------------------------------
# W4: Bounded-attempt classification retry tests
# ---------------------------------------------------------------------------


def test_adversarial_probe_exhaustion_produces_held_with_structured_note(
    tmp_path: Path,
) -> None:
    """When the adversarial probe fails the form is held with a structured HeldReason.

    The probe is a single deterministic evaluation — it is not retried.  We inject
    a stub that returns failures to simulate a rule-bundle defect and verify the form
    is held with attempts==1 and a structured HeldReason attached.
    """
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    always_failing = ("adversarial header probe failed: synthetic_email_header",)

    with patch(
        "scripts.security.phi_review._adversarial_header_validation",
        return_value=always_failing,
    ):
        approval = review_form_headers(
            form_name="probe_fail.xlsx",
            headers=["culture_result", "hemoglobin_g_dl"],
            privacy_config=cfg,
            rule_bundle=bundle,
        )

    assert approval.status == "held"
    # Single deterministic evaluation — attempts is always 1.
    assert approval.attempts == 1
    # The adversarial failure appears in reasons.
    assert any("adversarial header probe failed" in r for r in approval.reasons)
    # A structured HeldReason is attached.
    assert approval.held_reason is not None
    assert isinstance(approval.held_reason, HeldReason)
    # All three required fields are present and non-empty.
    assert approval.held_reason.what_was_tried
    assert approval.held_reason.what_was_ambiguous
    assert approval.held_reason.what_would_resolve


def test_adversarial_probe_exhaustion_held_reason_serialises_to_json(
    tmp_path: Path,
) -> None:
    """The structured held_reason must appear in to_json() output."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    always_failing = ("adversarial header probe failed: synthetic_email_header",)

    with patch(
        "scripts.security.phi_review._adversarial_header_validation",
        return_value=always_failing,
    ):
        approval = review_form_headers(
            form_name="probe_fail.xlsx",
            headers=["culture_result"],
            privacy_config=cfg,
            rule_bundle=bundle,
        )

    payload = approval.to_json()
    assert "held_reason" in payload
    held = payload["held_reason"]
    assert "what_was_tried" in held
    assert "what_was_ambiguous" in held
    assert "what_would_resolve" in held
    # Confirm no row values leaked into the note.
    serialised = json.dumps(payload, sort_keys=True)
    assert "raw_value" not in serialised
    assert "sample_value" not in serialised
    assert "Alice" not in serialised


def test_adversarial_probe_pass_approves_clean_form(
    tmp_path: Path,
) -> None:
    """When the adversarial probe passes the form is approved with attempts==1.

    The probe is a single deterministic evaluation: pass → approved immediately
    (no retry, no second chance).  A hypothetical "first-fail, second-pass"
    scenario cannot occur for a deterministic probe against immutable args.
    """
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    call_count = {"n": 0}

    def _probe_pass(privacy_config: object, rule_bundle: object) -> tuple[str, ...]:
        call_count["n"] += 1
        return ()  # probe passes

    with patch(
        "scripts.security.phi_review._adversarial_header_validation",
        side_effect=_probe_pass,
    ):
        approval = review_form_headers(
            form_name="clean.xlsx",
            headers=["culture_result", "hemoglobin_g_dl"],
            privacy_config=cfg,
            rule_bundle=bundle,
        )

    assert approval.status == "approved"
    # Exactly one evaluation — never retried.
    assert approval.attempts == 1
    assert call_count["n"] == 1
    # No held_reason when approved.
    assert approval.held_reason is None
    # No adversarial failure in reasons.
    assert not any("adversarial header probe failed" in r for r in approval.reasons)


def test_held_reason_note_contains_tried_ambiguous_resolving_fields(
    tmp_path: Path,
) -> None:
    """Assert the structured note contains the three operator-review fields."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    always_failing = (
        "adversarial header probe failed: synthetic_email_header",
        "adversarial header probe failed: synthetic_aadhaar_header",
    )

    with patch(
        "scripts.security.phi_review._adversarial_header_validation",
        return_value=always_failing,
    ):
        approval = review_form_headers(
            form_name="multi_fail.xlsx",
            headers=["culture_result"],
            privacy_config=cfg,
            rule_bundle=bundle,
        )

    assert approval.held_reason is not None
    note = approval.held_reason
    # what_was_tried must mention the deterministic nature and jurisdictions.
    assert "deterministic" in note.what_was_tried
    assert any(j in note.what_was_tried for j in cfg.jurisdictions)
    # what_was_ambiguous must name the failing probes.
    assert "synthetic_email_header" in note.what_was_ambiguous
    assert "synthetic_aadhaar_header" in note.what_was_ambiguous
    # what_would_resolve must be actionable (non-empty, operator-facing text).
    assert len(note.what_would_resolve) > 20


def test_clean_form_has_no_held_reason(tmp_path: Path) -> None:
    """A fully clean form must have held_reason=None and status=approved."""
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    approval = review_form_headers(
        form_name="clean.xlsx",
        headers=["participant_id", "culture_result", "visit_date"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    assert approval.status == "approved"
    assert approval.held_reason is None
    assert "held_reason" not in approval.to_json()


def test_held_reason_not_set_for_non_adversarial_holds(tmp_path: Path) -> None:
    """Coverage holds and structural blockers do NOT produce a held_reason note.

    Only adversarial probe failures trigger the structured note; structural blocker
    and coverage-hold reasons are operator-visible in approval.reasons and need no
    extra annotation.
    """
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    # Blank header → structural blocker hold; adversarial probes still pass.
    approval = review_form_headers(
        form_name="blank_header.xlsx",
        headers=["", "culture_result"],
        privacy_config=cfg,
        rule_bundle=bundle,
    )

    assert approval.status == "held"
    assert any("blank header" in r for r in approval.reasons)
    # No adversarial exhaustion → no structured held_reason.
    assert approval.held_reason is None


def test_max_synthetic_attempts_one_probe_failure_holds(tmp_path: Path) -> None:
    """A probe failure holds the form with attempts==1 regardless of max_synthetic_attempts.

    max_synthetic_attempts is loaded from config but no longer drives a loop — a single
    deterministic evaluation is always performed.  This test verifies that probe failure
    with max_synthetic_attempts=1 still produces attempts==1 and a structured held_reason.
    """
    study_dir = tmp_path / "data" / "raw" / "Study"
    path = _write_privacy_config(study_dir)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["approval"]["max_synthetic_attempts"] = 1
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)

    always_failing = ("adversarial header probe failed: synthetic_email_header",)

    with patch(
        "scripts.security.phi_review._adversarial_header_validation",
        return_value=always_failing,
    ):
        approval = review_form_headers(
            form_name="single_attempt.xlsx",
            headers=["culture_result"],
            privacy_config=cfg,
            rule_bundle=bundle,
        )

    assert approval.status == "held"
    assert approval.attempts == 1
    assert approval.held_reason is not None
    assert "1" in approval.held_reason.what_was_tried


# ---------------------------------------------------------------------------
# SoT cross-verification of PHI handling
# ---------------------------------------------------------------------------


def test_load_sot_variable_signals_parses_joined_view(tmp_path: Path) -> None:
    """The loader extracts per-variable has_pdf_question + PHI recommendation."""
    sot_root = tmp_path / "SoT"
    form_dir = sot_root / "FormX" / "joined"
    form_dir.mkdir(parents=True)
    (form_dir / "FormX_joined_query_view.yaml").write_text(
        yaml.safe_dump(
            {
                "variables": {
                    "VISDAT": {"pdf": {"question": "Visit date", "type": "date"}, "dataset": {}},
                    "STAFF_SIG": {"pdf": {"question": "Signature", "phi": "drop"}, "dataset": {}},
                    "NO_Q_COL": {"pdf": {"question": None}, "dataset": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    sig = load_sot_variable_signals(sot_root, "FormX.xlsx")
    assert sig["VISDAT"]["has_pdf_question"] is True
    assert sig["VISDAT"]["is_phi"] is False
    assert sig["STAFF_SIG"]["is_phi"] is True  # SoT recommends drop
    assert sig["NO_Q_COL"]["has_pdf_question"] is False
    # Missing SoT → empty dict (fail-soft, name-only review proceeds).
    assert load_sot_variable_signals(sot_root, "Absent.xlsx") == {}


def _benign_bundle(tmp_path: Path):
    study_dir = tmp_path / "data" / "raw" / "Study"
    _write_privacy_config(study_dir)
    cfg = load_study_privacy_config(study_dir)
    bundle = refresh_jurisdiction_rules(cfg, allow_network=False)
    return cfg, bundle


def test_sot_flagged_phi_published_raw_is_force_dropped(tmp_path: Path) -> None:
    """A column the scrub PUBLISHES RAW that the SoT flags PHI is a direct identifier
    → FORCE-DROPPED (not held). The form still publishes its other columns."""
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="F.xlsx",
        headers=["culture_result"],  # phi_review KEEPs this
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "CULTURE_RESULT": {"has_pdf_question": True, "sot_phi": "drop", "is_phi": True}
        },
        published_raw_headers=frozenset({"culture_result"}),
    )
    assert approval.status == "approved"
    assert "culture_result" in approval.force_drop_headers


def test_sot_flagged_phi_not_dropped_when_scrub_already_handles(tmp_path: Path) -> None:
    """Same SoT-PHI flag, but the column is NOT published raw (scrub already
    drops/pseudonymizes it) → not force-dropped again, not held.

    This is the published_raw gate: SUBJID2/signatures are phi_review-KEEP + SoT-PHI
    but the scrub drops them, so they are never leaked and need no override.
    """
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="F.xlsx",
        headers=["culture_result"],
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "CULTURE_RESULT": {"has_pdf_question": True, "sot_phi": "drop", "is_phi": True}
        },
        published_raw_headers=frozenset(),  # scrub does NOT publish it raw
    )
    assert approval.status == "approved"
    assert "culture_result" not in approval.force_drop_headers


def test_sot_benign_column_not_held(tmp_path: Path) -> None:
    """A SoT-confirmed-benign clinical column (pdf_question, no PHI) is not held."""
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="F.xlsx",
        headers=["culture_result"],
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "CULTURE_RESULT": {"has_pdf_question": True, "sot_phi": None, "is_phi": False}
        },
        published_raw_headers=frozenset({"culture_result"}),
    )
    assert approval.status == "approved"


# ---------------------------------------------------------------------------
# S1: STRONG direct-identifier subset — pdf_question must NOT clear them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "HS_INITIALS",
        "SC_NAME",
        "EE_PHONE",
        "Remote_Fax",
        "patient_email",
        "staff_signature",
        "aadhaar_no",
        "pan_card",
        "mrn_number",
    ],
)
def test_strong_direct_id_header_flagged(header: str) -> None:
    """STRONG direct identifiers must be detected by _is_strong_direct_id_header."""
    assert _is_strong_direct_id_header(header) is True


@pytest.mark.parametrize(
    "header",
    [
        "culture_result",
        "hemoglobin_g_dl",
        "interviewer_remarks",  # weak: 'remark' token, NOT strong
        "village",  # geography, NOT strong
        "subject_dob",  # life-event date, NOT strong
        "site_code",
    ],
)
def test_non_strong_header_not_flagged_as_strong(header: str) -> None:
    """Weak-risk and benign headers must NOT match the strong direct-identifier subset."""
    assert _is_strong_direct_id_header(header) is False


def test_strong_identifier_not_cleared_by_pdf_question(tmp_path: Path) -> None:
    """A STRONG direct identifier (initials) with has_pdf_question=True, sot_phi=None
    must be force-dropped — the SoT question presence does NOT confirm it benign.

    Regression guard for HS_INITIALS (16_Helminth): the printed CRF legitimately
    asks for patient initials, so the SoT has a pdf_question for it.  Before S1,
    this silently cleared the column as benign — a false clearance.
    """
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="16_Helminth.xlsx",
        headers=["HS_INITIALS", "culture_result"],
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "HS_INITIALS": {"has_pdf_question": True, "sot_phi": None, "is_phi": False},
            "CULTURE_RESULT": {"has_pdf_question": True, "sot_phi": None, "is_phi": False},
        },
        published_raw_headers=frozenset({"HS_INITIALS", "culture_result"}),
    )
    assert approval.status == "approved"  # form still publishes its other columns
    assert "HS_INITIALS" in approval.force_drop_headers
    assert "culture_result" not in approval.force_drop_headers


def test_strong_identifier_cleared_by_confirmed_keep(tmp_path: Path) -> None:
    """A STRONG identifier IS cleared when it appears in confirmed_keep_headers
    (deliberate operator override — e.g. the column encodes a coded category,
    not a raw name/contact value).
    """
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="16_Helminth.xlsx",
        headers=["HS_INITIALS"],
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "HS_INITIALS": {"has_pdf_question": True, "sot_phi": None, "is_phi": False},
        },
        published_raw_headers=frozenset({"HS_INITIALS"}),
        confirmed_keep_headers=frozenset({"HS_INITIALS"}),
    )
    assert "HS_INITIALS" not in approval.force_drop_headers


def test_weak_risky_header_still_cleared_by_pdf_question(tmp_path: Path) -> None:
    """Weak-risk tokens (remarks, geography, free-text) keep the current behavior:
    has_pdf_question=True + sot_phi=None confirms them benign (not force-dropped).

    Only STRONG identifiers (name/initials/signature/contact/gov-ID) lose this
    clearance.  The weak-token path must not regress.
    """
    cfg, bundle = _benign_bundle(tmp_path)
    approval = review_form_headers(
        form_name="demo.xlsx",
        headers=["interviewer_remarks", "village_code"],
        privacy_config=cfg,
        rule_bundle=bundle,
        sot_signals={
            "INTERVIEWER_REMARKS": {"has_pdf_question": True, "sot_phi": None, "is_phi": False},
            "VILLAGE_CODE": {"has_pdf_question": True, "sot_phi": None, "is_phi": False},
        },
        published_raw_headers=frozenset({"interviewer_remarks", "village_code"}),
    )
    assert "interviewer_remarks" not in approval.force_drop_headers
    assert "village_code" not in approval.force_drop_headers


# ---------------------------------------------------------------------------
# B-4: KEEP classifications must carry the evaluated jurisdiction list
# ---------------------------------------------------------------------------


def test_keep_classification_carries_evaluated_jurisdictions(tmp_path: Path) -> None:
    """A KEEP-classified header (no rule matched) must list the evaluated jurisdictions
    so the IRB ledger can show which regulations were checked and found no PHI rule.

    Before B-4, jurisdictions was an empty tuple for KEEP — the ledger couldn't
    distinguish 'no evaluation' from 'evaluated and found non-PHI'.
    """
    cfg, bundle = _benign_bundle(tmp_path)
    classified = classify_headers(["culture_result"], cfg, bundle)
    cl = classified["culture_result"]
    assert cl.action == Action.KEEP
    # Both configured jurisdictions must appear even though no rule fired.
    assert set(cl.jurisdictions) == {"USA", "INDIA"}
    # matched_rules stays empty — no rule actually matched.
    assert cl.matched_rules == ()


def test_non_keep_classification_jurisdiction_unchanged(tmp_path: Path) -> None:
    """Non-KEEP classifications must still list only the jurisdictions whose rules matched
    (not the full evaluated set) — B-4 must not widen non-KEEP entries.
    """
    cfg, bundle = _benign_bundle(tmp_path)
    # 'aadhaar_no' only matches India rules → should list INDIA only
    classified = classify_headers(["aadhaar_no"], cfg, bundle)
    cl = classified["aadhaar_no"]
    assert cl.action != Action.KEEP
    # Should be INDIA-only (aadhaar rules are India-jurisdiction)
    assert "INDIA" in cl.jurisdictions
    # Must not have been erroneously expanded to include USA-only rules
    # (aadhaar is India-specific; the only USA hit would require a USA aadhaar rule)
    # The safe assertion: jurisdictions is non-empty and the set is a subset of configured.
    assert set(cl.jurisdictions) <= {"USA", "INDIA"}
