"""SoT coverage hard gate.

Returns 0 only when every form is sot_present AND sot_complete.
Used as a CI gate that blocks Phase 1+ work until coverage is total.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def gate(coverage: dict[str, Any]) -> int:
    forms = coverage.get("forms", {})
    if not forms:
        _LOG.error("sot_coverage_gate.fail no forms found in coverage")
        return 1
    failures: list[str] = []
    for form, info in forms.items():
        if not isinstance(info, dict):
            failures.append(
                f"{form}: malformed coverage entry (expected dict, got {type(info).__name__})"
            )
            continue
        # Excluded forms are vacuously complete — skip gate check.
        if info.get("excluded"):
            _LOG.info(
                "sot_coverage_gate.excluded form=%s reason=%s",
                form,
                info.get("exclusion_reason", ""),
            )
            continue
        # Alias forms: gate passes if canonical policy is present (sot_complete set by walker).
        if info.get("alias_of"):
            if not info.get("sot_complete"):
                failures.append(
                    f"{form}: alias of {info['alias_of']} but canonical SoT YAML missing"
                )
            continue
        if not info.get("sot_present"):
            failures.append(f"{form}: SoT YAML missing")
        elif not info.get("sot_complete"):
            missing = info.get("missing_variables", [])
            failures.append(f"{form}: SoT incomplete (missing {len(missing)} variable(s))")
    if failures:
        for f in failures:
            _LOG.error("sot_coverage_gate.fail %s", f)
        return 1
    _LOG.info("sot_coverage_gate.pass forms=%d", len(forms))
    return 0


def main() -> int:
    import argparse

    import config

    p = argparse.ArgumentParser()
    p.add_argument("--coverage-json", default=str(config.SOT_GAP_COVERAGE_PATH))
    args = p.parse_args()
    coverage = json.loads(Path(args.coverage_json).read_text(encoding="utf-8"))
    return gate(coverage)


if __name__ == "__main__":
    raise SystemExit(main())
