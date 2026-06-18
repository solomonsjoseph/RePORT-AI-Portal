"""Canonical paths for maintainer-facing human-review artifacts.

All count-only review reports live under ``output/{study}/audit/human_review/``
with category sub-trees. This module is the single source of truth for those
paths; writers and readers must import helpers from here rather than assembling
paths ad hoc.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "HUMAN_REVIEW_ROOT",
    "LEGACY_SOT_REVIEW_DIR",
    "dataset_jsonl_union_review_path",
    "excel_duplicate_review_path",
    "human_review_root",
    "is_sot_review_report_path",
    "legacy_sot_review_report_path",
    "publish_sot_joined_gate_md_path",
    "resolve_sot_review_report_path",
    "safe_review_slug",
    "sot_review_report_path",
]

HUMAN_REVIEW_ROOT = "human_review"
LEGACY_SOT_REVIEW_DIR = "Sot_review"


def safe_review_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return slug or "unknown_form"


def human_review_root(audit_dir: Path) -> Path:
    return Path(audit_dir) / HUMAN_REVIEW_ROOT


def sot_review_report_path(audit_dir: Path, form: str) -> Path:
    return (
        human_review_root(audit_dir)
        / "sot"
        / safe_review_slug(form)
        / "review_report.md"
    )


def legacy_sot_review_report_path(audit_dir: Path, form: str) -> Path:
    return (
        Path(audit_dir)
        / LEGACY_SOT_REVIEW_DIR
        / safe_review_slug(form)
        / "review_report.md"
    )


def resolve_sot_review_report_path(audit_dir: Path, form: str) -> Path:
    """Return the canonical path, or the legacy file when only that exists."""
    canonical = sot_review_report_path(audit_dir, form)
    if canonical.is_file():
        return canonical
    legacy = legacy_sot_review_report_path(audit_dir, form)
    if legacy.is_file():
        return legacy
    return canonical


def is_sot_review_report_path(path: Path) -> bool:
    parts = path.parts
    if "human_review" in parts:
        try:
            idx = parts.index("human_review")
        except ValueError:
            return False
        return (
            idx + 3 < len(parts)
            and parts[idx + 1] == "sot"
            and path.name == "review_report.md"
        )
    return LEGACY_SOT_REVIEW_DIR in parts and path.name == "review_report.md"


def dataset_jsonl_union_review_path(audit_dir: Path, stem: str) -> Path:
    return (
        human_review_root(audit_dir)
        / "datasets"
        / safe_review_slug(stem)
        / "jsonl_union_review.md"
    )


def excel_duplicate_review_path(audit_dir: Path, group: str) -> Path:
    return (
        human_review_root(audit_dir)
        / "excel"
        / safe_review_slug(group)
        / "duplicate_review_report.md"
    )


def publish_sot_joined_gate_md_path(audit_dir: Path, run_id: str) -> Path:
    return (
        human_review_root(audit_dir)
        / "publish"
        / safe_review_slug(run_id)
        / "sot_joined_gate.md"
    )
