"""Read-deny helper — `os.access(path, os.R_OK)` returns False after enforcement.

Note: tests assume non-root execution. chmod 000 has no effect for root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.utils.read_deny import enforce_read_deny, restore_read_access


def test_enforce_read_deny_blocks_os_access(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text('{"forbidden": "value"}\n')
    assert os.access(target, os.R_OK)
    enforce_read_deny([target])
    try:
        assert not os.access(target, os.R_OK), "read-deny should make os.R_OK return False"
    finally:
        restore_read_access([target])


def test_restore_read_access_reverts(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text("payload\n")
    enforce_read_deny([target])
    restore_read_access([target])
    assert os.access(target, os.R_OK)


def test_enforce_read_deny_recursive_dir(tmp_path: Path) -> None:
    """Enforcing on a directory blocks reads of files inside it."""
    d = tmp_path / "row_jsonls"
    d.mkdir()
    inner = d / "form.jsonl"
    inner.write_text("payload\n")
    enforce_read_deny([d])
    try:
        # Either the dir or the file should be unreadable (or both).
        assert not os.access(inner, os.R_OK) or not os.access(d, os.R_OK)
    finally:
        restore_read_access([d])


def test_enforce_then_restore_round_trip_preserves_content(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    payload = '{"x": 1}\n'
    target.write_text(payload)
    enforce_read_deny([target])
    restore_read_access([target])
    # After restore, content is identical (chmod doesn't touch content).
    assert target.read_text() == payload


def test_enforce_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text("payload\n")
    enforce_read_deny([target])
    enforce_read_deny([target])  # second call must not error
    try:
        assert not os.access(target, os.R_OK)
    finally:
        restore_read_access([target])
