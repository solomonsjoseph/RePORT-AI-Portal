from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.security.phi_review import (
    Action,
    OfficialSourceRejected,
    classify_headers,
    is_phi_risky_header,
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
    assert approval.attempts == 5
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


def test_form_held_when_risky_keep_header_escapes_rules(tmp_path: Path) -> None:
    """A KEEP header with a PHI-risky name holds the form (Option C coverage hold)."""
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

    assert approval.status == "held"
    assert any(reason.startswith(PHI_COVERAGE_HOLD_PREFIX) for reason in approval.reasons)
    assert any("interviewer_remarks" in reason for reason in approval.reasons)
    # the escapee is classified KEEP — that is exactly why it must be held
    assert approval.actions["interviewer_remarks"] == Action.KEEP.value


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
