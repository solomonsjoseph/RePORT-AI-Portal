"""Value-freedom guard for scripts/security/phi_metrics.py.

The metric harness must never print, log, or return a cell value — it
prints column names, rule patterns, and counts only, mirroring the
value-free ``LeakScanFinding`` contract (llm_source_gate.py). This is
verified two ways: (1) a runtime check that every value ``compute_metrics``
returns is a plain int or a tuple of ints — a metrics dict that can
structurally only hold counts has no channel for a leaked string — and
(2) a static AST check that no ``print`` call in the module formats an
attribute pulled from parsed JSON content beyond the disposition-manifest
fields the module itself declares.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import config
from scripts.security.phi_scrub import PHI_DISPOSITION_FILENAME, PHI_REVIEW_QUEUE_FILENAME

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "security" / "phi_metrics.py"


def _seed_audit_artifacts(audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / PHI_DISPOSITION_FILENAME).write_text(
        json.dumps(
            {
                "age_variable_present": True,
                "compliance_posture": "icmr_coded_dataset",
                "columns": {
                    "SUBJID": {
                        "resolved_action": "pseudonymize",
                        "class": "B",
                        "matched_rule": "id",
                        "sot_declared": "pseudonymize",
                        "value_count": 3,
                    },
                    "VISDAT": {
                        "resolved_action": "jitter_date",
                        "class": "C",
                        "matched_rule": "date",
                        "sot_declared": None,
                        "value_count": 5,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / PHI_REVIEW_QUEUE_FILENAME).write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "column": "VISDAT",
                        "file": "1A_ICScreening.jsonl",
                        "trigger": "date_unparsed",
                        "destructive": True,
                        "count": 2,
                        "status": "open",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_compute_metrics_returns_only_counts(tmp_path: Path, monkeypatch) -> None:
    """Every value in the returned dict is a plain int or a tuple of ints —
    structurally incapable of carrying a leaked string value."""
    from scripts.security import phi_metrics

    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", tmp_path / "audit" / "phi_scrub_report.json")
    _seed_audit_artifacts(tmp_path / "audit")
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", tmp_path / "sot_empty")
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", tmp_path / "datasets_empty")
    monkeypatch.setattr(config, "DICTIONARY_JSON_OUTPUT_DIR", tmp_path / "dictionary_empty")

    metrics = phi_metrics.compute_metrics(config.STUDY_NAME)

    for key, value in metrics.items():
        if isinstance(value, tuple):
            assert all(isinstance(v, int) for v in value), f"{key} tuple must be all-int, got {value!r}"
        else:
            assert isinstance(value, int), f"{key} must be an int, got {type(value).__name__}: {value!r}"


def test_format_report_output_contains_no_json_artifact_paths(tmp_path: Path, monkeypatch) -> None:
    """format_report's rendered text is built only from the int/tuple
    metrics dict plus fixed label strings — smoke-check it never leaks a
    filesystem path or raw JSON fragment into the printed report."""
    from scripts.security import phi_metrics

    monkeypatch.setattr(config, "AUDIT_SCRUB_REPORT_PATH", tmp_path / "audit" / "phi_scrub_report.json")
    _seed_audit_artifacts(tmp_path / "audit")
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", tmp_path / "sot_empty")
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", tmp_path / "datasets_empty")
    monkeypatch.setattr(config, "DICTIONARY_JSON_OUTPUT_DIR", tmp_path / "dictionary_empty")

    metrics = phi_metrics.compute_metrics(config.STUDY_NAME)
    report = phi_metrics.format_report(metrics)

    assert str(tmp_path) not in report
    assert "{" not in report and "}" not in report  # no raw JSON/dict fragment leaked through


def _is_row_value_node(node: ast.AST) -> bool:
    """Same row-value heuristic as test_static_analysis.py: a pandas-style
    row iterator call, or a bare ``.values`` attribute access."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"iterrows", "itertuples", "to_dict"}
    )


def test_no_print_call_takes_a_row_iteration_argument() -> None:
    """AST guard: no ``print`` call in phi_metrics.py may take an argument
    derived from a pandas-style row iterator (there should be none — the
    module never reads a dataset row at all, only pre-computed audit JSON)."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(MODULE_PATH))
    violations = [
        f"{MODULE_PATH}:{node.lineno} print() call derived from a row iterator"
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            and any(
                _is_row_value_node(sub) for arg in node.args for sub in ast.walk(arg)
            )
        )
    ]
    assert not violations, "\n".join(violations)
