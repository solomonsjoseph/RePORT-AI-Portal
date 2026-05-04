"""Runtime retrieval over compact Source Truth catalog artifacts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CatalogAnswer",
    "SourceTruthRetrievalError",
    "SourceTruthRetriever",
]


class SourceTruthRetrievalError(ValueError):
    """Raised when catalog retrieval inputs are malformed."""


@dataclass(frozen=True)
class CatalogAnswer:
    """Answer text plus retrieval metadata for metadata-only catalog questions."""

    text: str
    variable_ids: list[str]
    needs_clarification: bool = False


EvidencePackLoader = Callable[[str], Mapping[str, Any] | None]


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_EVIDENCE_TERMS = frozenset(
    {
        "ambiguity",
        "basis",
        "evidence",
        "exact",
        "explain",
        "page",
        "provenance",
        "source",
        "wording",
        "why",
    }
)


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(value.replace("_", " "))}


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceTruthRetrievalError(f"catalog record must include string {field}")
    return value.strip()


def _records_by_id(records: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(records, list):
        raise SourceTruthRetrievalError("catalog artifact records must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise SourceTruthRetrievalError("catalog records must be mappings")
        variable_id = _string(record.get("variable_id"), "variable_id")
        by_id[variable_id] = record
    return by_id


def _packs_by_id(packs: Any) -> dict[str, Mapping[str, Any]]:
    if packs is None:
        return {}
    if not isinstance(packs, list):
        raise SourceTruthRetrievalError("catalog artifact evidence_packs must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for pack in packs:
        if not isinstance(pack, Mapping):
            raise SourceTruthRetrievalError("evidence packs must be mappings")
        by_id[_string(pack.get("variable_id"), "variable_id")] = pack
    return by_id


class SourceTruthRetriever:
    """Retrieve compact catalog cards and compose concise metadata answers."""

    def __init__(
        self,
        records: Mapping[str, Mapping[str, Any]],
        *,
        evidence_packs: Mapping[str, Mapping[str, Any]] | None = None,
        evidence_pack_loader: EvidencePackLoader | None = None,
    ) -> None:
        self._records = dict(records)
        self._evidence_packs = dict(evidence_packs or {})
        self._evidence_pack_loader = evidence_pack_loader

    @classmethod
    def from_catalog_artifact(
        cls,
        catalog_artifact: Mapping[str, Any],
        *,
        evidence_pack_loader: EvidencePackLoader | None = None,
    ) -> SourceTruthRetriever:
        """Build a retriever from the catalog artifact shape."""
        if not isinstance(catalog_artifact, Mapping):
            raise SourceTruthRetrievalError("catalog artifact must be a mapping")
        return cls(
            _records_by_id(catalog_artifact.get("records")),
            evidence_packs=_packs_by_id(catalog_artifact.get("evidence_packs")),
            evidence_pack_loader=evidence_pack_loader,
        )

    def retrieve_cards(self, query: str, *, limit: int = 5) -> list[Mapping[str, Any]]:
        """Return ranked compact catalog cards for a metadata query."""
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored = [
            (self._score(record, query, query_tokens), variable_id, record)
            for variable_id, record in self._records.items()
        ]
        matches = [
            (score, variable_id, record) for score, variable_id, record in scored if score > 0
        ]
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [record for _, _, record in matches[:limit]]

    def answer_metadata_question(self, question: str) -> CatalogAnswer:
        """Answer a variable metadata question from catalog cards plus lazy evidence."""
        matches = self.retrieve_cards(question, limit=3)
        if not matches:
            return CatalogAnswer(
                "I could not find a matching catalog variable for that question.",
                [],
            )
        if self._is_ambiguous(question, matches):
            return self._clarification(question, matches)

        record = matches[0]
        variable_id = _string(record.get("variable_id"), "variable_id")
        pack = self._load_evidence_pack(variable_id) if self._needs_evidence(question) else None
        return CatalogAnswer(self._compose_answer(record, pack), [variable_id])

    def _score(self, record: Mapping[str, Any], query: str, query_tokens: set[str]) -> int:
        variable_id = _string(record.get("variable_id"), "variable_id")
        if variable_id.lower() in query.lower():
            return 100
        terms = set(_tokens(variable_id))
        for key in ("label", "display_label", "normalized_meaning", "section", "field_class"):
            value = record.get(key)
            if isinstance(value, str):
                terms.update(_tokens(value))
        search_terms = record.get("search_terms", [])
        if isinstance(search_terms, list):
            terms.update(term.lower() for term in search_terms if isinstance(term, str))
        return len(query_tokens & terms)

    def _is_ambiguous(self, question: str, matches: list[Mapping[str, Any]]) -> bool:
        if len(matches) < 2 or self._needs_evidence(question):
            return False
        top_score = self._score(matches[0], question, _tokens(question))
        next_score = self._score(matches[1], question, _tokens(question))
        if top_score != next_score:
            return False
        return all(
            _string(record.get("variable_id"), "variable_id").lower() not in question.lower()
            for record in matches[:2]
        )

    def _clarification(self, question: str, matches: list[Mapping[str, Any]]) -> CatalogAnswer:
        query_tokens = _tokens(question)
        catalog_terms: set[str] = set()
        for record in matches:
            for key in ("variable_id", "label", "display_label", "normalized_meaning"):
                value = record.get(key)
                if isinstance(value, str):
                    catalog_terms.update(_tokens(value))
        focus = next(
            (token.upper() for token in sorted(query_tokens & catalog_terms) if len(token) > 2),
            "that",
        )
        candidates = ", ".join(
            f"{record['variable_id']} ({record.get('display_label') or record.get('label')})"
            for record in matches
        )
        return CatalogAnswer(
            f"Which {focus} variable do you mean? Candidates: {candidates}.",
            [_string(record.get("variable_id"), "variable_id") for record in matches],
            needs_clarification=True,
        )

    def _needs_evidence(self, question: str) -> bool:
        return bool(_tokens(question) & _EVIDENCE_TERMS)

    def _load_evidence_pack(self, variable_id: str) -> Mapping[str, Any] | None:
        if self._evidence_pack_loader is not None:
            return self._evidence_pack_loader(variable_id)
        return self._evidence_packs.get(variable_id)

    def _compose_answer(
        self,
        record: Mapping[str, Any],
        pack: Mapping[str, Any] | None,
    ) -> str:
        variable_id = _string(record.get("variable_id"), "variable_id")
        label = _string(record.get("display_label") or record.get("label"), "label")
        parts = [f"{variable_id} is {label}."]

        dataset_column = record.get("dataset_column")
        form = record.get("form")
        if isinstance(dataset_column, str) and isinstance(form, str):
            parts.append(f"It maps to dataset column {dataset_column} on {form}.")
        elif isinstance(dataset_column, str):
            parts.append(f"It maps to dataset column {dataset_column}.")

        handling = record.get("handling_action")
        if isinstance(handling, str):
            parts.append(f"Handling: {handling}.")

        options = self._options_text(record, pack)
        if options:
            parts.append(options)

        provenance = self._provenance_text(pack)
        if provenance:
            parts.append(provenance)

        return " ".join(parts)

    def _options_text(
        self,
        record: Mapping[str, Any],
        pack: Mapping[str, Any] | None,
    ) -> str | None:
        exact = pack.get("exact_source_wording", {}) if isinstance(pack, Mapping) else {}
        if isinstance(exact, Mapping):
            pdf_options = exact.get("pdf_options")
            if isinstance(pdf_options, list) and pdf_options:
                options = ", ".join(str(option) for option in pdf_options)
                return f"Options: {options}."

        summary = record.get("options_summary")
        if not isinstance(summary, Mapping):
            return None
        count = summary.get("count")
        if not isinstance(count, int) or count <= 0:
            return None
        option_set = summary.get("option_set")
        if isinstance(option_set, str):
            return f"Options: {count} defined in {option_set}."
        return f"Options: {count} defined."

    def _provenance_text(self, pack: Mapping[str, Any] | None) -> str | None:
        if not isinstance(pack, Mapping):
            return None
        references = pack.get("source_references")
        if not isinstance(references, Mapping):
            return None
        pdf = references.get("pdf")
        if not isinstance(pdf, Mapping):
            return None
        pages = pdf.get("annotation_pages")
        if not isinstance(pages, list) or not pages:
            return None
        page_text = ", ".join(str(page) for page in pages)
        return f"Provenance: PDF page {page_text}."
