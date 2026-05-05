"""Runtime retrieval over compact Source Truth catalog artifacts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from scripts.source_truth.catalog import AUDIT_ONLY_NOTE

__all__ = [
    "CatalogAnswer",
    "SourceTruthRetrievalError",
    "SourceTruthRetriever",
]


# Polite, deflection-only message for variables the user asks about that
# are not in the catalog (either never present or deliberately dropped).
# Avoids exposing PHI/sensitive classification or audit ledger details —
# see AC for issue #73.
DROPPED_OR_UNAVAILABLE_NOTE = (
    "I couldn't find that variable in the published study catalog. "
    "If you believe it should be available, please reach out to the "
    "project maintainer."
)


class SourceTruthRetrievalError(ValueError):
    """Raised when catalog retrieval inputs are malformed."""


@dataclass(frozen=True)
class CatalogAnswer:
    """Answer text plus retrieval metadata for metadata-only catalog questions.

    ``audit_only`` and ``analysis_queryable`` carry the boundary signals
    that downstream layers and tool-description guidance use to decide
    whether to surface the result, deflect to the maintainer, or refuse
    analysis. They are part of the public API of the answer.
    """

    text: str
    variable_ids: list[str]
    needs_clarification: bool = False
    audit_only: bool = False
    analysis_queryable: bool = True


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


def _record_id(record: Mapping[str, Any]) -> str:
    """Stable record id — variable cards expose ``variable_id``, study-design
    cards expose ``card_id``. Either is accepted; one of the two is required."""
    variable_id = record.get("variable_id")
    if isinstance(variable_id, str) and variable_id.strip():
        return variable_id.strip()
    card_id = record.get("card_id")
    if isinstance(card_id, str) and card_id.strip():
        return card_id.strip()
    raise SourceTruthRetrievalError(
        "catalog record must include a non-empty 'variable_id' or 'card_id'"
    )


def _records_by_id(records: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(records, list):
        raise SourceTruthRetrievalError("catalog artifact records must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise SourceTruthRetrievalError("catalog records must be mappings")
        by_id[_record_id(record)] = record
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
        excluded_records: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._records = dict(records)
        self._evidence_packs = dict(evidence_packs or {})
        self._evidence_pack_loader = evidence_pack_loader
        self._excluded_records = dict(excluded_records or {})

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
        excluded = catalog_artifact.get("excluded_records")
        excluded_map: Mapping[str, Mapping[str, Any]] = (
            excluded if isinstance(excluded, Mapping) else {}
        )
        return cls(
            _records_by_id(catalog_artifact.get("records")),
            evidence_packs=_packs_by_id(catalog_artifact.get("evidence_packs")),
            evidence_pack_loader=evidence_pack_loader,
            excluded_records=excluded_map,
        )

    def retrieve_cards(self, query: str, *, limit: int = 5) -> list[Mapping[str, Any]]:
        """Return ranked compact catalog cards for a metadata query."""
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored = [
            (self._score(record, query, query_tokens), record_id, record)
            for record_id, record in self._records.items()
        ]
        matches = [(score, record_id, record) for score, record_id, record in scored if score > 0]
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
        record_id = _record_id(record)
        pack = self._load_evidence_pack(record_id) if self._needs_evidence(question) else None
        return CatalogAnswer(self._compose_answer(record, pack), [record_id])

    def answer_chat_question(self, question: str) -> CatalogAnswer:
        """Answer a normal-chat question respecting the four-state boundary.

        The four states are:
          1. **Dataset-backed retained**: ordinary metadata answer; no
             ``Note:`` about PHI; ``analysis_queryable=True``.
          2. **Source-only**: metadata answer with a concise
             ``Note: <variable> is not analysis-queryable...`` so the LLM
             does not silently route the user into an analysis attempt.
             ``analysis_queryable=False``.
          3. **Dropped variable** (in ``excluded_records`` with
             ``handling_action="drop"``): polite maintainer-contact
             message; no PHI/sensitive classification or ledger detail.
          4. **Audit-only** retained variable
             (``audit_only=True`` flag on the compact card): surface the
             verbatim :data:`AUDIT_ONLY_NOTE` constant. The chat path does
             not expose ledger contents.

        Ambiguity wins over audit_only: if multiple compact cards match
        equally, the answer is a clarification request, not a deflection.
        """
        # Direct id match against dropped/source-only takes precedence
        # over fuzzy compact matches: if the user named the dropped or
        # source-only variable explicitly, treat it as that, not as a
        # different compact-catalog hit that happens to share a token.
        boundary = self._direct_excluded_match(question)
        if boundary is not None:
            return boundary

        matches = self.retrieve_cards(question, limit=3)

        # If nothing matched in the compact catalog, check whether the
        # question references a known dropped or source-only id. We check
        # by token rather than substring so the question doesn't need to
        # contain a verbatim variable id — any match between the
        # question's tokens and a known excluded/source-only id surfaces
        # the appropriate boundary response.
        if not matches:
            return self._chat_no_compact_match(question)

        if self._is_ambiguous(question, matches):
            return self._clarification(question, matches)

        record = matches[0]
        record_id = _record_id(record)
        if record.get("audit_only") is True:
            return CatalogAnswer(
                AUDIT_ONLY_NOTE,
                [record_id],
                audit_only=True,
                analysis_queryable=False,
            )

        pack = self._load_evidence_pack(record_id) if self._needs_evidence(question) else None
        return CatalogAnswer(
            self._compose_answer(record, pack),
            [record_id],
            audit_only=False,
            analysis_queryable=record.get("analysis_queryable") is True,
        )

    def _direct_excluded_match(self, question: str) -> CatalogAnswer | None:
        """If the question literally names a dropped or source-only variable
        id, return the boundary response for that variable. Otherwise None.

        The match is exact on the variable id (case-insensitive) — we do
        NOT fuzzy-match here, because the goal is to give precedence to
        explicit references and avoid pulling boundary responses out of
        thin air on a stray token.
        """
        lowered = question.lower()
        # Source-only first: evidence pack present, no compact record.
        for variable_id, pack in self._evidence_packs.items():
            if variable_id in self._records:
                continue
            if variable_id.lower() in lowered:
                return self._source_only_answer(variable_id, pack)
        # Then dropped: in excluded_records with handling_action == "drop".
        for variable_id, info in self._excluded_records.items():
            if not isinstance(info, Mapping) or info.get("handling_action") != "drop":
                continue
            if variable_id.lower() in lowered:
                return CatalogAnswer(
                    DROPPED_OR_UNAVAILABLE_NOTE,
                    [],
                    audit_only=False,
                    analysis_queryable=False,
                )
        return None

    def _chat_no_compact_match(self, question: str) -> CatalogAnswer:
        """Resolve chat questions whose tokens didn't hit a compact card.

        The question may reference:
          * a *dropped* variable (in ``excluded_records``) — polite
            maintainer-contact, never expose ledger details;
          * a *source-only* variable (evidence pack present, no compact
            record) — answer metadata but flag analysis-not-queryable;
          * an unknown id — generic catalog miss.
        """
        question_tokens = _tokens(question)
        if not question_tokens:
            return CatalogAnswer(
                "I could not find a matching catalog variable for that question.",
                [],
            )

        # Source-only: evidence pack exists but no compact record.
        for variable_id, pack in self._evidence_packs.items():
            if variable_id in self._records:
                continue
            id_tokens = _tokens(variable_id)
            if not id_tokens or not (id_tokens & question_tokens):
                continue
            return self._source_only_answer(variable_id, pack)

        # Dropped: in excluded_records.
        for variable_id, info in self._excluded_records.items():
            id_tokens = _tokens(variable_id)
            if not id_tokens or not (id_tokens & question_tokens):
                continue
            if isinstance(info, Mapping) and info.get("handling_action") == "drop":
                return CatalogAnswer(
                    DROPPED_OR_UNAVAILABLE_NOTE,
                    [],
                    audit_only=False,
                    analysis_queryable=False,
                )

        # Truly unknown: treat the same way as a dropped variable for
        # polite chat. The two surfaces are intentionally
        # indistinguishable so the chat path doesn't leak which
        # variables existed-but-dropped vs. never-existed — both reduce
        # to "ask the maintainer".
        return CatalogAnswer(
            DROPPED_OR_UNAVAILABLE_NOTE,
            [],
            audit_only=False,
            analysis_queryable=False,
        )

    def _source_only_answer(self, variable_id: str, pack: Mapping[str, Any]) -> CatalogAnswer:
        """Compose a metadata answer for a source-only variable.

        The answer is metadata-shaped (so the LLM has something useful
        to say) plus a concise ``Note:`` flagging that it is not
        analysis-queryable. We do not expose ledger detail.
        """
        normalization_trace = pack.get("normalization_trace")
        label = variable_id
        if isinstance(normalization_trace, Mapping):
            trace_label = normalization_trace.get("label")
            if isinstance(trace_label, str) and trace_label.strip():
                label = trace_label

        parts = [f"{variable_id} is {label}."]
        # Provenance line if the pack has PDF page metadata. Stays
        # consistent with the dataset-backed answer composer's style.
        provenance = self._provenance_text(pack)
        if provenance:
            parts.append(provenance)
        parts.append(
            f"Note: {variable_id} is source-only (PDF/metadata) and is "
            "not analysis-queryable in the current dataset."
        )
        return CatalogAnswer(
            " ".join(parts),
            [variable_id],
            audit_only=False,
            analysis_queryable=False,
        )

    def _score(self, record: Mapping[str, Any], query: str, query_tokens: set[str]) -> int:
        record_id = _record_id(record)
        if record_id.lower() in query.lower():
            return 100
        terms = set(_tokens(record_id))
        for key in (
            "label",
            "display_label",
            "normalized_meaning",
            "section",
            "field_class",
            # Study-design tier-specific fields:
            "visit_name",
            "specimen_type",
            "criteria_type",
            "cohort",
            "population",
            "form_id",
            "cohort_id",
            "timing",
            "catalog_tier",
        ):
            value = record.get(key)
            if isinstance(value, str):
                terms.update(_tokens(value))
        for list_key in (
            "tests",
            "tests_performed",
            "specimens_collected",
            "forms_completed",
            "timeline",
            "related_variables",
        ):
            value = record.get(list_key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        terms.update(_tokens(item))
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
        return all(_record_id(record).lower() not in question.lower() for record in matches[:2])

    def _clarification(self, question: str, matches: list[Mapping[str, Any]]) -> CatalogAnswer:
        query_tokens = _tokens(question)
        catalog_terms: set[str] = set()
        for record in matches:
            for key in ("variable_id", "card_id", "label", "display_label", "normalized_meaning"):
                value = record.get(key)
                if isinstance(value, str):
                    catalog_terms.update(_tokens(value))
        focus = next(
            (token.upper() for token in sorted(query_tokens & catalog_terms) if len(token) > 2),
            "that",
        )
        candidates = ", ".join(
            f"{_record_id(record)} ({record.get('display_label') or record.get('label')})"
            for record in matches
        )
        return CatalogAnswer(
            f"Which {focus} variable do you mean? Candidates: {candidates}.",
            [_record_id(record) for record in matches],
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
        record_id = _record_id(record)
        label = _string(record.get("display_label") or record.get("label"), "label")
        parts = [f"{record_id} is {label}."]

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
