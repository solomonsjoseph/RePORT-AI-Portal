"""Canonical artifact-version registry for RePORT AI Portal.

This module is the single supported source of truth for generated artifact,
schema, prompt, and API version identifiers in the single-study,
privacy-first, local-first runtime.

Design rules:
- Versions use semantic-version strings in ``MAJOR.MINOR.PATCH`` form.
- Each key maps to one artifact contract that can require rebuilds.
- The public registry exposed to callers is read-only.
- Callers should read versions from here, not duplicate literals elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

__all__ = [
    "VERSIONS",
    "ArtifactVersionError",
    "get_version",
    "snapshot_versions",
    "validate_versions",
]

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ArtifactVersionError(ValueError):
    """Raised when artifact-version keys or values are invalid."""


_VERSIONS: dict[str, str] = {
    # --- Data schemas ---
    "clean_jsonl_schema": "1.0.0",  # De-identified dataset JSONL record shape
}


def validate_versions(versions: Mapping[str, str] | None = None) -> None:
    """Validate artifact version keys and values.

    Args:
        versions: Mapping to validate. Defaults to the internal registry.

    Raises:
        ArtifactVersionError: If any key or version value is invalid.
    """
    target = _VERSIONS if versions is None else versions
    for key, value in target.items():
        if not isinstance(key, str) or not key.strip():
            raise ArtifactVersionError("Artifact version keys must be non-empty strings")
        if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
            raise ArtifactVersionError(
                f"Artifact version for {key!r} must be a semantic version string in MAJOR.MINOR.PATCH form"
            )


validate_versions()
VERSIONS: Mapping[str, str] = MappingProxyType(_VERSIONS)
"""Read-only artifact version map keyed by artifact contract name.

When a version here changes, previously generated artifacts for that contract
should be rebuilt.
"""


def get_version(name: str) -> str:
    """Return the registered version for one artifact contract."""
    if not isinstance(name, str) or not name.strip():
        raise ArtifactVersionError("Artifact version name must be a non-empty string")
    try:
        return VERSIONS[name]
    except KeyError as exc:
        raise ArtifactVersionError(f"Unknown artifact version key: {name}") from exc


def snapshot_versions() -> dict[str, str]:
    """Return a plain mutable snapshot of the current registry."""
    return dict(VERSIONS)
