"""Drift test: transformation_methods catalog ↔ phi_scrub._method_for_action (Note 9)."""

from __future__ import annotations

from pathlib import Path

import yaml

import config
from scripts.security.phi_scrub import PHIScrubConfig, _method_for_action, load_scrub_config


def _catalog_ids() -> set[str]:
    defaults = Path(config.CONFIG_DEFAULTS_DIR) / "phi_scrub.yaml"
    raw = yaml.safe_load(defaults.read_text(encoding="utf-8"))
    methods = raw.get("transformation_methods") or {}
    assert isinstance(methods, dict)
    return set(methods.keys())


def _engine_method_ids(cfg: PHIScrubConfig) -> set[str]:
    actions = (
        "cap",
        "jitter_date",
        "pseudonymize",
        "generalize",
        "band",
        "suppress_small_cell",
        "drop",
        "birthdate_drop",
    )
    ids: set[str] = set()
    for action in actions:
        name, _params = _method_for_action(action, cfg, "EXAMPLE_FIELD")
        if name:
            ids.add(name)
    return ids


def test_transformation_methods_catalog_covers_engine_methods() -> None:
    cfg = load_scrub_config(study="Indo-VAP")
    assert cfg is not None
    catalog = _catalog_ids()
    engine = _engine_method_ids(cfg)
    missing = engine - catalog
    assert not missing, f"catalog missing engine method IDs: {sorted(missing)}"


def test_phi_review_action_methods_match_engine() -> None:
    from scripts.security.phi_review import Action, _ACTION_METHOD

    cfg = load_scrub_config()
    assert cfg is not None
    for action, expected in _ACTION_METHOD.items():
        if action is Action.KEEP or expected is None:
            continue
        scrub_action = {
            Action.SUPPRESS: "suppress_small_cell",
            Action.CAP: "cap",
            Action.GENERALIZE: "generalize",
            Action.JITTER_DATE: "jitter_date",
            Action.PSEUDONYMIZE: "pseudonymize",
            Action.DROP: "drop",
        }[action]
        engine_id, _ = _method_for_action(scrub_action, cfg, "EXAMPLE")
        assert engine_id == expected, f"{action}: catalog drift {expected!r} vs {engine_id!r}"


def test_keep_passthrough_documented_in_catalog() -> None:
    assert "keep_passthrough" in _catalog_ids()
