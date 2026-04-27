"""Tests for scripts/utils/step_cache.py — pipeline step caching."""

from __future__ import annotations

from pathlib import Path

from scripts.utils.step_cache import (
    hash_directory,
    hash_file,
    is_step_fresh,
    save_step_manifest,
)


class TestHashFile:
    def test_known_content_known_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = hash_file(f)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        assert hash_file(f1) == hash_file(f2)

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert hash_file(f1) != hash_file(f2)


class TestHashDirectory:
    def test_deterministic_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "a.txt").write_text("a")
        h1 = hash_directory(tmp_path)
        h2 = hash_directory(tmp_path)
        assert h1 == h2

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")
        result = hash_directory(tmp_path)
        assert ".hidden" not in result

    def test_extension_filter(self, tmp_path: Path) -> None:
        (tmp_path / "data.csv").write_text("csv")
        (tmp_path / "notes.txt").write_text("txt")
        result = hash_directory(tmp_path, extensions=frozenset({".csv"}))
        assert "data.csv" in result or any("data.csv" in k for k in result)
        assert not any("notes.txt" in k for k in result)

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = hash_directory(tmp_path)
        assert result == {}


class TestSaveAndFresh:
    def test_save_then_fresh(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        out.mkdir()
        (out / "result.jsonl").write_text("{}")
        hashes = {"input.csv": "abc123"}
        save_step_manifest("test_step", out, hashes)
        assert is_step_fresh("test_step", out, hashes)

    def test_changed_input_not_fresh(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        out.mkdir()
        (out / "result.jsonl").write_text("{}")
        save_step_manifest("test_step", out, {"input.csv": "abc123"})
        assert not is_step_fresh("test_step", out, {"input.csv": "changed"})

    def test_missing_required_output_not_fresh(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        out.mkdir()
        hashes = {"input.csv": "abc123"}
        save_step_manifest("test_step", out, hashes)
        assert not is_step_fresh("test_step", out, hashes, required_outputs=["missing.jsonl"])

    def test_corrupt_manifest_not_fresh(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        out.mkdir()
        manifest = out / ".step_manifest_test_step.json"
        manifest.write_text("NOT VALID JSON {{{")
        assert not is_step_fresh("test_step", out, {"a": "b"})
