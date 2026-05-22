"""Published llm_source bundle readiness checks for the chat UI."""

from __future__ import annotations

from pathlib import Path

import config


def _has_dataset_jsonl() -> bool:
    return config.TRIO_DATASETS_DIR.is_dir() and any(config.TRIO_DATASETS_DIR.glob("*.jsonl"))


def _dictionary_source_expected() -> bool:
    source_dir = Path(getattr(config, "DATA_DICTIONARY_DIR", ""))
    try:
        return source_dir.is_dir() and any(
            path.is_file() and not path.name.startswith(".") for path in source_dir.rglob("*")
        )
    except OSError:
        return False


def _has_dictionary_mapping_jsonl() -> bool:
    mapping_dir = Path(
        getattr(
            config,
            "DICTIONARY_JSON_OUTPUT_DIR",
            config.STUDY_LLM_SOURCE_DIR / "dictionary_mapping" / "jsonl",
        )
    )
    return mapping_dir.is_dir() and any(mapping_dir.rglob("*.jsonl"))


def _has_policy_sot() -> bool:
    sot_dir = getattr(config, "LLM_SOURCE_SOT_DIR", config.STUDY_LLM_SOURCE_DIR / "SoT")
    sot_path = Path(sot_dir)
    if sot_path.is_dir() and any(sot_path.glob("*/pdf/*_policy.yaml")):
        return True

    legacy_dir = getattr(
        config,
        "LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR",
        config.STUDY_LLM_SOURCE_DIR / "source_truth",
    )
    legacy_path = Path(legacy_dir)
    return legacy_path.is_dir() and (
        any(legacy_path.glob("*_policy.yaml")) or any(legacy_path.glob("*_policy.lean.yaml"))
    )


def bundle_readiness_issues() -> list[str]:
    """Return human-readable reasons the published bundle is not ready."""

    issues: list[str] = []
    if not config.STUDY_LLM_SOURCE_DIR.exists():
        issues.append(f"missing llm_source directory: {config.STUDY_LLM_SOURCE_DIR}")
    if not _has_dataset_jsonl():
        issues.append(f"missing scrubbed dataset JSONL under {config.TRIO_DATASETS_DIR}")
    if not _has_policy_sot():
        issues.append("missing Source Truth policy output under llm_source/SoT/<pair>/pdf/")
    if _dictionary_source_expected() and not _has_dictionary_mapping_jsonl():
        issues.append(
            "missing dictionary mapping JSONL under "
            f"{config.DICTIONARY_JSON_OUTPUT_DIR}"
        )
    return issues


def published_bundle_exists() -> bool:
    """Return True when the assistant has the minimum published bundle.

    The active bundle shape requires scrubbed dataset JSONL plus Source Truth
    policy output. Dictionary mappings are required when a raw dictionary
    source is present, preserving the host pipeline's previous dictionary leg.
    """

    try:
        return not bundle_readiness_issues()
    except Exception:
        return False
