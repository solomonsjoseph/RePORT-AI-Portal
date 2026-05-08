"""file_access denies any path that resolves into output/*/audit/."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ai_assistant.file_access import validate_agent_read, validate_agent_write


def test_validate_agent_read_rejects_audit_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Mini" / "audit"
    audit_dir.mkdir(parents=True)
    secret = audit_dir / "lineage_manifest.json"
    secret.write_text("{}")
    with pytest.raises(PermissionError):
        validate_agent_read(secret)


def test_validate_agent_write_rejects_audit_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Mini" / "audit"
    audit_dir.mkdir(parents=True)
    target = audit_dir / "ledger.json"
    with pytest.raises(PermissionError):
        validate_agent_write(target)
