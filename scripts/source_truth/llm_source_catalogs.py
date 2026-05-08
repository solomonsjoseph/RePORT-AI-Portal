"""Lean-ToC writers for llm_source dictionary_mapping.

Three eventual catalogs (Tasks 5, 6 add the rest):
- ``dictionary_mapping/catalog.json`` — pointers to per-form JSONL files
  under ``dictionary_mapping/jsonl/``
- ``dataset_schema/catalog.json`` — Task 6
- ``study_metadata_catalog.json`` — Task 5

Every catalog stores pointers only — never inline payloads.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_SCHEMA_VERSION = 1


def relocate_dictionary(
    *,
    legacy_dir: Path | None = None,
    new_jsonl_dir: Path | None = None,
) -> int:
    """Copy the per-form dictionary tree from ``trio_bundle/dictionary/`` to
    ``llm_source/dictionary_mapping/jsonl/``, preserving the subdir+jsonl
    shape. Atomic per file. Idempotent. Legacy is preserved (Phase 5
    deletes it). Returns the number of files copied.
    """

    legacy_dir = legacy_dir if legacy_dir is not None else config.DICTIONARY_JSON_OUTPUT_DIR
    new_jsonl_dir = (
        new_jsonl_dir if new_jsonl_dir is not None else config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    )
    if not legacy_dir.is_dir():
        logger.info("relocate_dictionary.skipped legacy_dir_missing=%s", str(legacy_dir))
        return 0
    new_jsonl_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(legacy_dir.rglob("*.jsonl")):
        rel = src.relative_to(legacy_dir)
        target = new_jsonl_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with open(fd, "wb") as out_fh, open(src, "rb") as in_fh:
                shutil.copyfileobj(in_fh, out_fh)
            Path(tmp).replace(target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        count += 1
    logger.info(
        "relocate_dictionary.done legacy=%s new=%s files=%d",
        str(legacy_dir),
        str(new_jsonl_dir),
        count,
    )
    return count


def build_dictionary_catalog(
    *,
    jsonl_dir: Path | None = None,
    output_path: Path | None = None,
) -> None:
    """Walk ``jsonl_dir`` for per-form subdirs (each containing a `*_table.jsonl`)
    and write a lean ToC at ``output_path``. Catalog entries point at
    ``jsonl/<form>/<form>_table.jsonl`` relative to the catalog's parent.
    """

    jsonl_dir = (
        jsonl_dir if jsonl_dir is not None else config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    )
    output_path = (
        output_path if output_path is not None else config.LLM_SOURCE_DICTIONARY_CATALOG_PATH
    )
    forms: dict[str, dict[str, Any]] = {}
    if jsonl_dir.is_dir():
        for sub in sorted(p for p in jsonl_dir.iterdir() if p.is_dir()):
            jsonl_files = sorted(sub.glob("*.jsonl"))
            if not jsonl_files:
                continue
            # Pick the canonical *_table.jsonl if present, else first jsonl
            canonical = next(
                (f for f in jsonl_files if f.name.endswith("_table.jsonl")), jsonl_files[0]
            )
            forms[sub.name] = {
                "file": f"jsonl/{sub.name}/{canonical.name}",
            }
    payload = {"schema_version": _SCHEMA_VERSION, "forms": forms}
    atomic_write_json(output_path, payload)
    logger.info("dictionary_catalog.written forms=%d output=%s", len(forms), str(output_path))


def build_study_metadata_catalog(
    *,
    evidence_packs_dir: Path | None = None,
    output_path: Path | None = None,
) -> None:
    """Walk per-form evidence packs and write a lean study-metadata ToC.

    Skips legacy per-variable JSONs that share the directory (they have a
    ``variable_id`` field; new packs have ``form`` + ``variables[]``).
    """

    evidence_packs_dir = (
        evidence_packs_dir
        if evidence_packs_dir is not None
        else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    )
    output_path = (
        output_path
        if output_path is not None
        else config.STUDY_LLM_SOURCE_DIR / "study_metadata_catalog.json"
    )
    forms: dict[str, dict[str, Any]] = {}
    study: str | None = None
    if evidence_packs_dir.is_dir():
        for f in sorted(evidence_packs_dir.glob("*.json")):
            try:
                body = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(body, dict):
                continue
            # Distinguish new per-form pack (form + variables[]) from legacy per-variable
            if "form" not in body or "variables" not in body:
                continue
            form_name = body.get("form") or f.stem
            forms[form_name] = {
                "evidence_pack": f"evidence_packs/{f.name}",
                "variable_count": len(body.get("variables") or []),
            }
            if study is None:
                study = body.get("study")
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "study": study or "unknown",
        "forms": forms,
    }
    atomic_write_json(output_path, payload)
    logger.info(
        "study_metadata_catalog.written forms=%d output=%s", len(forms), str(output_path)
    )


def build_dataset_schema_catalog(
    *,
    sot_dir: Path | None = None,
    dataset_files_dir: Path | None = None,
    evidence_packs_dir: Path | None = None,
    output_path: Path | None = None,
) -> None:
    """Build the dataset_schema lean ToC with per-form handling summaries.

    Walks every SoT YAML, computes a histogram of handling actions, and
    records {file, sot_yaml, evidence_pack, handling_summary} per form.
    """

    sot_dir = sot_dir if sot_dir is not None else config.SOT_DIR
    dataset_files_dir = (
        dataset_files_dir
        if dataset_files_dir is not None
        else config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    )
    evidence_packs_dir = (
        evidence_packs_dir
        if evidence_packs_dir is not None
        else config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    )
    output_path = (
        output_path
        if output_path is not None
        else config.LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH
    )
    policy_files = sorted(sot_dir.glob("*_policy.yaml"))
    dataset_policies = sot_dir / "dataset_policies"
    if dataset_policies.is_dir():
        policy_files.extend(sorted(dataset_policies.glob("*_policy.yaml")))
    forms: dict[str, dict[str, Any]] = {}
    study: str | None = None
    for policy_path in policy_files:
        policy = yaml.safe_load(policy_path.read_text()) or {}
        if not isinstance(policy, dict):
            continue
        form = policy.get("form") or policy_path.stem.replace("_policy", "")
        if study is None:
            study = policy.get("study")
        # Compute handling_summary
        actions: Counter[str] = Counter()
        variables = policy.get("variables")
        if isinstance(variables, list):
            for v in variables:
                if isinstance(v, dict):
                    handling = v.get("handling_intent") or {}
                    action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
                    actions[action] += 1
        elif isinstance(variables, dict):
            for v in variables.values():
                if isinstance(v, dict):
                    handling = v.get("handling_intent") or {}
                    action = (handling.get("action") if isinstance(handling, dict) else None) or "unknown"
                    actions[action] += 1
        # Resolve relative paths
        sot_yaml_rel = str(policy_path)
        try:
            sot_yaml_rel = str(policy_path.relative_to(config.BASE_DIR))
        except ValueError:
            pass
        forms[form] = {
            "file": f"files/{form}.jsonl",
            "sot_yaml": sot_yaml_rel,
            "evidence_pack": f"../evidence_packs/{form}.json",
            "handling_summary": dict(sorted(actions.items())),
        }
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "study": study or "unknown",
        "forms": forms,
    }
    atomic_write_json(output_path, payload)
    logger.info(
        "dataset_schema_catalog.written forms=%d output=%s", len(forms), str(output_path)
    )


if __name__ == "__main__":
    relocate_dictionary()
    build_dictionary_catalog()
    build_study_metadata_catalog()
    build_dataset_schema_catalog()
