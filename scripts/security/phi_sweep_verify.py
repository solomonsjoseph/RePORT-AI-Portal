"""Phase 1 exit-criterion verifier.

Returns 0 only when every variable in the sweep findings is either:
- category == "covered"; OR
- category == "review_required_open" AND a matching HITL draft exists; OR
- category == "name_phi_uncovered" / "column_shape_phi_uncovered" AND a PR
  draft exists for the regulatory anchor.

Used by ``make phi-audit-verify`` and CI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import config
from scripts.security.phi_scrub import PHIScrubError


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


class VerificationFailed(PHIScrubError):
    pass


def _slug(text: str | None) -> str:
    if not text:
        return "uncategorized"
    return _SLUG_RE.sub("-", text).strip("-").lower() or "uncategorized"


def verify(
    *,
    findings_path: Path | None = None,
    hitl_drafts_dir: Path | None = None,
    pr_drafts_dir: Path | None = None,
) -> None:
    findings_path = findings_path if findings_path is not None else config.PHI_SWEEP_FINDINGS_PATH
    hitl_drafts_dir = hitl_drafts_dir if hitl_drafts_dir is not None else config.PHI_SWEEP_HITL_DRAFTS_DIR
    pr_drafts_dir = pr_drafts_dir if pr_drafts_dir is not None else config.PHI_SWEEP_PR_DRAFTS_DIR
    payload = json.loads(findings_path.read_text())
    failures: list[str] = []
    for f in payload["findings"]:
        cat = f["category"]
        if cat == "covered":
            continue
        if cat == "review_required_open":
            expected = hitl_drafts_dir / f"{f['form']}_{f['variable_id_masked']}.md"
            if not expected.is_file():
                failures.append(f"missing HITL draft: {expected}")
            continue
        if cat in {"name_phi_uncovered", "column_shape_phi_uncovered"}:
            expected = pr_drafts_dir / f"{_slug(f.get('regulatory_anchor_hint'))}.md"
            if not expected.is_file():
                failures.append(f"missing PR draft for anchor {f.get('regulatory_anchor_hint')!r}: {expected}")
            continue
        failures.append(f"unknown category {cat!r} for {f['form']}/{f['variable_id_masked']}")
    if failures:
        raise VerificationFailed("Phase 1 exit criterion not met:\n  - " + "\n  - ".join(failures))


if __name__ == "__main__":
    try:
        verify()
    except VerificationFailed as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
    sys.exit(0)
