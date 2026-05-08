"""SourceTruthRetriever rejects any path that resolves into output/*/audit/."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_open_for_retrieval_rejects_audit_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Mini" / "audit"
    audit_dir.mkdir(parents=True)
    secret = audit_dir / "phi_id_mapping.json"
    secret.write_text('{"x": 1}')
    from scripts.source_truth.retrieval import _open_for_retrieval
    with pytest.raises(PermissionError):
        _open_for_retrieval(secret)


def test_open_for_retrieval_allows_non_audit_path(tmp_path: Path) -> None:
    safe = tmp_path / "regular.json"
    safe.write_text("{}")
    from scripts.source_truth.retrieval import _open_for_retrieval
    _open_for_retrieval(safe)
