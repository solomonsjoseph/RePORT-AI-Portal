"""Per-form evidence pack ↔ SoT union test."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

import config
from scripts.security.phi_id_masker import mask_variable_id


def _form_to_sot_yaml() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for f in config.SOT_DIR.glob("*_policy.yaml"):
        files[f.stem.replace("_policy", "")] = f
    dataset_dir = config.SOT_DIR / "dataset_policies"
    if dataset_dir.is_dir():
        for f in dataset_dir.glob("*_policy.yaml"):
            files[f.stem.replace("_policy", "")] = f
    return files


def _sot_variable_ids(yaml_path: Path) -> set[str]:
    body = yaml.safe_load(yaml_path.read_text()) or {}
    variables = body.get("variables") or []
    if isinstance(variables, list):
        return {
            v.get("variable_id") for v in variables if isinstance(v, dict) and v.get("variable_id")
        }
    if isinstance(variables, dict):
        return set(variables.keys())
    return set()


def _evidence_variable_ids_unmasked(form: str, evidence_path: Path) -> set[str]:
    """Return cleartext set of variable_ids in the evidence pack, mapping masked → cleartext using the live key."""
    body = json.loads(evidence_path.read_text())
    sot_yaml = _form_to_sot_yaml().get(form)
    sot_vids = _sot_variable_ids(sot_yaml) if sot_yaml else set()
    masked_to_clear = {mask_variable_id(form, vid): vid for vid in sot_vids}
    out: set[str] = set()
    for var in body.get("variables") or []:
        vid = var.get("variable_id")
        if not vid:
            continue
        if var.get("id_masked"):
            cleartext = masked_to_clear.get(vid)
            if cleartext is not None:
                out.add(cleartext)
        else:
            out.add(vid)
    return out


@pytest.mark.skipif(
    not config.LLM_SOURCE_EVIDENCE_PACKS_DIR.is_dir(),
    reason="evidence_packs dir missing; run `make llm-source-build` first",
)
def test_every_form_has_one_evidence_pack() -> None:
    sot_forms = set(_form_to_sot_yaml().keys())
    pack_forms: Counter[str] = Counter()
    for f in config.LLM_SOURCE_EVIDENCE_PACKS_DIR.glob("*.json"):
        body = json.loads(f.read_text())
        if "form" in body and "variables" in body:  # per-form pack
            pack_forms[body["form"]] += 1
    missing = sot_forms - set(pack_forms.keys())
    extra = set(pack_forms.keys()) - sot_forms
    duplicates = {f: c for f, c in pack_forms.items() if c > 1}
    assert not missing, f"forms with no evidence pack: {sorted(missing)}"
    assert not extra, f"evidence packs with no SoT: {sorted(extra)}"
    assert not duplicates, f"forms with multiple packs: {duplicates}"


@pytest.mark.skipif(
    not config.LLM_SOURCE_EVIDENCE_PACKS_DIR.is_dir(),
    reason="evidence_packs dir missing",
)
def test_evidence_pack_variables_match_sot() -> None:
    forms = _form_to_sot_yaml()
    mismatches: list[str] = []
    for form, sot_yaml in forms.items():
        ep_path = config.LLM_SOURCE_EVIDENCE_PACKS_DIR / f"{form}.json"
        if not ep_path.is_file():
            continue  # caught by the test above
        sot_vids = _sot_variable_ids(sot_yaml)
        ep_vids = _evidence_variable_ids_unmasked(form, ep_path)
        if sot_vids != ep_vids:
            only_sot = sorted(sot_vids - ep_vids)
            only_ep = sorted(ep_vids - sot_vids)
            mismatches.append(f"{form}: only_sot={only_sot[:5]}... only_ep={only_ep[:5]}...")
    assert not mismatches, "\n".join(mismatches)
