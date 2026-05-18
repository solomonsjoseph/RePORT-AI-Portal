"""Canonical scripts package for RePORT AI Portal.

This package exposes the narrow top-level processing boundary for the active
single-study, privacy-first, local-first runtime. Most runtime logic now lives
in focused subpackages such as ``scripts.extraction`` and ``scripts.security``.

Top-level public API:
- ``load_study_dictionary``: load the study dictionary into clean JSONL outputs
- ``extract_datasets``: extract raw tabular datasets directly into
  ``output/{STUDY}/llm_source/dataset_schema/files/``
- ``__version__``: package version marker from the repository root

Design rules:
- Top-level imports stay lazy so importing ``scripts`` does not force heavy
  optional dependencies.
- Only the current top-level runtime surface is exported here.
- Extraction functions resolve from ``scripts.extraction.*``, not legacy
  flat-module paths.
- Unknown attributes must raise a normal ``AttributeError``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "__version__",
    "extract_datasets",
    "load_study_dictionary",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve the supported top-level scripts API."""
    if name == "load_study_dictionary":
        from .extraction.load_dictionary import load_study_dictionary

        return load_study_dictionary
    if name == "extract_datasets":
        from .extraction.dataset_pipeline import extract_datasets

        return extract_datasets
    if name == "__version__":
        from __version__ import __version__

        return __version__
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return the stable public surface for interactive discovery."""
    return sorted(__all__)
