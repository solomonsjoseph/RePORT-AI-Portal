"""Parity smoke test: every variable_id in legacy variables.json should
appear in the new compact catalog with display_label and form populated.

Skipped when either artifact is absent (e.g., on a fresh checkout).

NOTE (Phase 2): ``study_metadata_catalog.json`` was redesigned as a lean
ToC of forms pointing to per-form evidence packs (``forms`` dict mapping
form_name → {evidence_pack, variable_count}). Per-variable records moved
to ``evidence_packs/<form>.json``. Both tests below now traverse the
evidence packs via the ToC instead of relying on the legacy
``compact_records`` flat-array shape.
"""
import json
from pathlib import Path

import pytest

LEGACY_PATH = Path("output/Indo-VAP/trio_bundle/variables.json")
NEW_PATH = Path("output/Indo-VAP/llm_source/study_metadata_catalog.json")


def _iter_new_records():
    """Yield per-variable records from the Phase 2 lean ToC by following
    each form's ``evidence_pack`` pointer."""
    catalog = json.loads(NEW_PATH.read_text())
    base = NEW_PATH.parent
    for form_name, form_meta in catalog.get("forms", {}).items():
        pack_rel = form_meta.get("evidence_pack")
        if not pack_rel:
            continue
        pack_path = base / pack_rel
        if not pack_path.is_file():
            continue
        pack = json.loads(pack_path.read_text())
        for rec in pack.get("variables", []):
            # Inject form context for downstream checks.
            rec.setdefault("form", form_name)
            yield rec


@pytest.mark.skipif(
    not LEGACY_PATH.is_file() or not NEW_PATH.is_file(),
    reason="legacy or new artifact absent — run `make pipeline` first",
)
def test_every_legacy_variable_id_present_in_new_catalog():
    legacy = json.loads(LEGACY_PATH.read_text())

    legacy_vids = {
        v["variable_name"]
        for v in legacy
        if isinstance(v, dict) and v.get("variable_name")
    }
    new_vids = {r.get("variable_id") for r in _iter_new_records() if r.get("variable_id")}

    missing = legacy_vids - new_vids
    assert not missing, f"variables in legacy missing from new catalog: {sorted(missing)[:20]}"


@pytest.mark.skip(
    reason=(
        "Phase 2 redesign: display_label / form are no longer on per-variable "
        "evidence-pack records (those carry PHI-handling fields only). "
        "display_label lives in data_dictionary.json + dictionary_mapping; "
        "form is encoded by the evidence-pack filename. "
        "Re-implement against dictionary_mapping in Phase 5/6 if still needed."
    ),
)
def test_new_catalog_records_have_display_label_and_form():
    incomplete = [
        r.get("variable_id")
        for r in _iter_new_records()
        if not r.get("display_label") or not r.get("form")
    ]
    assert not incomplete, f"records missing display_label or form: {incomplete[:20]}"
