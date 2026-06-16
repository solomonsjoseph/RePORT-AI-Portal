"""Tests for the PHI Rulebook engine: versioned offline cache + drift (Wave 3 C2)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.security.phi_review import StudyPrivacyConfig, refresh_jurisdiction_rules
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
