"""Canonical project version metadata for RePORT AI Portal.

This module exposes the single supported source of truth for the repository
version string and its parsed tuple form.

Design rules:

- ``__version__`` is the only canonical version literal.
- ``__version_info__`` is derived from ``__version__`` and never maintained by hand.
- Version validation happens at import time and fails fast on invalid values.
- The accepted format here is the normal Semantic Versioning core form
  ``MAJOR.MINOR.PATCH``.

Notes:

- This module intentionally keeps zero external dependencies.
- Pre-release and build metadata are not accepted by this repository-level
  version constant.
- Major version zero indicates initial development under Semantic Versioning.
"""

from __future__ import annotations

import re

__all__ = ["__version__", "__version_info__"]

# Semantic Versioning normal version: X.Y.Z with no leading zeroes except zero itself.
_SEMVER_CORE_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

__version__: str = "0.21.1"
"""Canonical repository version in ``MAJOR.MINOR.PATCH`` form."""

_match = _SEMVER_CORE_RE.fullmatch(__version__)
if _match is None:
    raise ValueError(
        f"Invalid version format: {__version__!r}. Expected Semantic Versioning core "
        "format MAJOR.MINOR.PATCH with no leading zeroes."
    )

_major, _minor, _patch = (int(part) for part in _match.groups())
__version_info__: tuple[int, int, int] = (_major, _minor, _patch)
"""Parsed version tuple derived from ``__version__``."""
