"""Sandbox execution result contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SandboxResult:
    """Structured outcome of a single ``run_in_subprocess`` call.

    The orchestrator builds this from the child's stdout/stderr pipes plus the
    JSON manifest the child writes to ``output_dir/_sandbox_result.json``. All
    paths in ``figure_paths`` and ``code_paths`` are validated to live inside
    ``output_dir`` before being included here.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    oom_killed: bool = False
    truncated: bool = False
    figure_paths: list[Path] = field(default_factory=list)
    code_paths: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed
