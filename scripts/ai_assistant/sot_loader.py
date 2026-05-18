"""Lean SoT YAML loader — find, load, and summarise source-of-truth YAMLs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Validation dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    """A single invariant violation found by validate()."""

    code: str     # short kebab-case id, e.g. "section-ref-missing"
    path: str     # YAML path like "variables.HIV_HIV.section"
    message: str


@dataclass
class ValidationReport:
    """Aggregate result returned by validate()."""

    passed: bool
    errors: list[ValidationError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers for validate()
# ---------------------------------------------------------------------------

# NOTE: To avoid false positives on prose acronyms (HIV, ART, CD4) and
# instruction ids (I1, I2), the skip_logic variable-reference check is
# restricted to tokens that CONTAIN an underscore.  This catches all
# dataset column names like HIV_HIVDAT, VAR_A, etc. while silently
# ignoring bare acronyms and short instruction tokens.
_SKIP_LOGIC_VAR_RE = re.compile(r"\b[A-Z][A-Z0-9_]*_[A-Z0-9_]+\b")

# Instruction id pattern e.g. I1, I12
_INSTR_ID_RE = re.compile(r"\bI\d+\b")

# Pattern to extract a variable name from the start of an arrow string.
# Matches names like HIV_ARTTX or SUBJID (≥3 uppercase letters with optional
# underscore body) — used for both underscore-bearing and non-underscore
# names that are clearly identifiers (≥3 chars, all uppercase/digits).
_ARROW_VAR_RE = re.compile(
    r"^([A-Z][A-Z0-9]*_[A-Z0-9_]+|[A-Z][A-Z0-9]{2,})"
)

# Allowlist for jitter_date variable names. Some CRF exports use COMPDTE for
# completion date instead of COMPDAT.
_JITTER_DATE_ALLOWLIST_RE = re.compile(r"(_COMPDAT|_COMPDTE|_VISIT|_SIGNDAT|_ENTDAT)$")

# Allowed types for phi: drop
_DROP_ALLOWED_TYPES = {"signature", "initials", "datetime"}


def _extract_arrow_var(endpoint: Any) -> str | None:
    """Return the variable name encoded in an arrow from/to endpoint.

    *endpoint* is either:
      - a dict with optional keys ``variable`` and ``option``.
      - a string of the form ``"VARNAME (option)"`` or ``"VARNAME"`` or a
        descriptive phrase like ``"instruction I2"``.

    Returns None when the endpoint is a descriptive phrase (e.g. starts with
    "instruction ") so the caller can skip the check.
    """
    if isinstance(endpoint, dict):
        return endpoint.get("variable")  # may be None

    if not isinstance(endpoint, str):
        return None

    # Descriptive phrases like "instruction I2" are not variable refs.
    if endpoint.strip().lower().startswith("instruction "):
        return None

    m = _ARROW_VAR_RE.match(endpoint.strip())
    return m.group(1) if m else None


def _extract_arrow_option(endpoint: Any) -> str | None:
    """Return the option text encoded in an arrow from endpoint, or None."""
    if isinstance(endpoint, dict):
        return endpoint.get("option")

    if not isinstance(endpoint, str):
        return None

    m = re.match(r"^[A-Z][A-Z0-9_]*\s*\((.+)\)\s*$", endpoint.strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Public validate() function
# ---------------------------------------------------------------------------


def validate(data: dict[str, Any]) -> ValidationReport:
    """Validate a loaded lean YAML dict against the 10 structural invariants.

    Returns a :class:`ValidationReport`.  Never raises — all problems are
    reported as :class:`ValidationError` entries.

    Invariant codes:
      (a) section-ref-missing
      (b) skip-logic-var-ref-missing
      (c) mutex-reciprocity-broken
      (d) arrow-var-ref-missing
      (e) arrow-option-ref-missing
      (f) instruction-id-ref-missing
      (g) free-text-phi-undeclared
      (h) jitter-date-allowlist-violation
      (i) drop-typing-violation
      (j) pseudonymize-typing-violation
    """
    errors: list[ValidationError] = []

    if not isinstance(data, dict):
        errors.append(ValidationError(
            code="malformed-root",
            path="$",
            message="root is not a mapping",
        ))
        return ValidationReport(passed=False, errors=errors)

    sections: dict = data.get("sections") or {}
    variables: dict = data.get("variables") or {}
    instructions_list: list = data.get("instructions") or []
    arrows: list = data.get("arrows") or []

    if not isinstance(sections, dict):
        sections = {}
    if not isinstance(variables, dict):
        variables = {}
    if not isinstance(instructions_list, list):
        instructions_list = []
    if not isinstance(arrows, list):
        arrows = []

    section_keys: set[str] = set(sections.keys())
    variable_keys: set[str] = set(variables.keys())
    instruction_ids: set[str] = {
        entry["id"]
        for entry in instructions_list
        if isinstance(entry, dict) and "id" in entry
    }

    # -----------------------------------------------------------------------
    # (a) section-ref-missing
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        section_val = var_meta.get("section")
        if section_val not in section_keys:
            errors.append(ValidationError(
                code="section-ref-missing",
                path=f"variables.{var_name}.section",
                message=(
                    f"section {section_val!r} is not a key in top-level sections "
                    f"(known: {sorted(section_keys)!r})"
                ),
            ))

    # -----------------------------------------------------------------------
    # (b) skip-logic-var-ref-missing
    # NOTE: Only tokens containing an underscore are checked to avoid false
    # positives on prose acronyms (HIV, ART, CD4) and instruction ids (I1/I2).
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        skip_logic = var_meta.get("skip_logic")
        if not isinstance(skip_logic, str):
            continue
        errors.extend(
            ValidationError(
                code="skip-logic-var-ref-missing",
                path=f"variables.{var_name}.skip_logic",
                message=(
                    f"token {token!r} looks like a variable reference "
                    f"(contains '_') but is not in variables"
                ),
            )
            for token in _SKIP_LOGIC_VAR_RE.findall(skip_logic)
            if token not in variable_keys
        )

    # -----------------------------------------------------------------------
    # (c) mutex-reciprocity-broken
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        skip_logic = var_meta.get("skip_logic")
        if not isinstance(skip_logic, str):
            continue
        if "mutually exclusive with" not in skip_logic:
            continue
        # Extract the variable(s) named after "mutually exclusive with"
        after = skip_logic[skip_logic.index("mutually exclusive with") + len("mutually exclusive with"):]
        partners = _SKIP_LOGIC_VAR_RE.findall(after)
        for partner in partners:
            partner_meta = variables.get(partner)
            if not isinstance(partner_meta, dict):
                continue  # partner doesn't exist; (b) will catch that
            partner_skip = partner_meta.get("skip_logic") or ""
            if not re.search(
                r"mutually exclusive with\s+" + re.escape(var_name) + r"\b",
                partner_skip,
            ):
                errors.append(ValidationError(
                    code="mutex-reciprocity-broken",
                    path=f"variables.{var_name}.skip_logic",
                    message=(
                        f"{var_name!r} declares mutex with {partner!r} but "
                        f"{partner!r}.skip_logic does not reciprocate"
                    ),
                ))

    # -----------------------------------------------------------------------
    # (d) arrow-var-ref-missing
    # -----------------------------------------------------------------------
    for idx, arrow in enumerate(arrows):
        if not isinstance(arrow, dict):
            continue
        for endpoint_key in ("from", "to"):
            endpoint = arrow.get(endpoint_key)
            var_name = _extract_arrow_var(endpoint)
            if var_name is None:
                continue  # descriptive phrase or missing — skip
            if var_name not in variable_keys:
                errors.append(ValidationError(
                    code="arrow-var-ref-missing",
                    path=f"arrows[{idx}].{endpoint_key}",
                    message=(
                        f"variable {var_name!r} referenced in arrow endpoint "
                        f"is not in variables"
                    ),
                ))

    # -----------------------------------------------------------------------
    # (e) arrow-option-ref-missing
    # Be conservative: only check when the source variable has an options list
    # AND the parenthesized text can be an exact-match candidate.
    # -----------------------------------------------------------------------
    for idx, arrow in enumerate(arrows):
        if not isinstance(arrow, dict):
            continue
        from_endpoint = arrow.get("from")
        var_name = _extract_arrow_var(from_endpoint)
        if var_name is None:
            continue
        option_text = _extract_arrow_option(from_endpoint)
        if option_text is None:
            continue
        source_var = variables.get(var_name)
        if not isinstance(source_var, dict):
            continue
        options = source_var.get("options")
        if not isinstance(options, list) or not options:
            # No options on this variable — skip conservatively
            continue
        if option_text not in options:
            errors.append(ValidationError(
                code="arrow-option-ref-missing",
                path=f"arrows[{idx}].from",
                message=(
                    f"option {option_text!r} from arrow endpoint is not in "
                    f"{var_name}.options {options!r}"
                ),
            ))

    # -----------------------------------------------------------------------
    # (f) instruction-id-ref-missing
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        skip_logic = var_meta.get("skip_logic")
        if not isinstance(skip_logic, str):
            continue
        errors.extend(
            ValidationError(
                code="instruction-id-ref-missing",
                path=f"variables.{var_name}.skip_logic",
                message=(
                    f"instruction id {token!r} referenced in skip_logic "
                    f"is not in instructions (known: {sorted(instruction_ids)!r})"
                ),
            )
            for token in _INSTR_ID_RE.findall(skip_logic)
            if token not in instruction_ids
        )

    # -----------------------------------------------------------------------
    # (g) free-text-phi-undeclared
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        if var_meta.get("type") != "free_text":
            continue
        phi_val = var_meta.get("phi")
        notes_val = var_meta.get("notes") or ""
        has_phi = phi_val is not None
        has_no_phi_note = "no PHI expected" in (notes_val if isinstance(notes_val, str) else "")
        if not has_phi and not has_no_phi_note:
            errors.append(ValidationError(
                code="free-text-phi-undeclared",
                path=f"variables.{var_name}",
                message=(
                    f"free_text variable {var_name!r} must have either a non-null "
                    f"phi: field or notes: containing 'no PHI expected'"
                ),
            ))

    # -----------------------------------------------------------------------
    # (h) jitter-date-allowlist-violation
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        if var_meta.get("phi") != "jitter_date":
            continue
        if not _JITTER_DATE_ALLOWLIST_RE.search(var_name):
            errors.append(ValidationError(
                code="jitter-date-allowlist-violation",
                path=f"variables.{var_name}.phi",
                message=(
                    f"variable {var_name!r} has phi: jitter_date but its name "
                    f"does not match the allowlist regex "
                    f"(_COMPDAT|_COMPDTE|_VISIT|_SIGNDAT|_ENTDAT)$"
                ),
            ))

    # -----------------------------------------------------------------------
    # (i) drop-typing-violation
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        if var_meta.get("phi") != "drop":
            continue
        var_type = var_meta.get("type")
        if var_type not in _DROP_ALLOWED_TYPES:
            errors.append(ValidationError(
                code="drop-typing-violation",
                path=f"variables.{var_name}.type",
                message=(
                    f"variable {var_name!r} has phi: drop but type {var_type!r} "
                    f"is not in the allowed set {sorted(_DROP_ALLOWED_TYPES)!r}"
                ),
            ))

    # -----------------------------------------------------------------------
    # (j) pseudonymize-typing-violation
    # -----------------------------------------------------------------------
    for var_name, var_meta in variables.items():
        if not isinstance(var_meta, dict):
            continue
        if var_meta.get("phi") != "pseudonymize":
            continue
        var_type = var_meta.get("type")
        notes_val = var_meta.get("notes")
        if var_type == "identifier" or (var_type == "code" and notes_val and isinstance(notes_val, str) and notes_val.strip()):
            pass  # OK
        else:
            errors.append(ValidationError(
                code="pseudonymize-typing-violation",
                path=f"variables.{var_name}.type",
                message=(
                    f"variable {var_name!r} has phi: pseudonymize but must be "
                    f"type: identifier OR type: code with a non-empty notes: field "
                    f"(got type={var_type!r}, notes={notes_val!r})"
                ),
            ))

    return ValidationReport(passed=len(errors) == 0, errors=errors)


def find_lean_yaml(
    study: str,
    form: str | None,
    repo_root: Path,
) -> list[Path]:
    """Return lean YAML paths under output/<study>/llm_source/source_truth/.

    If *form* is given, returns the single matching ``<form>_policy.lean.yaml``
    (empty list when absent).  If *form* is None, returns all
    ``*_policy.lean.yaml`` files in that directory, sorted by name.
    """
    sot_dir = repo_root / "output" / study / "llm_source" / "source_truth"
    if not sot_dir.is_dir():
        return []
    if form is not None:
        candidate = sot_dir / f"{form}_policy.lean.yaml"
        return [candidate] if candidate.exists() else []
    return sorted(sot_dir.glob("*_policy.lean.yaml"))


def load_lean_yaml(path: Path) -> dict[str, Any]:
    """Load a lean YAML file and return its contents as a dict.

    Raises ``ValueError`` when the file is missing, unreadable, or structurally
    invalid (root not a dict, or missing the required *variables* key).
    """
    if not path.exists():
        raise ValueError(f"Lean YAML not found: {path}")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping at root of {path}, got {type(data).__name__}")
    if "variables" not in data:
        raise ValueError(f"Required key 'variables' missing in {path}")
    return data  # type: ignore[return-value]


def summarize_lean(data: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary view of a loaded lean YAML.

    Includes top-level metadata, section/variable counts, per-variable metadata
    (no dataset row values), and pass-through of instructions/arrows/discrepancies.
    """
    form_block = data.get("form", {})
    if isinstance(form_block, dict):
        form_summary = {
            "number": form_block.get("number"),
            "title": form_block.get("title"),
        }
    else:
        form_summary = {"number": None, "title": str(form_block)}

    sections = data.get("sections", {})
    raw_variables = data.get("variables", {})

    keep_fields = {
        "section",
        "pdf_question",
        "widget",
        "type",
        "options",
        "skip_logic",
        "phi",
        "pdf_label",
        "pdf_subsection",
        "format",
        "units",
        "precision",
        "notes",
    }

    variables: dict[str, Any] = {}
    for var_name, var_data in raw_variables.items():
        if not isinstance(var_data, dict):
            variables[var_name] = var_data
            continue
        variables[var_name] = {k: v for k, v in var_data.items() if k in keep_fields}

    summary: dict[str, Any] = {
        "study": data.get("study", ""),
        "form": form_summary,
        "section_count": len(sections) if isinstance(sections, dict) else 0,
        "variable_count": len(variables),
        "variables": variables,
    }
    for passthrough in ("instructions", "arrows", "discrepancies"):
        if passthrough in data:
            summary[passthrough] = data[passthrough]
    return summary
