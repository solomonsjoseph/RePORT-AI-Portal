"""Deterministic pre-publication scans for LLM-visible dataset artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scripts.security.phi_patterns import BLOCKING_PATTERNS, SUBJECT_ID_PATTERNS

__all__ = [
    "LeakScanFinding",
    "LeakScanResult",
    "scan_tree_for_phi",
]


@dataclass(frozen=True)
class LeakScanFinding:
    """A value-free leak-scan finding.

    The matched value is deliberately omitted so reports cannot become a PHI
    side channel.
    """

    relative_path: str
    line_number: int
    pattern_name: str


@dataclass(frozen=True)
class LeakScanResult:
    ok: bool
    findings: tuple[LeakScanFinding, ...]

    @property
    def detail(self) -> str:
        if self.ok:
            return ""
        first = self.findings[0]
        return (
            f"phi pattern {first.pattern_name} matched in "
            f"{first.relative_path} line {first.line_number} "
            "(matched content omitted)"
        )


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    return list(BLOCKING_PATTERNS) + [
        (f"SUBJECT_ID[{i}]", pattern) for i, pattern in enumerate(SUBJECT_ID_PATTERNS)
    ]


def scan_tree_for_phi(root: Path) -> LeakScanResult:
    """Scan a tree for blocking PHI patterns without returning matched values."""
    root = Path(root)
    if not root.is_dir():
        return LeakScanResult(ok=True, findings=())

    findings: list[LeakScanFinding] = []
    patterns = _patterns()
    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file():
            continue
        try:
            with fpath.open(encoding="utf-8", errors="replace") as fh:
                for line_number, line in enumerate(fh, start=1):
                    for pattern_name, pattern in patterns:
                        if pattern.search(line):
                            try:
                                relative_path = str(fpath.relative_to(root))
                            except ValueError:
                                relative_path = fpath.name
                            findings.append(
                                LeakScanFinding(
                                    relative_path=relative_path,
                                    line_number=line_number,
                                    pattern_name=pattern_name,
                                )
                            )
                            return LeakScanResult(ok=False, findings=tuple(findings))
        except OSError as exc:
            findings.append(
                LeakScanFinding(
                    relative_path=str(fpath),
                    line_number=0,
                    pattern_name=f"read_error:{exc.__class__.__name__}",
                )
            )
            return LeakScanResult(ok=False, findings=tuple(findings))

    return LeakScanResult(ok=True, findings=())
