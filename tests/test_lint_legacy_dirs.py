"""Tests for the legacy-directory string linter."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_linter_passes_on_clean_file(tmp_path: Path) -> None:
    f = tmp_path / "clean.py"
    f.write_text('def func():\n    path = "llm_source/data.json"\n', encoding="utf-8")

    from scripts.lint_legacy_dirs import check_file

    violations = check_file(f)
    assert violations == []


def test_linter_catches_trio_bundle(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text('path = output_root / "trio_bundle" / "datasets"\n', encoding="utf-8")

    from scripts.lint_legacy_dirs import check_file

    violations = check_file(f)
    assert any("trio_bundle" in v for v in violations)


def test_linter_catches_human_review(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text('review_dir = output_root / "human_review"\n', encoding="utf-8")

    from scripts.lint_legacy_dirs import check_file

    violations = check_file(f)
    assert any("human_review" in v for v in violations)


def test_linter_ignores_staging_in_secure_staging_context(tmp_path: Path) -> None:
    """'staging' as a concept name (not an output path component) must not trigger."""
    f = tmp_path / "secure_staging.py"
    f.write_text(
        "STAGING_UMASK = 0o077\n_STAGING_DIR_MODE = 0o700\n",
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_catches_output_staging_path_pattern(tmp_path: Path) -> None:
    """output/*/staging as a path string must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text(
        'staging_dir = output_root / "staging" / "llm_source"\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    violations = check_file(f)
    assert any("staging" in v for v in violations)


def test_linter_exit_code_nonzero_on_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "bad.py").write_text('path = "trio_bundle/datasets"\n', encoding="utf-8")

    from scripts.lint_legacy_dirs import lint_scripts_dir

    rc = lint_scripts_dir(scripts_dir)
    assert rc != 0


def test_linter_exit_code_zero_on_clean_dir(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "good.py").write_text('path = "llm_source/datasets"\n', encoding="utf-8")

    from scripts.lint_legacy_dirs import lint_scripts_dir

    rc = lint_scripts_dir(scripts_dir)
    assert rc == 0


def test_linter_skips_tmp_dir_staging(tmp_path: Path) -> None:
    """TMP_DIR-rooted staging is the Phase 5a intent — must NOT trigger."""
    f = tmp_path / "ok.py"
    f.write_text(
        'staging_root = config.TMP_DIR / study / "staging"\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_skips_tmp_dir_human_review(tmp_path: Path) -> None:
    """TMP_DIR-rooted human_review is the Phase 5a intent — must NOT trigger."""
    f = tmp_path / "ok.py"
    f.write_text(
        'review_dir = config.TMP_DIR / study / "human_review"\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_skips_comments(tmp_path: Path) -> None:
    """Pure comment lines describing the legacy migration must NOT trigger."""
    f = tmp_path / "ok.py"
    f.write_text(
        "# Historical: this was written to trio_bundle/datasets/ before Phase 5b.\n"
        'path = "llm_source/datasets/x.json"\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_skips_docstrings(tmp_path: Path) -> None:
    """Triple-quoted docstrings mentioning legacy names must NOT trigger."""
    f = tmp_path / "ok.py"
    f.write_text(
        'def foo():\n    """Reads from trio_bundle/datasets/ — historical note."""\n    pass\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_skips_compound_identifier_human_review(tmp_path: Path) -> None:
    """``needs_human_review`` is a status string label, not a directory name."""
    f = tmp_path / "ok.py"
    f.write_text(
        'PDF_EVIDENCE_NEEDS_HUMAN_REVIEW = "needs_human_review"\n',
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []


def test_linter_skips_trio_bundle_dir_constant(tmp_path: Path) -> None:
    """``config.TRIO_BUNDLE_DIR`` remains allowed for rollback compatibility."""
    f = tmp_path / "ok.py"
    f.write_text(
        "live_trio = Path(config.TRIO_BUNDLE_DIR)\n",
        encoding="utf-8",
    )
    from scripts.lint_legacy_dirs import check_file

    assert check_file(f) == []
