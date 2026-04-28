"""Production wiring smoke checks that avoid live LLM calls."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.ai_assistant.ui.auth import evaluate_auth


def test_fixture_study_bundle_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "output" / "Fixture" / "trio_bundle"
    datasets = bundle / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "1_fixture.jsonl").write_text('{"row": 1}\n', encoding="utf-8")
    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", bundle)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", datasets)

    assert Path(config.TRIO_BUNDLE_DIR).is_dir()
    assert any(Path(config.TRIO_DATASETS_DIR).glob("*.jsonl"))


def test_chat_auth_proxy_configuration_is_enforceable() -> None:
    result = evaluate_auth(
        headers={"X-Forwarded-User": "operator", "X-Report-AI-Proxy-Secret": "secret"},
        ip="127.0.0.1",
        environ={
            "REPORT_AI_AUTH_MODE": "proxy",
            "REPORT_AI_PROXY_SHARED_SECRET": "secret",
        },
    )

    assert result.ok
    assert result.user == "operator"
