"""Phase 4 config constants."""

from __future__ import annotations

from pathlib import Path

import config


def test_audit_no_llm_sentinel_name() -> None:
    assert config.AUDIT_NO_LLM_SENTINEL_NAME == ".NO_LLM_ZONE"


def test_audit_sentinel_alarm_path() -> None:
    p = config.AUDIT_SENTINEL_ALARM_PATH
    assert isinstance(p, Path)
    assert p.name == "audit_sentinel_alarms.jsonl"
    assert p.parent == config.TMP_DIR


def test_audit_no_llm_zone_attribute_name() -> None:
    """Custom .gitattributes attribute name."""
    assert config.AUDIT_NO_LLM_ZONE_ATTRIBUTE == "report-ai-portal-no-llm"
