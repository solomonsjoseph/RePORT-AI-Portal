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
        'STAGING_UMASK = 0o077\n_STAGING_DIR_MODE = 0o700\n',
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


def test_linter_exit_code_nonzero_on_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
