"""Tests for scripts/artifact_versions.py — version registry."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from scripts.artifact_versions import (
    VERSIONS,
    ArtifactVersionError,
    get_version,
    snapshot_versions,
    validate_versions,
)


class TestVersionsConstant:
    def test_versions_is_read_only(self) -> None:
        assert isinstance(VERSIONS, MappingProxyType)
        with pytest.raises(TypeError):
            VERSIONS["new_key"] = "1.0.0"  # type: ignore[index]

    def test_versions_not_empty(self) -> None:
        assert len(VERSIONS) > 0

    def test_all_values_are_strings(self) -> None:
        for key, val in VERSIONS.items():
            assert isinstance(key, str)
            assert isinstance(val, str)


class TestGetVersion:
    def test_valid_key(self) -> None:
        key = next(iter(VERSIONS))
        result = get_version(key)
        assert isinstance(result, str)
        assert result == VERSIONS[key]

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ArtifactVersionError):
            get_version("nonexistent_artifact_key")


class TestValidateVersions:
    def test_valid_versions_pass(self) -> None:
        validate_versions()  # Should not raise

    def test_invalid_semver_raises(self) -> None:
        with pytest.raises(ArtifactVersionError):
            validate_versions({"bad_key": "not-a-semver"})


class TestSnapshotVersions:
    def test_returns_mutable_copy(self) -> None:
        snap = snapshot_versions()
        assert isinstance(snap, dict)
        assert dict(VERSIONS) == snap
        # Mutating snapshot does not affect VERSIONS
        snap["extra"] = "9.9.9"
        assert "extra" not in VERSIONS
