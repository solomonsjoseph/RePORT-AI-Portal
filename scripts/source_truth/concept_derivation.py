"""SoT-derived concept index.

Pure-function derivation of the cross-form study concept index from the
already-loaded SoT policy artifacts (one per form). No I/O. No
hand-authored definition wording — only structural signals from the
SoT itself (form titles, sections, section_groupings, sensitivity flags).

The output schema is::

    {
      "schema_version": 1,
      "policy_status": "derived_from_sot",
      "study": <study name>,
      "cohorts": {...},
      "outcomes": {...},
      "exposures": {...},
      "schedules": {...},
      "definitions": {...}
    }

Policy YAMLs are FROZEN; this module reads only what the
``policy_loader`` already exposes on the translated record:

- ``record["normalized"]["section"]`` — pdf_sections key the variable was
  mapped to (e.g. ``"alcohol"``, ``"medical_history"``,
  ``"adverse_event"``).
- ``record["normalized"]["sensitivity_flags"]`` — flags such as
  ``"subject_identifier"`` used to assign cohort identifier roles.
- ``record["normalized"]["relationships"]["section_grouping"]`` — coarse
  section grouping when present.

Outcome derivation deliberately covers the full SoT outcome surface:
adverse-event (95_SAE), incident-tb / follow-up (12A/12B), treatment
compliance (13), and final-outcome / final-status (98A/98B/99A/99B).
The plan's narrower form list would silently drop the canonical study
final-outcome forms, so we widen the form set and the section-pattern
list. See Phase-2 design notes for context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "ConceptDerivationError",
    "derive_cohorts",
    "derive_concept_index",
    "derive_definitions",
    "derive_exposures",
    "derive_outcomes",
    "derive_schedules",
]


class ConceptDerivationError(ValueError):
    """Raised when the concept index cannot be derived from SoT inputs."""


# Cohort assignment by form-id suffix.
_COHORT_A_RE = re.compile(r"^[0-9]+A($|_)")
_COHORT_B_RE = re.compile(r"^[0-9]+B($|_)")

# Forms that contribute outcome-relevant variables. The plan named only
# 95_SAE / 12B_FUB / 13_TxCompliance, but the canonical final-outcome
# sections in real Indo-VAP live in 98A/98B/99A/99B; widening the form
# set ensures derivation does not silently drop them.
_OUTCOME_FORM_PATTERNS = (
    re.compile(r"^95_SAE($|_)"),
    re.compile(r"^12[AB]_FU[AB]($|_)"),
    re.compile(r"^13_TxCompliance($|_)"),
    re.compile(r"^98[AB]_FO[AB]($|_)"),
    re.compile(r"^99[AB]_FS[AB]($|_)"),
)

# Section-name substrings that mark a record as outcome-relevant.
_OUTCOME_SECTION_TOKENS = (
    "adverse_event",
    "incident_tb",
    "treatment_outcome",
    "event_summary",
    "relapse",
    "final_outcome",
    "tb_diagnosis",
    "death",
    "alternative_diagnosis",
)

# Forms that contribute exposure variables — the baselines.
_EXPOSURE_FORM_PATTERNS = (
    re.compile(r"^2A_ICBaseline($|_)"),
    re.compile(r"^2B_HCBaseline($|_)"),
)

# Section-name substrings (case-insensitive) that mark a section as a
# risk-factor / exposure. Matches both translated-record
# ``normalized.section`` values (e.g. ``alcohol``, ``smoking_history``,
# ``medical_history``, ``diet``) and the broader pdf_sections keys
# (e.g. ``risk_behaviours_alcohol``, ``clinical_evaluation_medhx``,
# ``hiv``, ``dietary_questions``).
_EXPOSURE_SECTION_TOKENS = (
    "alcohol",
    "smoking",
    "tobacco",
    "risk_behav",
    "medical_history",
    "medhx",
    "hiv",
    "diet",
    "nutrition",
    "bmi",
    "diabetes",
)


# Schedule phase classification.
#
# Each rule has a title pattern, an optional id pattern, and a phase
# label. The first rule whose title pattern matches the form_title — or
# whose id pattern (when provided) matches the form_id — wins. ``None``
# in the id slot means "this rule is title-only"; we skip the id-match
# entirely rather than relying on a never-match sentinel regex.
#
# Order matters: more specific patterns first (adverse_event/
# final_outcome before follow_up/screening/baseline so that ``98A_FOA``
# is classified as final_outcome rather than via a title containing the
# word "Form").
_SCHEDULE_RULES: tuple[tuple[re.Pattern[str], re.Pattern[str] | None, str], ...] = (
    (
        re.compile(r"serious adverse event|\bsae\b", re.IGNORECASE),
        re.compile(r"^95_SAE($|_)"),
        "adverse_event",
    ),
    (
        re.compile(r"final outcome|\bfoa\b|\bfob\b|\bfsa\b|\bfsb\b|off[- ]?study", re.IGNORECASE),
        re.compile(r"^9[89][AB]_(FO|FS)[AB]($|_)"),
        "final_outcome",
    ),
    (
        re.compile(r"follow.?up.*\bb\b|\bfub\b|household contact follow", re.IGNORECASE),
        re.compile(r"^12B_FUB($|_)"),
        "follow_up_b",
    ),
    (
        re.compile(r"follow.?up.*\ba\b|\bfua\b|index case follow", re.IGNORECASE),
        re.compile(r"^12A_FUA($|_)"),
        "follow_up_a",
    ),
    (
        re.compile(r"follow.?up", re.IGNORECASE),
        None,  # title-only rule — no id match
        "follow_up_a",
    ),
    (
        re.compile(r"screening", re.IGNORECASE),
        re.compile(r"Screening", re.IGNORECASE),
        "screening",
    ),
    (
        re.compile(r"baseline", re.IGNORECASE),
        re.compile(r"Baseline", re.IGNORECASE),
        "baseline",
    ),
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _form_title(form: Mapping[str, Any]) -> str:
    md = form.get("pdf_form_metadata") or {}
    title = md.get("form_title")
    return title if isinstance(title, str) and title else ""


def _record_section(record: Mapping[str, Any]) -> str | None:
    nm = record.get("normalized") or {}
    sec = nm.get("section")
    return sec if isinstance(sec, str) else None


def _record_section_grouping(record: Mapping[str, Any]) -> str | None:
    nm = record.get("normalized") or {}
    rels = nm.get("relationships") or {}
    sg = rels.get("section_grouping")
    return sg if isinstance(sg, str) else None


def _is_subject_identifier(record: Mapping[str, Any]) -> bool:
    nm = record.get("normalized") or {}
    flags = nm.get("sensitivity_flags") or []
    return "subject_identifier" in flags


def _section_matches(section: str | None, tokens: Iterable[str]) -> bool:
    if not section:
        return False
    s = section.lower()
    return any(tok in s for tok in tokens)


def _form_matches_any(form_id: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(p.match(form_id) for p in patterns)


def _humanize(name: str) -> str:
    """Turn a snake/camel section name into a human-readable label.

    ``risk_behaviours_alcohol`` → ``"Risk behaviours: alcohol"``;
    ``medical_history`` → ``"Medical history"``.
    """
    parts = name.replace("-", "_").split("_")
    if len(parts) >= 2 and parts[0] in {"risk", "clinical"}:
        head = " ".join(parts[:2])
        tail = " ".join(parts[2:])
        return f"{head.capitalize()}: {tail}".strip(": ").rstrip()
    return " ".join(parts).capitalize() or name


def _study_name(forms: list[Mapping[str, Any]]) -> str:
    """Pick the first non-empty study name across all forms."""
    for f in forms:
        s = f.get("study")
        if isinstance(s, str) and s:
            return s
    return "unknown"


# ---------------------------------------------------------------------------
# cohorts
# ---------------------------------------------------------------------------


def derive_cohorts(forms: list[dict]) -> dict[str, dict]:
    """Group forms into cohort A (Index Cases) and cohort B (Household Contacts)."""
    buckets: dict[str, dict[str, Any]] = {
        "cohort_a": {
            "name": "Cohort A — Index Cases",
            "member_forms": [],
            "member_variables": [],
        },
        "cohort_b": {
            "name": "Cohort B — Household Contacts",
            "member_forms": [],
            "member_variables": [],
        },
    }
    for form in forms:
        fid = form.get("form")
        if not isinstance(fid, str):
            continue
        if _COHORT_A_RE.match(fid):
            target = "cohort_a"
        elif _COHORT_B_RE.match(fid):
            target = "cohort_b"
        else:
            continue
        if fid not in buckets[target]["member_forms"]:
            buckets[target]["member_forms"].append(fid)
        for record in form.get("records") or []:
            vid = record.get("variable_id")
            if not vid:
                continue
            role = "identifier" if _is_subject_identifier(record) else "member"
            buckets[target]["member_variables"].append(
                {"form": fid, "variable_id": vid, "role": role}
            )

    out: dict[str, dict[str, Any]] = {}
    for cid, body in buckets.items():
        # Drop empty cohorts so callers don't see noise on small fixtures.
        if not body["member_forms"]:
            continue
        body["member_forms"] = sorted(body["member_forms"])
        body["member_variables"] = sorted(
            body["member_variables"],
            key=lambda m: (m["form"], m["variable_id"]),
        )
        out[cid] = body
    return out


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------


def derive_outcomes(forms: list[dict]) -> dict[str, dict]:
    """Pull outcome-relevant variables from the SAE / follow-up / final-outcome forms."""
    out: dict[str, dict[str, Any]] = {}
    for form in forms:
        fid = form.get("form")
        if not isinstance(fid, str) or not _form_matches_any(fid, _OUTCOME_FORM_PATTERNS):
            continue

        members: list[dict[str, Any]] = []
        section_signals: set[str] = set()
        for record in form.get("records") or []:
            sec = _record_section(record)
            sg = _record_section_grouping(record)
            ref_sections = [s for s in (sec, sg) if s]
            if not any(_section_matches(rs, _OUTCOME_SECTION_TOKENS) for rs in ref_sections):
                continue
            vid = record.get("variable_id")
            if not vid:
                continue
            members.append({"form": fid, "variable_id": vid, "role": "outcome"})
            for rs in ref_sections:
                if _section_matches(rs, _OUTCOME_SECTION_TOKENS):
                    section_signals.add(rs)

        if not members:
            continue
        title = _form_title(form) or fid
        key = fid.lower()
        out[key] = {
            "name": title,
            "member_forms": [fid],
            "member_variables": sorted(members, key=lambda m: (m["form"], m["variable_id"])),
            "section_signals": sorted(section_signals),
        }
    return out


# ---------------------------------------------------------------------------
# exposures
# ---------------------------------------------------------------------------


def derive_exposures(forms: list[dict]) -> dict[str, dict]:
    """Derive exposure groupings from baseline-form risk-factor sections."""
    grouped: dict[str, dict[str, Any]] = {}
    for form in forms:
        fid = form.get("form")
        if not isinstance(fid, str) or not _form_matches_any(fid, _EXPOSURE_FORM_PATTERNS):
            continue
        for record in form.get("records") or []:
            sec = _record_section(record)
            sg = _record_section_grouping(record)
            # Choose the most specific matching key for grouping.
            matching: str | None = None
            for candidate in (sg, sec):
                if _section_matches(candidate, _EXPOSURE_SECTION_TOKENS):
                    matching = candidate
                    break
            if not matching:
                continue
            vid = record.get("variable_id")
            if not vid:
                continue
            entry = grouped.setdefault(
                matching,
                {
                    "name": _humanize(matching),
                    "member_forms": [],
                    "member_variables": [],
                },
            )
            if fid not in entry["member_forms"]:
                entry["member_forms"].append(fid)
            entry["member_variables"].append({"form": fid, "variable_id": vid, "role": "exposure"})

    # Canonicalise.
    for body in grouped.values():
        body["member_forms"] = sorted(body["member_forms"])
        body["member_variables"] = sorted(
            body["member_variables"],
            key=lambda m: (m["form"], m["variable_id"]),
        )
    return grouped


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------


def derive_schedules(forms: list[dict]) -> dict[str, dict]:
    """Group forms by phase via form-title and form-id regex match.

    Falls back to form-id pattern when the title alone is ambiguous —
    e.g. ``2A_ICBaseline`` has title ``"INDEX CASE: Clinical/Demographic
    Form"`` which lacks the word "baseline", but the form id does.

    Output is **phase-keyed**: each phase entry collects every form
    classified to that phase under ``member_forms`` (sorted, unique).
    Phases with zero members are not emitted, mirroring the
    ``derive_cohorts`` / ``derive_exposures`` shape.
    """
    grouped: dict[str, list[str]] = {}
    for form in forms:
        fid = form.get("form")
        if not isinstance(fid, str):
            continue
        title = _form_title(form)
        phase = "other"
        for title_rule, id_rule, label in _SCHEDULE_RULES:
            if title_rule.search(title) or (id_rule is not None and id_rule.search(fid)):
                phase = label
                break
        grouped.setdefault(phase, []).append(fid)

    return {
        phase: {"phase": phase, "member_forms": sorted(set(form_ids))}
        for phase, form_ids in grouped.items()
        if form_ids
    }


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------


def derive_definitions(forms: list[dict]) -> dict[str, dict]:
    """Per-form structural definition: form, form_title, non-null section_labels.

    Deliberately contains NO free-text definition wording — that lives in
    evidence packs, never in the concept index.
    """
    out: dict[str, dict[str, Any]] = {}
    for form in forms:
        fid = form.get("form")
        if not isinstance(fid, str):
            continue
        title = _form_title(form)
        section_labels: list[str] = []
        sections = form.get("pdf_sections") or {}
        if isinstance(sections, Mapping):
            for body in sections.values():
                if not isinstance(body, Mapping):
                    continue
                label = body.get("section_label")
                if isinstance(label, str) and label:
                    section_labels.append(label)
        out[fid] = {
            "form": fid,
            "form_title": title,
            "section_labels": section_labels,
        }
    return out


# ---------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------


def derive_concept_index(forms: list[dict]) -> dict[str, Any]:
    """Aggregate all five derivation passes into a canonical concept index."""
    if not forms:
        raise ConceptDerivationError("Cannot derive concept index from empty policy artifact list")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "policy_status": "derived_from_sot",
        "study": _study_name(forms),
        "cohorts": derive_cohorts(forms),
        "outcomes": derive_outcomes(forms),
        "exposures": derive_exposures(forms),
        "schedules": derive_schedules(forms),
        "definitions": derive_definitions(forms),
    }
    return _canonicalise(payload)


def _canonicalise(value: Any) -> Any:
    """Recursively sort dict keys for stable serialization.

    Lists are left in their (already-sorted) order — sorting a list of
    heterogeneous dicts is the caller's responsibility.
    """
    if isinstance(value, Mapping):
        return {k: _canonicalise(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_canonicalise(v) for v in value]
    return value
