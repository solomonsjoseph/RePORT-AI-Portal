"""Tests for the PHI Rulebook engine: versioned offline cache + drift (Wave 3 C2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.security.phi_review import StudyPrivacyConfig, load_study_privacy_config, refresh_jurisdiction_rules
from scripts.security.phi_rulebook import (
    RULEBOOK_CACHE_VERSION,
    cache_filename,
    default_seed_dir,
    read_cache_entry,
    resolve_rulebook,
    write_cache_entry,
)

_JURIS = ("INDIA", "USA")


def _privacy_config(jurisdictions: tuple[str, ...] = _JURIS) -> StudyPrivacyConfig:
    return StudyPrivacyConfig(
        study_dir=Path("test"),
        jurisdictions=jurisdictions,
        rule_refresh="pinned_only",
        conflict_policy="strictest_wins",
        max_synthetic_attempts=1,
        approval_mode="hybrid",
        parallelism_mode="auto",
        data_as_of=None,
    )


def test_cache_filename_is_sorted_and_versioned() -> None:
    assert cache_filename(("USA", "INDIA")) == f"rulebook_v{RULEBOOK_CACHE_VERSION}_INDIA_USA.json"
    assert cache_filename(("INDIA", "USA")) == cache_filename(("USA", "INDIA"))


def test_committed_seed_exists_and_matches_pinned() -> None:
    """The committed v1 seed must equal the in-code pinned rules (no drift on a
    clean checkout)."""
    seed = read_cache_entry(default_seed_dir() / cache_filename(_JURIS), jurisdictions=_JURIS)
    assert seed is not None, "committed v1 seed missing for INDIA_USA"
    bundle = refresh_jurisdiction_rules(_privacy_config(), allow_network=False)
    assert seed["rules_sha256"] == bundle.rules_sha256


def test_first_run_uses_seed_no_drift(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    res = resolve_rulebook(
        _privacy_config(),
        allow_network=False,
        cache_dir=cache_dir,
        seed_dir=default_seed_dir(),
    )
    assert res.cache_status == "seed"
    assert not res.drift_detected
    # The resolve writes a live cache entry for next time.
    assert (cache_dir / cache_filename(_JURIS)).is_file()


def test_second_run_is_cache_hit(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    resolve_rulebook(
        _privacy_config(), allow_network=False, cache_dir=cache_dir, seed_dir=default_seed_dir()
    )
    res2 = resolve_rulebook(
        _privacy_config(), allow_network=False, cache_dir=cache_dir, seed_dir=default_seed_dir()
    )
    assert res2.cache_status == "cache_hit"
    assert not res2.drift_detected


def test_rebuilt_when_no_cache_or_seed(tmp_path: Path) -> None:
    res = resolve_rulebook(
        _privacy_config(),
        allow_network=False,
        cache_dir=tmp_path / "cache",
        seed_dir=tmp_path / "emptyseed",
    )
    assert res.cache_status == "rebuilt_no_cache"
    assert not res.drift_detected  # nothing to compare against
    assert res.baseline_sha256 is None


def test_drift_detected_against_tampered_baseline(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    # Seed a baseline with a deliberately wrong rules_sha256.
    bundle = refresh_jurisdiction_rules(_privacy_config(), allow_network=False)
    write_cache_entry(cache_dir, bundle, _JURIS)
    p = cache_dir / cache_filename(_JURIS)
    data = json.loads(p.read_text())
    data["rules_sha256"] = "0" * 64
    p.write_text(json.dumps(data), encoding="utf-8")

    res = resolve_rulebook(
        _privacy_config(), allow_network=False, cache_dir=cache_dir, seed_dir=tmp_path / "noseed"
    )
    assert res.drift_detected
    assert res.baseline_sha256 == "0" * 64


def test_read_cache_entry_rejects_version_mismatch(tmp_path: Path) -> None:
    p = tmp_path / cache_filename(_JURIS)
    p.write_text(
        json.dumps({"cache_version": 999, "jurisdictions": ["INDIA", "USA"], "rules_sha256": "x"}),
        encoding="utf-8",
    )
    assert read_cache_entry(p, jurisdictions=_JURIS) is None


def test_read_cache_entry_rejects_jurisdiction_mismatch(tmp_path: Path) -> None:
    p = tmp_path / cache_filename(_JURIS)
    p.write_text(
        json.dumps(
            {
                "cache_version": RULEBOOK_CACHE_VERSION,
                "jurisdictions": ["USA"],
                "rules_sha256": "x",
            }
        ),
        encoding="utf-8",
    )
    assert read_cache_entry(p, jurisdictions=_JURIS) is None


def test_read_cache_entry_corrupt_is_none(tmp_path: Path) -> None:
    p = tmp_path / cache_filename(_JURIS)
    p.write_text("{not json", encoding="utf-8")
    assert read_cache_entry(p, jurisdictions=_JURIS) is None


def test_cache_payload_is_value_free(tmp_path: Path) -> None:
    """The cache holds rule metadata only — ids/jurisdictions/actions/reasons."""
    bundle = refresh_jurisdiction_rules(_privacy_config(), allow_network=False)
    write_cache_entry(tmp_path, bundle, _JURIS)
    data = json.loads((tmp_path / cache_filename(_JURIS)).read_text())
    assert set(data) >= {"cache_version", "jurisdictions", "rules_sha256", "rules", "sources"}
    for rule in data["rules"]:
        # rule metadata only; no compiled patterns or study data
        assert set(rule) <= {"id", "jurisdiction", "action", "reason"}


def test_classification_gate_routes_through_resolve_rulebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wave B.4: publish classification gate uses resolve_rulebook (same path as P0)."""
    import config
    from scripts.skills.extract_to_llm_source import _run_form_approval_gate
    from tests.skills.conftest import patch_config

    study = "RulebookGate"
    patch_config(monkeypatch, tmp_path, study=study)
    study_raw = tmp_path / "data" / "raw" / study

    privacy_yaml = {
        "jurisdictions": ["USA", "INDIA"],
        "data_as_of": "2025-12-31",
        "rule_refresh": "online_preferred",
        "conflict_policy": "strictest_wins",
        "approval": {"mode": "hybrid", "max_synthetic_attempts": 1},
        "parallelism": {"mode": "auto"},
    }
    (study_raw / "_study_privacy.yaml").write_text(yaml.dump(privacy_yaml), encoding="utf-8")
    (study_raw / "_forms_manifest.yaml").write_text(
        yaml.dump({"required": [], "optional": [], "reject": []}),
        encoding="utf-8",
    )

    run_dir = config.OUTPUT_DIR / study / "runs" / "run_rulebook_gate"
    run_dir.mkdir(parents=True, exist_ok=True)

    captured: dict[str, bool] = {}

    def _recording_resolve(privacy_config, **kwargs):
        captured["allow_network"] = kwargs.get("allow_network", False)
        return resolve_rulebook(privacy_config, **kwargs)

    with patch("scripts.security.phi_rulebook.resolve_rulebook", side_effect=_recording_resolve):
        gate = _run_form_approval_gate(
            study=study,
            study_raw_dir=study_raw,
            run_dir=run_dir,
            max_workers=None,
        )

    assert captured.get("allow_network") is True
    assert gate.approval_report_path is not None
    payload = json.loads(gate.approval_report_path.read_text(encoding="utf-8"))
    privacy = load_study_privacy_config(study_raw)
    expected = resolve_rulebook(privacy, allow_network=True)
    assert payload["rule_bundle"]["rules_sha256"] == expected.bundle.rules_sha256


def test_snapshot_staleness_uses_resolve_rulebook_rules_sha256() -> None:
    """Wave B.5: evaluate_snapshot_staleness compares manifest rules_sha256 to resolve_rulebook."""
    from scripts.utils.snapshot import check_snapshot_staleness

    privacy = _privacy_config()
    current_sha = resolve_rulebook(privacy, allow_network=False).bundle.rules_sha256
    manifest = {
        "phi_rulebook_version": RULEBOOK_CACHE_VERSION,
        "phi_rulebook_rules_sha256": "0" * 64,
    }
    findings = check_snapshot_staleness(
        manifest,
        current_rulebook_version=RULEBOOK_CACHE_VERSION,
        current_key_fingerprint=None,
        current_input_components=None,
        current_rulebook_rules_sha256=current_sha,
    )
    assert any(f.trigger == "rulebook_update" for f in findings)
    assert findings[0].severity.value == "warn"
