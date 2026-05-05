"""PDF evidence extraction over authorized pre-extracted text structures.

This module does not parse PDFs and does not read dataset rows. It accepts
already-authorized page/text structures and keeps only form semantics that are
useful for Source Truth review: visible wording, options, groups, relationships,
skip logic, date/unit hints, specimen/test wording, and nearby context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "PDF_EVIDENCE_COMPLETE",
    "PDF_EVIDENCE_NEEDS_HUMAN_REVIEW",
    "PDF_EVIDENCE_NOT_EXTRACTED_YET",
    "PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT",
    "build_pdf_evidence_completeness_report",
    "check_pdf_evidence_completeness",
    "extract_pdf_evidence",
]


PDF_EVIDENCE_COMPLETE = "complete"
PDF_EVIDENCE_NEEDS_HUMAN_REVIEW = "needs_human_review"
PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT = "no_useful_text_left"
PDF_EVIDENCE_NOT_EXTRACTED_YET = "not_extracted_yet"

_TEXT_KEYS = ("lines", "text_lines", "blocks", "spans", "words")
_NOISE_RE = re.compile(
    r"\b("
    r"artifact\s+version|created|creation\s+date|exported|export\s+timestamp|"
    r"footer|form\s+version\s+date|pdf\s+created|pdf\s+creation|printed|"
    r"print\s+timestamp|version\s+date"
    r")\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"^\s*(?:[A-Z]{0,3}\d+[.)]?\s+|\d+[.)]\s+).+")
_SKIP_RE = re.compile(r"\b(if\b.+\bskip\b|skip\s+to|go\s+to)\b", re.IGNORECASE)
_DATE_UNIT_RE = re.compile(
    r"\b(dd[/ -]?mm[/ -]?yyyy|date|day|month|year|unit|mg/dl|cells/mm3|kg|cm)\b",
    re.IGNORECASE,
)
_SPECIMEN_TEST_RE = re.compile(r"\b(specimen|test|assay|result|cd4|viral\s+load)\b", re.IGNORECASE)

_ROLE_KIND = {
    "context": "useful_context",
    "date": "date_unit_hint",
    "date_hint": "date_unit_hint",
    "group": "section_label",
    "hint": "date_unit_hint",
    "instruction": "useful_context",
    "label": "useful_context",
    "note": "useful_context",
    "option": "option_text",
    "question": "question_wording",
    "section": "section_label",
    "skip": "skip_instruction",
    "test": "specimen_test_wording",
    "unit": "date_unit_hint",
}


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _page_number(page: Mapping[str, object], fallback: int) -> int:
    value = page.get("page") or page.get("page_number")
    return value if isinstance(value, int) else fallback


def _line_entries(page: Mapping[str, object]) -> list[object]:
    entries: list[object] = []
    for key in _TEXT_KEYS:
        value = page.get(key)
        if isinstance(value, str):
            entries.append(value)
        elif isinstance(value, Iterable) and not isinstance(value, Mapping):
            entries.extend(value)
    return entries


def _annotation_fields(pages: Sequence[Mapping[str, object]]) -> set[str]:
    fields: set[str] = set()
    for page in pages:
        raw = page.get("annotations")
        if not isinstance(raw, Iterable) or isinstance(raw, (str, Mapping)):
            continue
        fields.update(item for item in raw if isinstance(item, str) and item)
    return fields


def _dataset_columns(form_payload: Mapping[str, object]) -> set[str]:
    raw = form_payload.get("dataset_columns")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, Mapping)):
        return set()
    return {item for item in raw if isinstance(item, str) and item}


def _entry_text(entry: object) -> str | None:
    if isinstance(entry, str):
        return _string(entry)
    if not isinstance(entry, Mapping):
        return None
    for key in ("text", "line", "value"):
        text = _string(entry.get(key))
        if text is not None:
            return text
    return None


def _entry_role(entry: object) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    for key in ("role", "kind", "type"):
        role = _string(entry.get(key))
        if role is not None:
            return role.lower().replace("-", "_").replace(" ", "_")
    return None


def _entry_field(entry: object) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    for key in ("field", "field_id", "variable", "variable_id"):
        field = _string(entry.get(key))
        if field is not None:
            return field
    return None


def _entry_parent(entry: object) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    for key in ("parent", "parent_id", "parent_field", "group"):
        parent = _string(entry.get(key))
        if parent is not None:
            return parent
    return None


def _is_noise(text: str, role: str | None) -> bool:
    return role in {"footer", "metadata", "timestamp", "version"} or bool(_NOISE_RE.search(text))


def _kind(text: str, role: str | None) -> str | None:
    if role in _ROLE_KIND:
        return _ROLE_KIND[role]
    if _SKIP_RE.search(text):
        return "skip_instruction"
    if _DATE_UNIT_RE.search(text):
        return "date_unit_hint"
    if re.match(r"^\s*(section|group)\b", text, flags=re.IGNORECASE):
        return "section_label"
    if _QUESTION_RE.match(text) or "?" in text:
        return "question_wording"
    if _SPECIMEN_TEST_RE.search(text):
        return "specimen_test_wording"
    return None


def extract_pdf_evidence(pages: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Extract Source Truth evidence from authorized PDF page text.

    Args:
        pages: Pre-extracted page structures. Only textual keys such as
            ``lines``/``text_lines``/``blocks``/``spans``/``words`` are read.
            Raw dataset-value keys are ignored and never copied to the artifact.

    Returns:
        A plain artifact mapping containing ``evidence``, ``excluded``,
        ``unclassified_text``, and extraction counters.
    """
    evidence: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    unclassified: list[dict[str, object]] = []
    text_entry_count = 0
    useful_text_count = 0
    current_section_id: str | None = None

    for page_index, page in enumerate(pages, start=1):
        page_no = _page_number(page, page_index)
        for line_index, entry in enumerate(_line_entries(page), start=1):
            text = _entry_text(entry)
            if text is None:
                continue
            text_entry_count += 1
            role = _entry_role(entry)
            if _is_noise(text, role):
                excluded.append(
                    {"page": page_no, "text": text, "reason": "footer_or_version_noise"}
                )
                continue
            useful_text_count += 1
            kind = _kind(text, role)
            if kind is None:
                unclassified.append({"page": page_no, "text": text})
                continue
            field_id = _entry_field(entry)
            parent_id = _entry_parent(entry)
            if kind == "section_label":
                current_section_id = field_id or f"page-{page_no}-section-{line_index}"
            item: dict[str, object] = {
                "id": f"p{page_no}-l{line_index}",
                "page": page_no,
                "kind": kind,
                "text": text,
            }
            if field_id is not None:
                item["field_id"] = field_id
            if parent_id is not None:
                item["parent_id"] = parent_id
            elif current_section_id is not None and kind != "section_label":
                item["section_id"] = current_section_id
            evidence.append(item)

    return {
        "source_boundary": "authorized_pre_extracted_pdf_page_text",
        "evidence": evidence,
        "evidence_count": len(evidence),
        "excluded": excluded,
        "excluded_count": len(excluded),
        "unclassified_text": [item["text"] for item in unclassified],
        "unclassified": unclassified,
        "text_entry_count": text_entry_count,
        "useful_text_count": useful_text_count,
    }


def check_pdf_evidence_completeness(artifact: Mapping[str, object]) -> dict[str, object]:
    """Return the completeness gate for an extracted PDF evidence artifact."""
    text_entry_count = artifact.get("text_entry_count")
    useful_text_count = artifact.get("useful_text_count")
    unclassified = artifact.get("unclassified_text")
    if text_entry_count == 0:
        state = PDF_EVIDENCE_NOT_EXTRACTED_YET
    elif useful_text_count == 0:
        state = PDF_EVIDENCE_NO_USEFUL_TEXT_LEFT
    elif isinstance(unclassified, list) and unclassified:
        state = PDF_EVIDENCE_NEEDS_HUMAN_REVIEW
    else:
        state = PDF_EVIDENCE_COMPLETE
    return {
        "state": state,
        "evidence_count": artifact.get("evidence_count", 0),
        "excluded_count": artifact.get("excluded_count", 0),
        "unclassified_text": unclassified if isinstance(unclassified, list) else [],
    }


def build_pdf_evidence_completeness_report(
    forms: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Build an all-form PDF evidence completeness report.

    Each form payload may include:

    * ``pages``: authorized pre-extracted PDF page structures.
    * ``dataset_columns``: optional current dataset column names.

    The report intentionally does not inspect or copy any row-shaped data.
    """
    form_reports: dict[str, dict[str, object]] = {}
    state_counts: dict[str, int] = {}

    for form_name, form_payload in forms.items():
        raw_pages = form_payload.get("pages", [])
        pages = raw_pages if isinstance(raw_pages, list) else []
        page_maps = [page for page in pages if isinstance(page, Mapping)]
        artifact = extract_pdf_evidence(page_maps)
        gate = check_pdf_evidence_completeness(artifact)
        state = str(gate["state"])
        state_counts[state] = state_counts.get(state, 0) + 1

        raw_evidence = artifact.get("evidence", [])
        evidence = raw_evidence if isinstance(raw_evidence, list) else []
        evidence_items = [item for item in evidence if isinstance(item, Mapping)]
        captured_field_ids = sorted(
            {
                field_id
                for item in evidence_items
                if isinstance(field_id := item.get("field_id"), str) and field_id
            }
        )
        annotations = _annotation_fields(page_maps)
        dataset_columns = _dataset_columns(form_payload)
        source_only = sorted(
            field_id
            for field_id in captured_field_ids
            if dataset_columns and field_id not in dataset_columns
        )
        gaps: list[str] = []
        if state == PDF_EVIDENCE_NOT_EXTRACTED_YET:
            gaps.append("not extracted yet")
        if state == PDF_EVIDENCE_NEEDS_HUMAN_REVIEW:
            unclassified = gate.get("unclassified_text", [])
            if isinstance(unclassified, list):
                gaps.extend(str(item) for item in unclassified)

        form_reports[form_name] = {
            "state": state,
            "captured_pdf_evidence": gate["evidence_count"],
            "captured_field_ids": captured_field_ids,
            "unmatched_annotation_fields": sorted(annotations - set(captured_field_ids)),
            "source_only_pdf_evidence": source_only,
            "evidence_gaps": gaps,
            "excluded_footer_version_content": gate["excluded_count"],
        }

    return {
        "form_count": len(form_reports),
        "forms": form_reports,
        "state_counts": dict(sorted(state_counts.items())),
    }
