"""Merge-on-approval helper.

Copies an approved YAML draft over the SoT YAML for the given form.
Leaves the evidence pack draft in place (Phase 2 picks it up).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def merge_approved_draft(
    form: str,
    draft_yaml_path: Path,
    draft_pack_path: Path,
    sot_dir: Path,
) -> None:
    sot_dir.mkdir(parents=True, exist_ok=True)
    try:
        yaml.safe_load(draft_yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Draft YAML is malformed for form {form!r}: {draft_yaml_path}"
        ) from exc
    target = sot_dir / f"{form}_policy.yaml"
    tmp = target.with_suffix(".yaml.tmp")
    shutil.copyfile(draft_yaml_path, tmp)
    tmp.replace(target)
    _LOG.info(
        "sot_merge.applied form=%s target=%s evidence_pack_path=%s",
        form,
        target,
        draft_pack_path,
    )
