"""Tests for config.py — configuration and path management."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from config import (
    StudyPackError,
    _get_env_bool,
    _get_env_int,
    detect_study_name,
    ensure_directories,
    resolve_study_pack,
    strict_study_detection_enabled,
)


class TestDetectStudyName:
    def test_finds_study_with_datasets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = tmp_path / "MyStudy" / "datasets"
        study.mkdir(parents=True)
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
        result = detect_study_name()
        assert result == "MyStudy"

    def test_no_valid_study_returns_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "EmptyDir").mkdir()
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
        result = detect_study_name()
        assert result == config.DEFAULT_DATASET_NAME

    def test_missing_raw_dir_returns_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "nonexistent")
        result = detect_study_name()
        assert result == config.DEFAULT_DATASET_NAME

    def test_strict_missing_raw_dir_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "nonexistent")
        with pytest.raises(RuntimeError, match="RAW_DATA_DIR missing"):
            detect_study_name(strict=True)

    def test_strict_no_valid_study_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "EmptyDir").mkdir()
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
        with pytest.raises(RuntimeError, match="No valid study"):
            detect_study_name(strict=True)

    def test_proxy_auth_does_not_make_detection_strict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "EmptyDir").mkdir()
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
        monkeypatch.setenv("REPORT_AI_AUTH_MODE", "proxy")
        monkeypatch.delenv("REPORT_AI_STRICT_STUDY_DETECTION", raising=False)

        assert detect_study_name() == config.DEFAULT_DATASET_NAME


class TestStrictStudyDetection:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORT_AI_STRICT_STUDY_DETECTION", raising=False)
        assert not strict_study_detection_enabled()

    def test_enabled_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPORT_AI_STRICT_STUDY_DETECTION", "1")
        assert strict_study_detection_enabled()


class TestGetEnvInt:
    def test_valid_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "42")
        assert _get_env_int("TEST_INT_VAR", 0) == 42

    def test_missing_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT_VAR_MISSING", raising=False)
        assert _get_env_int("TEST_INT_VAR_MISSING", 99) == 99

    def test_invalid_int_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_BAD", "abc")
        with pytest.raises(ValueError):
            _get_env_int("TEST_INT_BAD", 0)


class TestGetEnvBool:
    @pytest.mark.parametrize("val", ["true", "1", "yes", "on", "True", "YES"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("TEST_BOOL", val)
        assert _get_env_bool("TEST_BOOL", False) is True

    @pytest.mark.parametrize("val", ["false", "0", "no", "off"])
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("TEST_BOOL", val)
        assert _get_env_bool("TEST_BOOL", True) is False


class TestEnsureDirectories:
    def test_creates_required_directories(self, monkeypatch_config: Path) -> None:
        ensure_directories()
        # All expected dirs should exist after call
        assert config.TRIO_DATASETS_DIR.is_dir()
        assert config.STUDY_AUDIT_DIR.is_dir()

    def test_does_not_create_staging_dir(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Staging is managed per-run by main.py, not by ensure_directories().

        A stale workspace from a crashed previous run must be purged explicitly
        by _prepare_staging() / _publish_staging(), so ensure_directories() must
        not pre-create STUDY_STAGING_DIR or any of its children.
        """
        staging_root = monkeypatch_config / "tmp" / "TestStudy"
        monkeypatch.setattr(config, "STUDY_STAGING_DIR", staging_root)
        monkeypatch.setattr(config, "STAGING_DATASETS_DIR", staging_root / "datasets")
        monkeypatch.setattr(config, "STAGING_DICTIONARY_DIR", staging_root / "dictionary")

        assert not staging_root.exists()
        ensure_directories()
        # Positive sanity: audit dir still created…
        assert config.STUDY_AUDIT_DIR.is_dir()
        # …but the staging workspace and its children must NOT be created here.
        assert not staging_root.exists()
        assert not (staging_root / "datasets").exists()
        assert not (staging_root / "dictionary").exists()


class TestStagingPaths:
    def test_study_staging_dir_under_tmp(self) -> None:
        assert config.STUDY_STAGING_DIR == config.TMP_DIR / config.STUDY_NAME

    def test_staging_datasets_dir(self) -> None:
        assert config.STAGING_DATASETS_DIR == config.STUDY_STAGING_DIR / "datasets"

    def test_staging_dictionary_dir(self) -> None:
        assert config.STAGING_DICTIONARY_DIR == config.STUDY_STAGING_DIR / "dictionary"


class TestAuditReportPaths:
    """Only the dataset leg carries PHI → only its audit reports exist.

    Dictionary mappings and legacy PDF JSON compatibility helpers are
    content-only from the host side.
    """

    def test_dataset_report_path(self) -> None:
        assert (
            config.AUDIT_DATASET_REPORT_PATH
            == config.STUDY_AUDIT_DIR / "dataset_cleanup_report.json"
        )

    def test_scrub_report_path(self) -> None:
        assert config.AUDIT_SCRUB_REPORT_PATH == config.STUDY_AUDIT_DIR / "phi_scrub_report.json"

    def test_no_dict_or_pdf_audit_constants_defined(self) -> None:
        assert not hasattr(config, "AUDIT_DICTIONARY_REPORT_PATH")
        assert not hasattr(config, "AUDIT_PDFS_REPORT_PATH")


class TestConstants:
    def test_base_dir_exists(self) -> None:
        assert config.BASE_DIR.is_dir()

    def test_study_name_is_string(self) -> None:
        assert isinstance(config.STUDY_NAME, str)
        assert len(config.STUDY_NAME) > 0


def test_legacy_constants_point_to_llm_source() -> None:
    """Phase 5b: legacy trio_bundle constants must now point under llm_source/."""
    import config

    assert "trio_bundle" not in str(config.TRIO_DATASETS_DIR), (
        f"TRIO_DATASETS_DIR still under trio_bundle/: {config.TRIO_DATASETS_DIR}"
    )
    assert str(config.TRIO_DATASETS_DIR).endswith("llm_source/dataset_schema/files")
    assert "trio_bundle" not in str(config.DICTIONARY_JSON_OUTPUT_DIR), (
        f"DICTIONARY_JSON_OUTPUT_DIR still under trio_bundle/: {config.DICTIONARY_JSON_OUTPUT_DIR}"
    )
    assert str(config.DICTIONARY_JSON_OUTPUT_DIR).endswith("llm_source/dictionary_mapping/jsonl")


class TestResolveStudyPack:
    """Study packs are swappable, not decorative: no fallback to another
    study's rules — a missing pack must raise, never silently substitute."""

    def test_resolves_bundled_indo_vap_pack(self) -> None:
        pack_dir = resolve_study_pack("Indo-VAP")
        assert (pack_dir / "pack.yaml").is_file()
        assert (pack_dir / "phi_scrub.yaml").is_file()

    def test_explicit_pack_dir_wins(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom_pack"
        custom.mkdir()
        (custom / "pack.yaml").write_text("name: Custom\n", encoding="utf-8")
        assert resolve_study_pack("AnyStudy", pack_dir=custom) == custom

    def test_env_var_wins_over_default_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_pack = tmp_path / "env_pack"
        env_pack.mkdir()
        (env_pack / "pack.yaml").write_text("name: EnvPack\n", encoding="utf-8")
        monkeypatch.setenv("REPORT_AI_STUDY_PACK_DIR", str(env_pack))
        assert resolve_study_pack("Indo-VAP") == env_pack

    def test_missing_study_raises_naming_study_and_directories_tried(self) -> None:
        with pytest.raises(StudyPackError) as exc_info:
            resolve_study_pack("Nonexistent-Study")
        message = str(exc_info.value)
        assert "Nonexistent-Study" in message
        assert "study_packs" in message

    def test_no_fallback_to_another_study(self, tmp_path: Path) -> None:
        """Only an Indo-VAP pack exists; running study 'Foo' must not
        silently resolve to Indo-VAP's rules."""
        with pytest.raises(StudyPackError, match="Foo"):
            resolve_study_pack("Foo")
