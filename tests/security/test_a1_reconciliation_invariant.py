"""A1 PHI decider↔cleaner reconciliation invariant (Note 28).

The reconciliation guarantee: for the active study, NO published column may carry
both a ``keep_decision`` (phi_review decided KEEP) and a transform ``event`` (the
scrub config drops / pseudonymizes / jitters / suppresses it). Such a column is
the "keep + transform" contradiction A1 eliminated (414 derived → 0).

This is a fast, value-free regression gate built on the exact two functions the
live pipeline uses — ``phi_review.classify_headers`` (decider) and
``extract_to_llm_source._configured_scrub_action`` (cleaner config) — over the
study's row-1 column NAMES only (GR-1). It skips cleanly where the raw datasets
are not present (e.g. CI without the PHI corpus). The authoritative
post-publish check (events ∩ keep_decisions over the real ledgers) runs at the
Indo-VAP orchestrator E2E.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config

_STUDY = "Indo-VAP"

# Pre-existing decided>applied cases the harness flags because it intentionally
# models ONLY classify_headers (not the live review_form_headers SoT /
# confirmed-keep / force-drop reconciliation): free-text ``*_OTHER`` / ``*_COMMENT``
# columns (raw-classified SUPPRESS) and ``Image_Seq`` (raw-classified DROP) that
# the config keeps and the live flow clears. A1 must not GROW this set.
# 20 → 23 (Note 34): the free-text suppress rule was extended to plural forms
# (``notes?``/``comments?``/``remarks?``), so three ``*_Notes`` columns now decide
# SUPPRESS instead of KEEP — STRICTER protection (closing a free-text gap), cleared
# by the live force-drop flow exactly like the existing ``*_OTHER``/``*_COMMENT`` set.
_KNOWN_UNDER_PROTECTION_BASELINE = 23


def _raw_present() -> bool:
    raw = Path(config.RAW_DATA_DIR) / _STUDY / "datasets"
    return raw.is_dir() and any(
        raw.glob(f"*{ext}") for ext in (".xlsx", ".xls", ".csv")
    )


@pytest.mark.skipif(not _raw_present(), reason=f"{_STUDY} raw datasets not present")
def test_no_keep_plus_transform_contradiction() -> None:
    from scripts.utils.reconcile_phi_decisions import derive

    report = derive(_STUDY)
    contradictions = report["contradictions"]
    assert report["contradictions_total"] == 0, (
        f"{report['contradictions_total']} keep+transform contradiction(s) remain: "
        + ", ".join(f"{c['form']}:{c['column']}({c['applied']})" for c in contradictions[:25])
    )


@pytest.mark.skipif(not _raw_present(), reason=f"{_STUDY} raw datasets not present")
def test_no_new_under_protection_beyond_baseline() -> None:
    """phi_review must never decide a STRICTER action than the cleaner applies
    (assertion-12 under-protection). A1 must not exceed the known baseline."""
    from scripts.utils.reconcile_phi_decisions import derive

    report = derive(_STUDY)
    assert report["under_protection_total"] <= _KNOWN_UNDER_PROTECTION_BASELINE, (
        f"{report['under_protection_total']} under-protection(s) "
        f"(> baseline {_KNOWN_UNDER_PROTECTION_BASELINE}): "
        + ", ".join(
            f"{u['form']}:{u['column']}({u['decided']}>{u['applied']})"
            for u in report["under_protections"][:25]
        )
    )
