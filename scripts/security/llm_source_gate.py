"""Deterministic pre-publication scans for LLM-visible dataset artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.security.phi_patterns import BLOCKING_PATTERNS, SUBJECT_ID_PATTERNS

__all__ = [
    "LeakScanFinding",
    "LeakScanResult",
    "scan_tree_for_phi",
]

_MAX_FINDINGS = 200


@dataclass(frozen=True)
class LeakScanFinding:
    """A value-free leak-scan finding.

    The matched value is deliberately omitted so reports cannot become a PHI
    side channel.
    """

    relative_path: str
    line_number: int
    pattern_name: str


@dataclass(frozen=True)
class LeakScanResult:
    ok: bool
    findings: tuple[LeakScanFinding, ...]

    @property
    def detail(self) -> str:
        if self.ok:
            return ""
        first = self.findings[0]
        more = f" (+{len(self.findings) - 1} more)" if len(self.findings) > 1 else ""
        return (
            f"phi pattern {first.pattern_name} matched in "
            f"{first.relative_path} line {first.line_number} "
            f"(matched content omitted){more}"
        )


# Blocking for published dataset artifacts only, not the chat-time agent
# gate (phi_gate.py) — that gate deliberately trades recall for precision
# and leaves DATE_MDY / bare 8-digit numerics at WARN tier. A published
# artifact gets no such leniency: Step 1 guarantees every date-column value
# is either jittered, a declared sentinel, or null, so any compact/slash
# date-shaped string surviving in a *date-classified* field is raw PHI.
_ARTIFACT_BLOCKING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("DATE_COMPACT", re.compile(r"(?<!\d)\d{8}(?!\d)")),
    ("DATE_SLASH", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
]


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    return list(BLOCKING_PATTERNS) + [
        (f"SUBJECT_ID[{i}]", pattern) for i, pattern in enumerate(SUBJECT_ID_PATTERNS)
    ]


# ── Step 9b: narrow structural-declaration allowlist for the SoT leg ──────
# Evidence-based (see phi-pipeline-hardening-plan.md Step 9 contingency):
# every SUBJECT_ID[2] ("FID") hit measured against the real Indo-VAP SoT
# tree resolves to one of two verified-structural declarations that name
# the column/variable itself, never a printed value:
#   * dataset/*_schema.json               — a `"name": "FID..."` column-
#     name field, one field per pretty-printed line (fixed shape, emitted
#     only by Tier-1 header-only generators).
#   * joined/*_joined_query_view.yaml and pdf/*_policy.yaml — the bare
#     `variables:` mapping key itself (e.g. `  FID2:`), two-space indented
#     and colon-terminated with nothing else on the line.
# A `discrepancies[].dataset_column_binding[]` list entry (`- FID2`, dash-
# prefixed, no trailing colon) is a DIFFERENT line shape and is
# deliberately NOT matched here — per the plan, anything appearing under
# `discrepancies`, `pdf_question`, or `widget` is subject-adjacent CRF
# content and must stay BLOCKING, fixed at the source YAML instead.
_SOT_SCHEMA_COLUMN_NAME_LINE = re.compile(r'^\s*"name":\s*"[A-Za-z][A-Za-z0-9_]*"\s*,?\s*$')
_SOT_VARIABLE_KEY_LINE = re.compile(r"^\s{2}[A-Za-z][A-Za-z0-9_]*:\s*$")
_FID_PATTERN_NAME = next(
    (
        f"SUBJECT_ID[{i}]"
        for i, pat in enumerate(SUBJECT_ID_PATTERNS)
        if pat.pattern == r"\bFID\d*\b"
    ),
    None,
)


def _sot_structural_line_allowed(fpath: Path, line: str, pattern_name: str) -> bool:
    """True only for a verified structural column/variable-name declaration
    in a published SoT artifact (Step 9b) — never a blanket suppression.
    Scoped to the exact FID pattern hit and the exact SoT filename shapes
    measured; any other pattern or file stays fail-closed (blocking).
    """
    if pattern_name != _FID_PATTERN_NAME or _FID_PATTERN_NAME is None:
        return False
    name = fpath.name
    if name.endswith("_schema.json"):
        return bool(_SOT_SCHEMA_COLUMN_NAME_LINE.match(line))
    if name.endswith("_joined_query_view.yaml") or name.endswith("_policy.yaml"):
        return bool(_SOT_VARIABLE_KEY_LINE.match(line))
    return False


# ── Step 9a follow-up: narrow field-name allowlist for dictionary
# documentation columns ─────────────────────────────────────────────────
# Evidence-based (2026-08-11 dictionary-leg review — matched text read and
# confirmed benign, never a subject value): every DATE_ISO/URL hit measured
# against the real Indo-VAP dictionary leg resolves to one of three fixed,
# study-authored documentation fields describing the STUDY'S OWN
# coding/naming conventions:
#   * "Variable Endings" (Codelists_table_2) — a naming-convention legend
#     row, e.g. "Use 1900-01-01 as an Unknown date".
#   * "Group notes / thoughts" (Codelists_table_3) and "Notes" (per-form
#     dictionary tables, e.g. tblDIS_table/tblMED_table) — free-text
#     citations to external public coding standards (WHO ATC drug codes,
#     WHO ICD-11 disease codes, Wikipedia ISO country codes).
# Scoped to dictionary-leg files ONLY — filename ends "_table.jsonl" or
# "_table_<N>.jsonl", the fixed shape dictionary staging always uses;
# dataset staging files never match this pattern (confirmed: they are
# named `<code>_<Form>.jsonl` with no "_table" suffix) — to exactly these
# three field names — AND to exactly the two pattern types actually
# observed and confirmed benign (DATE_ISO, URL). Any other pattern (a
# subject-ID shape, an email, a phone number, ...) in one of these fields
# still blocks — this is not a blanket per-field exemption. Any other
# field, file shape, or pattern stays fail-closed (blocking).
_DICTIONARY_DOCUMENTATION_FIELDS = frozenset(
    {"Notes", "Group notes / thoughts", "Variable Endings"}
)
_DICTIONARY_DOCUMENTATION_ALLOWED_PATTERNS = frozenset({"DATE_ISO", "URL"})
_DICTIONARY_TABLE_FILENAME = re.compile(r".*_table(?:_\d+)?\.jsonl$")


def _dictionary_documentation_field_allowed(fpath: Path, prefix: str, pattern_name: str) -> bool:
    """True only for one of the two verified-benign pattern types matching
    a declared study-documentation field in a dictionary table (Step 9a
    follow-up) — never a blanket suppression. Any other field name, file
    shape, or pattern type stays fail-closed (blocking)."""
    if pattern_name not in _DICTIONARY_DOCUMENTATION_ALLOWED_PATTERNS:
        return False
    if prefix not in _DICTIONARY_DOCUMENTATION_FIELDS:
        return False
    return bool(_DICTIONARY_TABLE_FILENAME.match(fpath.name))


_cached_transformed: dict[str, dict[str, list[str]]] | None = None
_transformed_loaded = False
_cached_scrub_cfg = None
_scrub_cfg_loaded = False


def _load_transformed_manifest() -> dict[str, dict[str, list[str]]]:
    """Load ``phi_scrub_transformed.json`` (Step 8) — cached per process.

    Missing or unreadable manifest -> empty mapping -> every DATE_* hit is
    blocked (fail-closed), matching the prior ``except Exception`` posture.
    """
    global _cached_transformed, _transformed_loaded
    if _transformed_loaded:
        return _cached_transformed or {}
    _transformed_loaded = True
    try:
        import config
        from scripts.security.phi_scrub import PHI_TRANSFORMED_FILENAME

        manifest_path = Path(config.AUDIT_SCRUB_REPORT_PATH).parent / PHI_TRANSFORMED_FILENAME
        with manifest_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        _cached_transformed = raw if isinstance(raw, dict) else {}
    except Exception:
        _cached_transformed = {}
    return _cached_transformed


def _load_scrub_cfg():
    """Load the compiled scrub rule catalog — cached per process. Used only
    to SCOPE which fields the artifact-only patterns (Step 8) apply to
    (avoids flagging a legitimate non-date 8-digit value, e.g. a lab
    accession number, in a field no date rule ever matched). Never used to
    decide whether a hit is *allowed* — that is the manifest's job."""
    global _cached_scrub_cfg, _scrub_cfg_loaded
    if not _scrub_cfg_loaded:
        try:
            from scripts.security.phi_scrub import load_scrub_config

            _cached_scrub_cfg = load_scrub_config()
        except Exception:
            _cached_scrub_cfg = None
        _scrub_cfg_loaded = True
    return _cached_scrub_cfg


def _is_allowed_scrubbed_date(path: str, *, dataset_file: str | None) -> bool:
    """Return True for a field the scrubber PROVABLY transformed a value
    for in *dataset_file* (Step 8) — evidence of transformation, not mere
    rule-membership. A field "classified" as a date rule but never actually
    transformed (the D2 bug: 24,669 raw dates published because
    ``shift_date`` silently returned the raw value) no longer passes.
    """
    field = path.rsplit(".", 1)[-1]
    if field == "extraction_utc" and path.startswith("_provenance."):
        return True
    if dataset_file is None:
        return False
    manifest = _load_transformed_manifest()
    per_file = manifest.get(dataset_file)
    if not per_file:
        return False
    return field in per_file.get("date", ())


def _field_is_date_classified(path: str) -> bool:
    """Return True when the compiled scrub config's ``date_fields`` rules
    (or birthdate field) match this field NAME — the scoping set for the
    artifact-only patterns, independent of transformation evidence."""
    field = path.rsplit(".", 1)[-1]
    cfg = _load_scrub_cfg()
    if cfg is None:
        return False
    return cfg.field_is_date(field) or cfg.field_is_birthdate(field)


def _scan_json_line(
    *,
    root: Path,
    fpath: Path,
    line: str,
    line_number: int,
    patterns: list[tuple[str, re.Pattern[str]]],
    artifact_patterns: list[tuple[str, re.Pattern[str]]],
    dataset_file: str | None,
) -> list[LeakScanFinding]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return []

    findings: list[LeakScanFinding] = []

    def _relpath() -> str:
        try:
            return str(fpath.relative_to(root))
        except ValueError:
            return fpath.name

    def _walk(obj: object, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                _walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                _walk(value, f"{prefix}[{index}]")
        elif isinstance(obj, str):
            # Artifact-only patterns (DATE_COMPACT / DATE_SLASH) apply only
            # to fields the compiled rule catalog classifies as date-shaped
            # for THIS field name (scoping — avoids false-positiving on a
            # legitimate 8-digit non-date value elsewhere). Every DATE_-
            # prefixed pattern — including these two — is then exempted
            # from blocking through the SAME manifest-backed evidence check
            # as any other date pattern; a field with no transformation
            # evidence stays blocked (the exact D2 fail-open this closes).
            applicable = patterns
            if _field_is_date_classified(prefix):
                applicable = patterns + artifact_patterns
            allowed_date = _is_allowed_scrubbed_date(prefix, dataset_file=dataset_file)
            for pattern_name, pattern in applicable:
                if not pattern.search(obj):
                    continue
                if pattern_name.startswith("DATE_") and allowed_date:
                    continue
                if _dictionary_documentation_field_allowed(fpath, prefix, pattern_name):
                    continue
                findings.append(
                    LeakScanFinding(
                        relative_path=_relpath(), line_number=line_number, pattern_name=pattern_name
                    )
                )

    _walk(payload)
    return findings


def scan_tree_for_phi(root: Path) -> LeakScanResult:
    """Scan a tree for blocking PHI patterns without returning matched values.

    Collects up to :data:`_MAX_FINDINGS` findings instead of stopping at
    the first hit, so a single scan reports the full scope of a leak.
    """
    root = Path(root)
    if not root.is_dir():
        return LeakScanResult(ok=True, findings=())

    findings: list[LeakScanFinding] = []
    patterns = _patterns()
    artifact_patterns = _ARTIFACT_BLOCKING_PATTERNS
    for fpath in sorted(root.rglob("*")):
        if len(findings) >= _MAX_FINDINGS:
            break
        if not fpath.is_file():
            continue
        dataset_file = fpath.name if fpath.suffix == ".jsonl" else None
        try:
            with fpath.open(encoding="utf-8", errors="replace") as fh:
                for line_number, line in enumerate(fh, start=1):
                    if len(findings) >= _MAX_FINDINGS:
                        break
                    if fpath.suffix == ".jsonl":
                        findings.extend(
                            _scan_json_line(
                                root=root,
                                fpath=fpath,
                                line=line,
                                line_number=line_number,
                                patterns=patterns,
                                artifact_patterns=artifact_patterns,
                                dataset_file=dataset_file,
                            )
                        )
                        continue
                    for pattern_name, pattern in patterns:
                        if pattern.search(line):
                            if _sot_structural_line_allowed(fpath, line, pattern_name):
                                continue
                            try:
                                relative_path = str(fpath.relative_to(root))
                            except ValueError:
                                relative_path = fpath.name
                            findings.append(
                                LeakScanFinding(
                                    relative_path=relative_path,
                                    line_number=line_number,
                                    pattern_name=pattern_name,
                                )
                            )
                            break
        except OSError as exc:
            findings.append(
                LeakScanFinding(
                    relative_path=str(fpath),
                    line_number=0,
                    pattern_name=f"read_error:{exc.__class__.__name__}",
                )
            )

    return LeakScanResult(ok=not findings, findings=tuple(findings[:_MAX_FINDINGS]))


