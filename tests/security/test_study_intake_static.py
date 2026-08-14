"""Static analysis tests: data-isolation invariant enforcement in Tier-1/Tier-2 modules.

Tier 1 ("header-only", ``TIER1_HEADER_ONLY``) may open a raw dataset but must stop
after row 1 — no pandas, no ``.comment`` access, no ``csv.Sniffer``, XLSX streamed
via ``zipfile``/``ElementTree.iterparse`` with an explicit stop-after-row-1 guard,
CSV read via a single ``next(reader)`` inside a context manager. Three of the four
sot-lean-generator scripts never open a dataset at all, so the format-specific
checks (XLSX streaming/guard, CSV single-``next()``) apply only when the module's
source actually references that format; the format-agnostic checks (no pandas, no
``.comment``, no ``Sniffer``) still apply to every Tier-1 module unconditionally.

Tier 2 ("trusted value path", ``TIER2_TRUSTED_VALUE_PATH``) may read row 2+
internally, but a value must never reach a file, log, exception message, or
stdout. That check reuses the row-value AST visitor already implemented in
``test_static_analysis.py`` rather than reimplementing it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.security.test_static_analysis import _line_above_has_allow, _subtree_has_row_value

REPO_ROOT = Path(__file__).resolve().parents[2]

# Tier 1 — header-only: may open a raw dataset but MUST stop after row 1.
TIER1_HEADER_ONLY = [
    "scripts/source_truth/study_intake.py",
    "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/extract_sources.py",
    "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/check_lean_policy.py",
    "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/generate_pdf_aware_candidate.py",
    "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/generate_joined_query_view.py",
]

# Tier 2 — trusted value path: may read row 2+ internally, but no value may
# reach a file, log, exception message, or stdout.
TIER2_TRUSTED_VALUE_PATH = [
    "plugins/report-ai-study-pipeline/skills/excel-duplicate-handler/scripts/merge_excel_duplicates.py",
]


def _resolve(rel_path: str) -> Path:
    """Resolve *rel_path* (repo-root-relative) against the repo root.

    ``__file__`` is tests/security/test_study_intake_static.py:
    parents[0] = tests/security, [1] = tests/, [2] = repo_root (RePORT-AI-Portal).
    """
    return REPO_ROOT / rel_path


def _read_source(rel_path: str) -> str:
    """Read *rel_path* as text, skipping the test if the module doesn't exist yet."""
    module_path = _resolve(rel_path)
    if not module_path.is_file():
        pytest.skip(f"{rel_path} does not exist yet")
    return module_path.read_text(encoding="utf-8")


def _references_xlsx(source: str) -> bool:
    """True when *source* actually opens an XLSX workbook."""
    return "zipfile.ZipFile" in source or ".xlsx" in source


def _has_first_row_guard(source: str) -> bool:
    """True when a streamed row-number check against "1" appears at or near a
    ``elem.attrib.get("r")`` read — whether inline (``if elem.attrib.get("r")
    != "1":``) or via an intermediate variable (``row_number =
    elem.attrib.get("r")`` then ``if row_number != "1":`` a couple of lines
    later).
    """
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if 'attrib.get("r")' not in line:
            continue
        window = "\n".join(lines[index : index + 3])
        if '!= "1"' in window or "!= '1'" in window:
            return True
    return False


class TestDataIsolationStaticGuards:
    """Verify that Tier-1 header-only modules enforce the row-2+ isolation invariant."""

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_no_pandas_import(self, rel_path: str) -> None:
        """Pandas allows iterating past row 1; must not be imported."""
        source = _read_source(rel_path)
        assert not re.search(r"^\s*(?:import|from)\s+pandas\b", source, re.MULTILINE), (
            f"{rel_path} must not import pandas (allows iteration past row 1)"
        )

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_no_comment_attribute(self, rel_path: str) -> None:
        """openpyxl cell.comment exists but is never accessed."""
        source = _read_source(rel_path)
        # Reject patterns like .comment or \.comment
        assert not re.search(r"\.comment\b", source), (
            f"{rel_path} must not access .comment attribute (off-sheet data vector)"
        )

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_no_sniffer(self, rel_path: str) -> None:
        """csv.Sniffer reads entire file to detect dialect; forbidden."""
        source = _read_source(rel_path)
        assert not re.search(r"\bSniffer\b", source), (
            f"{rel_path} must not use csv.Sniffer (reads entire file)"
        )

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_xlsx_uses_zip_streaming_not_openpyxl(self, rel_path: str) -> None:
        """XLSX header reads stream the workbook XML directly, not via row iterators."""
        source = _read_source(rel_path)
        if not _references_xlsx(source):
            pytest.skip(f"{rel_path} does not open an xlsx dataset")
        assert "zipfile.ZipFile" in source
        assert "ElementTree.iterparse" in source
        assert "openpyxl.load_workbook" not in source

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_xlsx_first_row_guard_present(self, rel_path: str) -> None:
        """The streamed XLSX parser must stop after row 1."""
        source = _read_source(rel_path)
        if not _references_xlsx(source):
            pytest.skip(f"{rel_path} does not open an xlsx dataset")
        assert _has_first_row_guard(source), (
            f"{rel_path} must guard the streamed XLSX row loop to stop after row 1"
        )
        assert "break" in source

    @pytest.mark.parametrize("rel_path", TIER1_HEADER_ONLY)
    def test_csv_reader_called_once(self, rel_path: str) -> None:
        """CSV reading must call next() exactly once, then close."""
        source = _read_source(rel_path)
        if 'suffix == ".csv"' not in source:
            pytest.skip(f"{rel_path} has no CSV-handling code path")
        # Find the csv reading section
        csv_section = re.search(r"if suffix == \".csv\".*?(?=if suffix in|$)", source, re.DOTALL)
        assert csv_section, f"{rel_path}: CSV reading section not found"
        csv_code = csv_section.group(0)
        # next(reader) should be called once
        next_calls = len(re.findall(r"\bnext\(", csv_code))
        assert next_calls >= 1, f"{rel_path}: CSV reading section must call next(reader) at least once"
        # Should close the file (either via context manager or explicit close)
        assert "with dataset.open" in csv_code, f"{rel_path}: CSV reading must use context manager"


# ---------------------------------------------------------------------------
# Tier 2 — trusted value path: internal row/cell access is allowed, but no
# value may reach print, a logging call, or a file write. This reuses the
# row-value AST visitor (`_subtree_has_row_value`, and its
# `# phi-static: allow row=keys-only` escape hatch via `_line_above_has_allow`)
# already implemented in test_static_analysis.py rather than reimplementing
# it. Only the sensitive-call predicate below is new, since it targets
# print/logging/write calls rather than json.dump/dumps.
# ---------------------------------------------------------------------------

_LOGGING_METHOD_NAMES = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
_FILE_WRITE_METHOD_NAMES = {"write", "writerow", "writerows", "write_text", "writelines"}
_SENSITIVE_CALL_ATTRS = _LOGGING_METHOD_NAMES | _FILE_WRITE_METHOD_NAMES


def _is_sensitive_call(node: ast.AST) -> bool:
    """True for print(...), a logging-method call, or a file-write call."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return True
    return isinstance(node.func, ast.Attribute) and node.func.attr in _SENSITIVE_CALL_ATTRS


def _sensitive_call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else node.func.attr  # type: ignore[union-attr]


def _walk_for_row_derived_sensitive_calls(path: Path) -> list[str]:
    """Flag print/logging/write calls whose argument is derived from a row or cell value."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not _is_sensitive_call(node):
            continue
        assert isinstance(node, ast.Call)
        args = [*node.args, *(kw.value for kw in node.keywords)]
        if any(_subtree_has_row_value(arg) for arg in args) and not _line_above_has_allow(
            src, node.lineno
        ):
            violations.append(
                f"{path}:{node.lineno} {_sensitive_call_name(node)} call "
                "with row/cell-derived argument"
            )
    return violations


@pytest.mark.parametrize("rel_path", TIER2_TRUSTED_VALUE_PATH)
def test_tier2_no_row_values_in_write_or_log_calls(rel_path: str) -> None:
    """Tier-2 modules may read row 2+ internally, but a value must never reach
    print, a logging call, or a file write."""
    path = _resolve(rel_path)
    if not path.is_file():
        pytest.skip(f"{rel_path} does not exist yet")
    violations = _walk_for_row_derived_sensitive_calls(path)
    assert not violations, "row/cell value reached a write or log call:\n" + "\n".join(violations)
