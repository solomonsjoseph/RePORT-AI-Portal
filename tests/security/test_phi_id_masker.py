"""HMAC-SHA256 variable-id masker — Phase 1 helper, reused by Phase 3."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.security.phi_id_masker import (
    PHIIdMaskerError,
    mask_variable_id,
)

_HEX_TOKEN = re.compile(r"^[0-9a-f]{12}$")


def _write_key(keyfile: Path, byte: int) -> None:
    """Write a 32-byte HMAC key in the hex-text format ``load_key`` expects."""
    keyfile.write_text(bytes([byte] * 32).hex(), encoding="utf-8")
    keyfile.chmod(0o600)


def test_returns_12_hex_chars(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0x00)
    token = mask_variable_id("1A_ICScreening", "IC_BIRTHDAT", key_path=keyfile)
    assert _HEX_TOKEN.match(token), f"expected 12 hex chars, got {token!r}"


def test_deterministic(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0xAB)
    a = mask_variable_id("1A_ICScreening", "IC_BIRTHDAT", key_path=keyfile)
    b = mask_variable_id("1A_ICScreening", "IC_BIRTHDAT", key_path=keyfile)
    assert a == b


def test_form_is_domain_separator(tmp_path: Path) -> None:
    keyfile = tmp_path / "phi.key"
    _write_key(keyfile, 0xAB)
    a = mask_variable_id("1A_ICScreening", "IC_BIRTHDAT", key_path=keyfile)
    b = mask_variable_id("1B_HCScreening", "IC_BIRTHDAT", key_path=keyfile)
    assert a != b


def test_missing_key_raises(tmp_path: Path) -> None:
    with pytest.raises(PHIIdMaskerError):
        mask_variable_id("F", "V", key_path=tmp_path / "does_not_exist.key")
