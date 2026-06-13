"""Deterministic source citations for study form variables.

Resolves a ``(form_id, field_id)`` pair to the exact ``file:line`` in the
PHI-scrubbed ``llm_source/`` tree where that variable is defined, so the agent
can cite real provenance instead of inventing it. This module backs the
``cite_source`` agent tool in :mod:`scripts.ai_assistant.agent_tools`.

Search precedence (first hit wins), per resolved form:

1. ``form_policy``    — ``SoT/<form>/pdf/<form>_policy.yaml`` (YAML variable key)
2. ``dataset_schema`` — ``SoT/<form>/dataset/<form>_schema.json`` (column ``name``)
3. ``llm_jsonl``      — ``dataset_schema/files/<form>.jsonl`` (column key, header
   line only — data rows are never echoed)

then, as a study-wide fallback:

4. ``study_config``   — ``study_metadata/study_variable_map.yaml`` (mapped column)

The form-policy YAML is the most authoritative definition of a field's
meaning, so it is consulted first; the per-form sources are tried before the
study-wide variable map because a form-scoped hit is the precise provenance a
citation needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import config

# Snippet length cap (chars). Matches the ~200-char contract documented on the
# cite_source tool so callers get a readable line without dumping long rows.
_SNIPPET_MAX = 200


class CitationNotFoundError(Exception):
    """Raised when no source location can be found for a ``(form, field)``."""


@dataclass(frozen=True)
class Citation:
    """A resolved source location for a form variable.

    Attributes mirror the JSON the ``cite_source`` tool emits.
    """

    file: str  # repo-relative path
    line: int  # 1-indexed
    snippet: str  # ~200 chars
    matched_term: str  # the field token that matched
    source_kind: str  # form_policy | dataset_schema | llm_jsonl | study_config


def _repo_rel(path: Path) -> str:
    """Render *path* relative to the repo root, falling back to the full path."""
    try:
        return str(path.resolve().relative_to(Path(config.BASE_DIR).resolve()))
    except ValueError:
        return str(path)


def _snippet(line: str) -> str:
    return line.strip()[:_SNIPPET_MAX]


def _first_match(path: Path, pattern: re.Pattern[str]) -> tuple[int, str] | None:
    """Return ``(1-indexed line, line text)`` of the first match, or ``None``."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if pattern.search(line):
                    return lineno, line
    except OSError:
        return None
    return None


def _resolve_form_dirs(form_id: str) -> list[Path]:
    """Resolve *form_id* to candidate SoT form directories, best match first.

    Accepts an exact directory name (``"95_SAE"``) or a leading fragment
    (``"98A"`` → ``"98A_FOA"``). If several candidates remain they are returned
    sorted and the caller disambiguates by which form actually contains the
    field.
    """
    sot = Path(config.LLM_SOURCE_SOT_DIR)
    if not sot.is_dir():
        return []
    dirs = sorted(p for p in sot.iterdir() if p.is_dir() and not p.name.startswith("."))
    fl = form_id.strip().lower()
    if not fl:
        return []
    exact = [p for p in dirs if p.name.lower() == fl]
    if exact:
        return exact
    return [p for p in dirs if p.name.lower().startswith(fl + "_") or p.name.lower().startswith(fl)]


def _cite_in_form_policy(form_dir: Path, field: str) -> Citation | None:
    """Find *field* as a YAML variable key in the form policy."""
    path = form_dir / "pdf" / f"{form_dir.name}_policy.yaml"
    pattern = re.compile(rf"^\s*{re.escape(field)}\s*:", re.IGNORECASE)
    found = _first_match(path, pattern)
    if found is None:
        return None
    lineno, line = found
    return Citation(_repo_rel(path), lineno, _snippet(line), field, "form_policy")


def _cite_in_dataset_schema(form_dir: Path, field: str) -> Citation | None:
    """Find *field* as a column ``name`` in the dataset schema JSON."""
    path = form_dir / "dataset" / f"{form_dir.name}_schema.json"
    pattern = re.compile(rf'"name"\s*:\s*"{re.escape(field)}"', re.IGNORECASE)
    found = _first_match(path, pattern)
    if found is None:
        return None
    lineno, line = found
    return Citation(_repo_rel(path), lineno, _snippet(line), field, "dataset_schema")


def _cite_in_jsonl(form_dir: Path, field: str) -> Citation | None:
    """Confirm *field* is a column key in the dataset JSONL header line.

    Only the first line is read (the column-structure record or first row),
    and the returned snippet names the column rather than echoing row content,
    so no data values leak into a citation.
    """
    path = Path(config.TRIO_DATASETS_DIR) / f"{form_dir.name}.jsonl"
    if not path.is_file():
        return None
    pattern = re.compile(rf'"{re.escape(field)}"\s*:', re.IGNORECASE)
    try:
        with path.open(encoding="utf-8") as fh:
            header = fh.readline()
    except OSError:
        return None
    if not pattern.search(header):
        return None
    snippet = f'column "{field}" present in {path.name}'
    return Citation(_repo_rel(path), 1, snippet, field, "llm_jsonl")


def _cite_in_study_config(field: str) -> Citation | None:
    """Find *field* as a mapped column in the study variable map.

    GAP-9: anchored to structural positions where a column name is a value
    of a ``column:`` / ``backing_column:`` key, or a mapping key of a
    concept block — mirroring the anchored patterns in
    :func:`_cite_in_form_policy` and :func:`_cite_in_dataset_schema`.
    A free word-boundary substring match would return coincidental wrong
    citations (e.g. a field named "AGE" hitting a line containing "CAGE").
    We prefer returning no citation over a wrong one.
    """
    path = Path(config.LLM_SOURCE_STUDY_METADATA_DIR) / "study_variable_map.yaml"
    # Match the field when it appears as:
    #   column: FIELD_NAME          (mapping value after "column:" key)
    #   backing_column: FIELD_NAME  (mapping value after "backing_column:" key)
    #   FIELD_NAME:                 (a YAML mapping key — concept-block entry)
    # All patterns are case-insensitive and anchor to column-name boundaries
    # (whitespace, quotes, or end-of-token) to avoid partial-word coincidences.
    esc = re.escape(field)
    pattern = re.compile(
        rf"(?:"
        # value of column/backing_column — quoted form is exact; the unquoted form
        # needs an explicit end-of-token boundary, else field 'AGE' would match
        # `column: AGE_AT_ENROLL` (optional quotes can't bound the right edge).
        rf"(?:column|backing_column)\s*:\s*(?:['\"]{esc}['\"]|{esc}(?![\w-]))"
        rf"|^\s*{esc}\s*:"  # YAML mapping key at line start
        rf")",
        re.IGNORECASE,
    )
    found = _first_match(path, pattern)
    if found is None:
        return None
    lineno, line = found
    return Citation(_repo_rel(path), lineno, _snippet(line), field, "study_config")


def cite_variable(form_id: str, field_id: str) -> Citation:
    """Return the source location where *field_id* is defined for *form_id*.

    Args:
        form_id: A SoT form identifier — exact (``"95_SAE"``) or a leading
            fragment (``"98A"``).
        field_id: The dataset column / policy variable name (e.g. ``"AE_AGE"``).

    Returns:
        A :class:`Citation` for the first source (in precedence order) that
        defines the field.

    Raises:
        CitationNotFoundError: If the form cannot be resolved or the field is absent
            from every source — callers must surface this rather than guess.
    """
    field = field_id.strip()
    if not field:
        raise CitationNotFoundError("field_id is required")

    form_dirs = _resolve_form_dirs(form_id)
    if not form_dirs:
        raise CitationNotFoundError(f"no SoT form matches form_id {form_id!r}")

    per_form_finders = (_cite_in_form_policy, _cite_in_dataset_schema, _cite_in_jsonl)
    for form_dir in form_dirs:
        for finder in per_form_finders:
            citation = finder(form_dir, field)
            if citation is not None:
                return citation

    study_citation = _cite_in_study_config(field)
    if study_citation is not None:
        return study_citation

    forms = ", ".join(p.name for p in form_dirs)
    raise CitationNotFoundError(f"field {field!r} not found in source for form(s): {forms}")
