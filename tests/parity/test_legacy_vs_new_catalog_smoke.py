"""Parity smoke test: every variable_id in legacy variables.json should
appear in the new compact catalog with display_label and form populated.

Skipped when either artifact is absent (e.g., on a fresh checkout).
"""
import json
from pathlib import Path

import pytest

LEGACY_PATH = Path("output/Indo-VAP/trio_bundle/variables.json")
NEW_PATH = Path("output/Indo-VAP/llm_source/study_metadata_catalog.json")


@pytest.mark.skipif(
    not LEGACY_PATH.is_file() or not NEW_PATH.is_file(),
    reason="legacy or new artifact absent — run `make pipeline` first",
)
def test_every_legacy_variable_id_present_in_new_catalog():
    legacy = json.loads(LEGACY_PATH.read_text())
    new = json.loads(NEW_PATH.read_text())

    legacy_vids = {v["variable_name"] for v in legacy if isinstance(v, dict) and v.get("variable_name")}
    new_vids = {r["variable_id"] for r in new["compact_records"]}

    missing = legacy_vids - new_vids
    assert not missing, f"variables in legacy missing from new catalog: {sorted(missing)[:20]}"


@pytest.mark.skipif(
    not NEW_PATH.is_file(),
    reason="new catalog absent",
)
def test_new_catalog_records_have_display_label_and_form():
    new = json.loads(NEW_PATH.read_text())
    incomplete = [
        r["variable_id"]
        for r in new["compact_records"]
        if not r.get("display_label") or not r.get("form")
    ]
    assert not incomplete, f"records missing display_label or form: {incomplete[:20]}"
