"""Cross-check SoT ``phi:`` declarations against the compiled scrub rule
catalog (Step 10b of the PHI pipeline hardening plan).

Two independent authorities describe how each variable should be handled:

* The **design layer** — per-form ``<form>_policy.yaml`` files under
  ``output/{STUDY}/llm_source/SoT/*/pdf/`` declare ``variables.<NAME>.phi``
  (``drop`` / ``pseudonymize`` / ``jitter_date``) as part of the study's
  source-of-truth authoring workflow.
* The **implementation layer** — :func:`scripts.security.phi_scrub.resolve_action`
  classifies the same column names against the compiled ``phi_scrub.yaml``
  rule catalog.

Nothing previously cross-checked the two. This module parses the SoT
policy YAMLs — file names, section labels, and ``phi:`` declarations only,
never a dataset row value — and reports every disagreement so drift is
caught instead of silently accumulating (the 56-variable gap this plan
found and closed for Indo-VAP).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.security.phi_scrub import PHIScrubConfig, resolve_action

__all__ = ["SotDisagreement", "collect_sot_declarations", "crosscheck_sot_policy"]

# SoT declarations that a resolved action can satisfy even when the literal
# strings differ — birthdate_drop satisfies a declared "drop", and the
# value-conditional redundant_subject_id tag satisfies a declared
# "pseudonymize" (its two possible outcomes are drop-if-identical or
# pseudonymize-if-different; SoT authors only ever declare "pseudonymize"
# for these page-level ID copies).
_SATISFIES: dict[str, frozenset[str]] = {
    "birthdate_drop": frozenset({"drop"}),
    "redundant_subject_id": frozenset({"pseudonymize"}),
}


class SotDisagreement:
    """One variable where the SoT ``phi:`` declaration and the compiled
    scrub catalog resolve differently."""

    __slots__ = ("column", "declared", "form", "resolved")

    def __init__(self, *, column: str, form: str, declared: str, resolved: str) -> None:
        self.column = column
        self.form = form
        self.declared = declared
        self.resolved = resolved

    def to_dict(self) -> dict[str, str]:
        return {
            "column": self.column,
            "form": self.form,
            "declared": self.declared,
            "resolved": self.resolved,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return (
            f"SotDisagreement(column={self.column!r}, form={self.form!r}, "
            f"declared={self.declared!r}, resolved={self.resolved!r})"
        )


def collect_sot_declarations(sot_root: Path) -> dict[str, tuple[str, str]]:
    """Return ``{column: (declared_phi_action, form_name)}`` for every
    ``variables.<NAME>.phi`` entry across every ``*_policy.yaml`` under
    *sot_root*. Reads only variable names and the ``phi:`` string — never a
    dataset row value. Columns with no ``phi:`` key are omitted (no
    declaration to cross-check)."""
    declarations: dict[str, tuple[str, str]] = {}
    if not sot_root.is_dir():
        return declarations
    for policy_path in sorted(sot_root.glob("*/pdf/*_policy.yaml")):
        try:
            with policy_path.open("r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict):
            continue
        variables = raw.get("variables")
        if not isinstance(variables, dict):
            continue
        form_name = policy_path.parent.parent.name
        for name, entry in variables.items():
            if not isinstance(entry, dict):
                continue
            phi = entry.get("phi")
            if not phi:
                continue
            declarations[str(name)] = (str(phi), form_name)
    return declarations


def crosscheck_sot_policy(
    cfg: PHIScrubConfig,
    sot_root: Path,
    *,
    age_variable_present: bool = True,
) -> list[SotDisagreement]:
    """Compare every SoT ``phi:`` declaration under *sot_root* against
    :func:`resolve_action`. Returns the list of disagreements — does not
    raise. Steps 4 and 6 reconcile the known 56 mismatches for Indo-VAP, so
    a non-empty result on a stable rule catalog means new drift and should
    be routed to the review queue (Step 11c), not silently accepted."""
    declarations = collect_sot_declarations(sot_root)
    disagreements: list[SotDisagreement] = []
    for column, (declared, form_name) in sorted(declarations.items()):
        resolved = resolve_action(cfg, column, age_variable_present=age_variable_present)
        if resolved == declared:
            continue
        if declared in _SATISFIES.get(resolved, frozenset()):
            continue
        disagreements.append(
            SotDisagreement(column=column, form=form_name, declared=declared, resolved=resolved)
        )
    return disagreements
