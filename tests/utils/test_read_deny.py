"""Read-deny helper — `os.access(path, os.R_OK)` returns False inside the with-block.

Note: tests assume non-root execution. chmod 000 has no effect for root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.utils.read_deny import read_deny

_skip_as_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod-based denial has no effect for root",
)


@_skip_as_root
def test_read_deny_blocks_os_access(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text('{"forbidden": "value"}\n')
    assert os.access(target, os.R_OK)
    with read_deny([target]):
        assert not os.access(target, os.R_OK), "read-deny should make os.R_OK return False"


def test_read_deny_restores_on_exit(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text("payload\n")
    with read_deny([target]):
        pass
    assert os.access(target, os.R_OK)


@_skip_as_root
def test_read_deny_recursive_dir(tmp_path: Path) -> None:
    """Enforcing on a directory blocks reads of files inside it."""
    d = tmp_path / "row_jsonls"
    d.mkdir()
    inner = d / "form.jsonl"
    inner.write_text("payload\n")
    with read_deny([d]):
        assert not os.access(inner, os.R_OK) or not os.access(d, os.R_OK)


def test_read_deny_round_trip_preserves_content(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    payload = '{"x": 1}\n'
    target.write_text(payload)
    with read_deny([target]):
        pass
    assert target.read_text() == payload


def test_read_deny_restores_even_when_block_raises(tmp_path: Path) -> None:
    target = tmp_path / "secret.jsonl"
    target.write_text("payload\n")
    try:
        with read_deny([target]):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert os.access(target, os.R_OK)


def test_read_deny_restores_dir_children(tmp_path: Path) -> None:
    """Restore must reach files inside a mode-0 directory.

    Walking a chmod-0 directory yields no children via rglob (the dir is
    unreadable), so a naive restore that re-walks misses them. The CM
    records modes before any chmod runs and replays from the recorded
    map on exit, so children come back.
    """
    d = tmp_path / "row_jsonls"
    d.mkdir()
    a = d / "a.jsonl"
    b = d / "b.jsonl"
    a.write_text("a\n")
    b.write_text("b\n")
    with read_deny([d]):
        pass
    assert os.access(a, os.R_OK)
    assert os.access(b, os.R_OK)
    assert a.read_text() == "a\n"
    assert b.read_text() == "b\n"
