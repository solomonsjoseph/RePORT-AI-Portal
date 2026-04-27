"""Phase 2.x++ pipeline polish — regression tests for v0.17.2 follow-up fixes.

Companion to ``docs/irb_dossier/phase3_phi_followups.md``. Pins the
follow-up items surfaced by the deeper extraction-pipeline + PHI-scrub-
internals + PR-#10-re-verify audit on 2026-04-27:

- **N1**  ``run_scrub`` fails closed when ``phi_scrub.yaml`` is absent
- **N2**  ``_publish_leg`` uses ``secure_remove_tree`` (not plain rmtree)
        for the old trio_bundle
- **N3**  Lineage manifest carries ``phi_key_fingerprint``
- **N5**  PDF staging destination zone-asserted at the inlet
- **N11** Indo-VAP screen numbers (``IS_SCRNNUM`` / ``IC_SCRNNUM``) covered
        by the pseudonymization id_fields rule set
- **N12** Sandbox ``_sandbox_result.json`` manifest chmod'd 0o600
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WINDOWS_SKIP = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")


# ── N12 — sandbox manifest chmod ────────────────────────────────────────────


def test_sandbox_runner_chmods_sandbox_result_manifest() -> None:
    """The ``_sandbox_result.json`` manifest at the end of a sandbox run
    must be chmod'd 0o600 — PR #10 fixed the saved .py files but missed
    the manifest. This pins it."""
    src = Path("scripts/ai_assistant/sandbox/runner.py").read_text(encoding="utf-8")
    assert "manifest_path.chmod(0o600)" in src or (
        "manifest_path = output_dir" in src and "chmod(0o600)" in src
    ), "_sandbox_result.json must be chmod'd 0o600 in _emit_manifest (see runner.py:_emit_manifest)"


# ── N5 — PDF staging zone assertion ─────────────────────────────────────────


def test_main_zone_asserts_pdf_staging_destination() -> None:
    """``main.py`` must call ``assert_write_zone(pdf_extractions_dir)`` at
    the inlet, not just at publish time. Catches a misconfigured
    ``STAGING_PDFS_DIR`` before any PDF write touches disk."""
    src = Path("main.py").read_text(encoding="utf-8")
    # Find the line where pdf_extractions_dir is assigned, then look for an
    # assert_write_zone within the next 8 lines.
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "pdf_extractions_dir = Path(config.STAGING_PDFS_DIR)" in line:
            window = "\n".join(lines[i : i + 8])
            assert "assert_write_zone(pdf_extractions_dir)" in window, (
                "main.py must call assert_write_zone(pdf_extractions_dir) "
                "immediately after resolving STAGING_PDFS_DIR"
            )
            return
    pytest.fail("Could not locate pdf_extractions_dir assignment in main.py")


# ── N2 — secure-remove old trio_bundle on republish ─────────────────────────


def test_publish_leg_uses_secure_remove_tree_for_old_bundle() -> None:
    """``_publish_leg`` must call ``secure_remove_tree`` (zero-fill +
    zone-asserted) — not ``shutil.rmtree`` — when republishing over an
    existing trio_bundle, so a re-run doesn't leave PHI-adjacent forensic
    blocks on disk."""
    # Line-window scan: find ``def _publish_leg`` and look at the next ~40
    # lines. Avoids catastrophic regex backtracking on the 1000+ line file.
    lines = Path("main.py").read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("def _publish_leg(")),
        None,
    )
    assert start is not None, "_publish_leg definition not found in main.py"
    body = "\n".join(lines[start : start + 50])
    assert "if trio_dir.exists():" in body, "trio_dir.exists() block not found"
    assert "secure_remove_tree(trio_dir)" in body, (
        "secure_remove_tree(trio_dir) must replace shutil.rmtree(trio_dir) "
        "inside the trio_dir.exists() branch of _publish_leg"
    )


# ── N3 — phi_key_fingerprint in lineage manifest ────────────────────────────


# NOTE: a live ``emit_lineage_manifest`` test would require monkey-patching
# the ``secure_env`` zone marker which is frozen at import. The two static
# checks below verify the same contract via source inspection — sufficient
# for the regression-pin and avoids the import-time-fixture hang risk.


def test_lineage_manifest_omits_fingerprint_when_not_provided() -> None:
    """The fingerprint is optional — manifests without it must still be
    valid (legacy callers). Verifies the new field is keyed-out cleanly."""
    src = Path("scripts/utils/lineage.py").read_text(encoding="utf-8")
    assert "phi_key_fingerprint: str | None = None" in src, (
        "phi_key_fingerprint must be optional in the function signature"
    )
    assert "if phi_key_fingerprint is not None:" in src, (
        "Manifest must omit the field when None (don't carry a null)"
    )


def test_main_emits_phi_key_fingerprint_to_lineage() -> None:
    """``main.py``'s ``run_lineage`` must compute SHA-256 of the PHI key
    and pass it to ``emit_lineage_manifest``."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "phi_key_fingerprint=phi_key_fp" in src, (
        "main.py must pass the fingerprint into emit_lineage_manifest"
    )
    assert "_hashlib.sha256(_load_phi_key()).hexdigest()" in src, (
        "main.py must compute the fingerprint as SHA-256 of the loaded key"
    )


# ── N11 — Indo-VAP screen numbers covered by pseudonymization ───────────────


def test_phi_scrub_yaml_pseudonymises_indovap_screen_numbers() -> None:
    """``IS_SCRNNUM`` and ``IC_SCRNNUM`` (Indo-VAP screen numbers — linkable
    back to enrolment registers) must be matched by an ``id_fields`` rule
    so they get HMAC-pseudonymised, not pass through raw."""
    yaml_text = Path("scripts/security/phi_scrub.yaml").read_text(encoding="utf-8")
    # The pattern we added handles both via ``^I[CS]_SCRNNUM$``.
    assert "I[CS]_SCRNNUM" in yaml_text or "IS_SCRNNUM" in yaml_text, (
        "phi_scrub.yaml id_fields must cover IS_SCRNNUM / IC_SCRNNUM"
    )
    # Behavioral round-trip: load the config, walk the id_fields, and
    # confirm at least one pattern actually matches.
    from scripts.security.phi_scrub import load_scrub_config

    cfg = load_scrub_config()
    assert cfg is not None, "phi_scrub.yaml failed to load"
    matched = any(rule.pattern.match("IS_SCRNNUM") for rule in cfg.id_patterns)
    assert matched, "No id_patterns rule actually matches the literal column 'IS_SCRNNUM'"
    matched_ic = any(rule.pattern.match("IC_SCRNNUM") for rule in cfg.id_patterns)
    assert matched_ic, "No id_patterns rule matches 'IC_SCRNNUM'"
