"""Tests for source-pack extraction helpers."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).parents[3]
EXTRACT_SCRIPT = REPO_ROOT / "skills" / "sot-lean-generator" / "scripts" / "extract_sources.py"


def _load_extract_sources() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extract_sources_for_test", EXTRACT_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ghostscript_render_writes_one_png_per_pdf_page(monkeypatch, tmp_path: Path) -> None:
    """The authoritative render path must cover every page, not just page 1."""
    module = _load_extract_sources()
    pdf = tmp_path / "multi page.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    render_dir = tmp_path / "rendered"

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/gs" if name == "gs" else None)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        output_arg = next(part for part in cmd if part.startswith("-sOutputFile="))
        output_pattern = Path(output_arg.split("=", 1)[1])
        for page in range(1, 4):
            page_path = Path(str(output_pattern).replace("%03d", f"{page:03d}"))
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    renders = module._render_with_ghostscript(pdf, render_dir, page_count=3, dpi=600)

    assert renders == [
        str(render_dir / "multi page.pdf.page-001.png"),
        str(render_dir / "multi page.pdf.page-002.png"),
        str(render_dir / "multi page.pdf.page-003.png"),
    ]
    assert len(calls) == 1
    assert "-dFirstPage=1" in calls[0]
    assert "-dLastPage=3" in calls[0]
    assert any(part == f"-sOutputFile={render_dir / 'multi page.pdf.page-%03d.png'}" for part in calls[0])
