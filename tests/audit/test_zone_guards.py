"""Zone-guard helper - denies any audit-zone read."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit.zone_guards import deny_if_audit_zone


def test_allows_non_audit_path(tmp_path: Path) -> None:
    safe = tmp_path / "regular.json"
    safe.write_text("{}")
    deny_if_audit_zone(safe)  # must not raise


def test_denies_path_inside_audit_dir(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Mini" / "audit"
    audit_dir.mkdir(parents=True)
    target = audit_dir / "ledger.json"
    target.write_text("{}")
    with pytest.raises(PermissionError):
        deny_if_audit_zone(target)


def test_denies_symlink_escaping_into_audit_dir(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Mini" / "audit"
    audit_dir.mkdir(parents=True)
    real = audit_dir / "secret.json"
    real.write_text("{}")
    decoy = tmp_path / "decoy_link.json"
    decoy.symlink_to(real)
    with pytest.raises(PermissionError):
        deny_if_audit_zone(decoy)


def test_denies_when_attr_set_even_if_realpath_innocent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense in depth: attribute alone triggers deny."""
    target = tmp_path / "lookalike.json"
    target.write_text("{}")
    # Force the attr check to claim true
    def fake_attr(_p: Path) -> bool:
        return True
    monkeypatch.setattr("scripts.audit.zone_guards._has_no_llm_attribute", fake_attr)
    with pytest.raises(PermissionError):
        deny_if_audit_zone(target)


def test_attribute_check_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call for the same path doesn't re-run subprocess."""
    target = tmp_path / "regular.json"
    target.write_text("{}")
    calls = {"n": 0}
    def counted_subprocess(*args, **kwargs):
        calls["n"] += 1
        class R:
            stdout = "unspecified"
            returncode = 0
        return R()
    monkeypatch.setattr("scripts.audit.zone_guards.subprocess.run", counted_subprocess)
    # Clear cache to ensure fresh start
    from scripts.audit.zone_guards import _has_no_llm_attribute
    _has_no_llm_attribute.cache_clear()
    deny_if_audit_zone(target)
    deny_if_audit_zone(target)
    assert calls["n"] == 1, "git check-attr should be cached per-path"
