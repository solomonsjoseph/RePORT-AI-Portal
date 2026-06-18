"""Tests for PDF-aware SoT candidate field-count reconciliation (N3a).

Uses header/metadata-only source-pack fixtures — never dataset row values.
"""

# ruff: noqa: S108

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
_GENERATOR_PATH = (
    REPO
    / "plugins/report-ai-study-pipeline/skills/sot-lean-generator/scripts/generate_pdf_aware_candidate.py"
)
spec = importlib.util.spec_from_file_location("generate_pdf_aware_candidate", _GENERATOR_PATH)
assert spec is not None and spec.loader is not None
gpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpc)

PDF_FIELD_COUNT_MISMATCH = gpc.PDF_FIELD_COUNT_MISMATCH_KIND


def _field_count_discrepancy(
    *,
    headers: list[str],
    annotations: list[str],
    missing_headers: list[str] | None = None,
    form: str = "TestForm",
    annotation_aliases: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    header_set = set(headers)
    lower_to_header = {name.lower(): name for name in headers}
    aliases = dict(annotation_aliases or {})
    return gpc._pdf_field_count_mismatch_discrepancy(
        headers=headers,
        missing_headers=missing_headers
        if missing_headers is not None
        else [header for header in headers if header not in set(annotations)],
        annotation_labels=annotations,
        header_set=header_set,
        lower_to_header=lower_to_header,
        annotation_aliases=aliases,
        non_variable_annotations=set(gpc.NON_VARIABLE_ANNOTATIONS.get(form, set())),
        true_missing_annotations=set(
            gpc.TRUE_PDF_VARIABLES_WITHOUT_DATASET_HEADER.get(form, set())
        ),
    )


class TestPdfFieldCountMismatch:
    def test_system_column_does_not_trigger_field_count_mismatch(self) -> None:
        discrepancy = _field_count_discrepancy(
            headers=["VAR_A", "VAR_B", "Time_Stamp"],
            annotations=["VAR_A", "VAR_B"],
            missing_headers=[],
        )
        assert discrepancy is None

    def test_documented_missing_headers_do_not_trigger_field_count_mismatch(self) -> None:
        discrepancy = _field_count_discrepancy(
            headers=["VAR_A", "VAR_B", "VAR_C"],
            annotations=["VAR_A", "VAR_B"],
            missing_headers=["VAR_C"],
        )
        assert discrepancy is None

    def test_unaccounted_bindable_header_triggers_field_count_mismatch(self) -> None:
        discrepancy = _field_count_discrepancy(
            headers=["VAR_A", "VAR_B", "VAR_C"],
            annotations=["VAR_A", "VAR_B"],
            missing_headers=["VAR_B"],
        )
        assert discrepancy is not None
        assert discrepancy["kind"] == PDF_FIELD_COUNT_MISMATCH
        assert discrepancy["dataset_column_binding"]["unaccounted_headers"] == ["VAR_C"]

    def test_curated_alias_reconciles_bindable_headers(self) -> None:
        discrepancy = _field_count_discrepancy(
            headers=["FC_PARAS4_4"],
            annotations=["FC_PARAS3_4"],
            missing_headers=["FC_PARAS4_4"],
            form="15_Feces",
            annotation_aliases={"FC_PARAS3_4": "FC_PARAS4_4"},
        )
        assert discrepancy is None


@pytest.mark.parametrize(
    ("form", "pack_path"),
    [
        ("6_HIV", Path("/tmp/sot_source_pack_6_HIV.json")),
        ("15_Feces", Path("/tmp/sot_source_pack_15_Feces.json")),
    ],
)
def test_indo_vap_regression_no_field_count_hold(form: str, pack_path: Path) -> None:
    if not pack_path.is_file():
        pytest.skip(f"source pack not present: {pack_path}")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    result = gpc.build_candidate(REPO, form, pack_path)
    kinds = [entry["kind"] for entry in result.get("discrepancies", [])]
    assert PDF_FIELD_COUNT_MISMATCH not in kinds

    header_set = set(pack["headers"])
    lower_to_header = {name.lower(): name for name in pack["headers"]}
    aliases = gpc._annotation_aliases_for(form, header_set)
    for label in {
        ann for page in pack.get("pages", []) for ann in page.get("annotations", []) or []
    }:
        if label not in header_set and label.lower() in lower_to_header:
            aliases[label] = lower_to_header[label.lower()]
    missing = [
        entry["dataset_column_binding"]
        for entry in result.get("discrepancies", [])
        if entry.get("kind") == "dataset_header_without_visible_pdf_widget"
    ]
    missing_headers = missing[0] if missing else []
    assert (
        gpc._pdf_field_count_mismatch_discrepancy(
            headers=pack["headers"],
            missing_headers=missing_headers,
            annotation_labels=[
                ann for page in pack.get("pages", []) for ann in page.get("annotations", []) or []
            ],
            header_set=header_set,
            lower_to_header=lower_to_header,
            annotation_aliases=aliases,
            non_variable_annotations=set(gpc.NON_VARIABLE_ANNOTATIONS.get(form, set())),
            true_missing_annotations=set(
                gpc.TRUE_PDF_VARIABLES_WITHOUT_DATASET_HEADER.get(form, set())
            ),
        )
        is None
    )
