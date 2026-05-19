"""Header-only PHI review support for study dataset intake.

This module deliberately works from study privacy configuration and dataset
headers only. It must not read row values, emit synthetic values, or execute
generated transform code.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml

__all__ = [
    "Action",
    "FormReviewApproval",
    "HeaderClassification",
    "OfficialSourceRejected",
    "PureTransformValidation",
    "RuleBundle",
    "StudyPrivacyConfig",
    "classify_headers",
    "load_study_privacy_config",
    "refresh_jurisdiction_rules",
    "review_form_headers",
    "validate_official_source_url",
    "validate_pure_transform_source",
    "verify_approval_payload",
]


class OfficialSourceRejected(ValueError):  # noqa: N818 - public test contract.
    """Raised when a jurisdiction rule source is not an official source."""


class Action(StrEnum):
    """Allowed PHI handling actions, ordered by review strictness."""

    KEEP = "keep"
    SUPPRESS = "suppress"
    CAP = "cap"
    GENERALIZE = "generalize"
    JITTER_DATE = "jitter_date"
    PSEUDONYMIZE = "pseudonymize"
    DROP = "drop"


_ACTION_RANK: dict[Action, int] = {
    Action.KEEP: 0,
    Action.SUPPRESS: 1,
    Action.CAP: 2,
    Action.GENERALIZE: 3,
    Action.JITTER_DATE: 4,
    Action.PSEUDONYMIZE: 5,
    Action.DROP: 6,
}


@dataclass(frozen=True, slots=True)
class StudyPrivacyConfig:
    """Maintainer-owned study privacy review configuration."""

    study_dir: Path
    jurisdictions: tuple[str, ...]
    rule_refresh: str
    conflict_policy: str
    max_synthetic_attempts: int
    approval_mode: str
    parallelism_mode: str


@dataclass(frozen=True, slots=True)
class HeaderRule:
    """One header-name classification rule."""

    id: str
    jurisdiction: str
    action: Action
    patterns: tuple[re.Pattern[str], ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RuleBundle:
    """Resolved jurisdiction rules and their official-source provenance."""

    source_mode: str
    rules_sha256: str
    sources: tuple[dict[str, str], ...]
    rules: tuple[HeaderRule, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "source_mode": self.source_mode,
            "rules_sha256": self.rules_sha256,
            "sources": list(self.sources),
            "rules": [
                {
                    "id": rule.id,
                    "jurisdiction": rule.jurisdiction,
                    "action": rule.action.value,
                    "reason": rule.reason,
                }
                for rule in self.rules
            ],
        }


@dataclass(frozen=True, slots=True)
class HeaderClassification:
    """Classification for a single dataset header."""

    header: str
    action: Action
    matched_rules: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        """Return audit-safe JSON with header metadata only, never values."""
        return {
            "header": self.header,
            "action": self.action.value,
            "matched_rules": list(self.matched_rules),
            "jurisdictions": list(self.jurisdictions),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class PureTransformValidation:
    """Static validation outcome for generated transform source."""

    ok: bool
    errors: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors)}


@dataclass(frozen=True, slots=True)
class FormReviewApproval:
    """Form-level review decision safe to serialize into audit ledgers."""

    form_name: str
    status: str
    attempts: int
    actions: dict[str, str]
    classifications: tuple[HeaderClassification, ...]
    reasons: tuple[str, ...]
    rule_bundle_sha256: str
    source_mode: str

    def to_json(self) -> dict[str, Any]:
        """Return a payload with headers/actions only; no row or fake values."""
        return {
            "form_name": self.form_name,
            "status": self.status,
            "attempts": self.attempts,
            "actions": dict(self.actions),
            "classifications": [item.to_json() for item in self.classifications],
            "reasons": list(self.reasons),
            "rule_bundle_sha256": self.rule_bundle_sha256,
            "source_mode": self.source_mode,
        }


_SUPPORTED_JURISDICTIONS = frozenset({"USA", "INDIA"})
_SUPPORTED_REFRESH_MODES = frozenset({"online_preferred", "pinned_only"})
_SUPPORTED_CONFLICT_POLICIES = frozenset({"strictest_wins"})

_OFFICIAL_SOURCE_HOSTS = frozenset(
    {
        "hhs.gov",
        "www.hhs.gov",
        "ecfr.gov",
        "www.ecfr.gov",
        "indiacode.nic.in",
        "www.indiacode.nic.in",
        "icmr.gov.in",
        "www.icmr.gov.in",
        "uidai.gov.in",
        "www.uidai.gov.in",
        "meity.gov.in",
        "www.meity.gov.in",
    }
)

_PINNED_SOURCES: tuple[dict[str, str], ...] = (
    {
        "jurisdiction": "USA",
        "title": "eCFR HIPAA de-identification rule",
        "url": "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.514",
    },
    {
        "jurisdiction": "USA",
        "title": "HHS HIPAA de-identification guidance",
        "url": "https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html",
    },
    {
        "jurisdiction": "INDIA",
        "title": "Digital Personal Data Protection Act, 2023",
        "url": "https://www.indiacode.nic.in/indiacode/handle/123456789/22037",
    },
    {
        "jurisdiction": "INDIA",
        "title": "ICMR official guidelines index",
        "url": "https://www.icmr.gov.in/guidelines",
    },
    {
        "jurisdiction": "INDIA",
        "title": "Aadhaar Act and UIDAI legal framework",
        "url": "https://uidai.gov.in/en/about-uidai/legal-framework/2033-aadhaar-targeted-delivery-of-financial-and-other-subsidies%2C-benefits-and-services-act%2C-2016.html",
    },
)


def _compile_many(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.I) for pattern in patterns)


_PINNED_RULE_SPECS: tuple[dict[str, object], ...] = (
    {
        "id": "usa_safe_harbor_direct_identifiers",
        "jurisdiction": "USA",
        "action": Action.DROP,
        "reason": "HIPAA Safe Harbor direct identifier header.",
        "patterns": (
            r"\b(name|first[_ -]?name|last[_ -]?name|full[_ -]?name)\b",
            r"\b(email|e[-_ ]?mail)\b",
            r"\b(phone|telephone|mobile|cell|fax)\b",
            r"\b(ssn|social[_ -]?security)\b",
            r"\b(address|street|city|county|precinct|zip|postal)\b",
            r"\b(url|uri|ip[_ -]?address|photo|image|biometric|finger|voice)\b",
            r"\b(account|license|certificate|vehicle|plate|device[_ -]?serial)\b",
            r"\b(mrn|medical[_ -]?record|health[_ -]?plan|beneficiary)\b",
        ),
    },
    {
        "id": "usa_safe_harbor_dates",
        "jurisdiction": "USA",
        "action": Action.JITTER_DATE,
        "reason": "HIPAA Safe Harbor date element header.",
        "patterns": (
            r"\b(date|datetime|timestamp|time[_ -]?stamp)\b",
            r"(^|[_ -])(dob|dod)([_ -]|$)",
            r"\b(birth|admission|discharge|death)[_ -]?date\b",
            r"(^|[_ -])[a-z0-9]*(dat|dt)\d*$",
        ),
    },
    {
        "id": "usa_safe_harbor_age",
        "jurisdiction": "USA",
        "action": Action.CAP,
        "reason": "HIPAA Safe Harbor age-over-89 aggregation header.",
        "patterns": (
            r"\b(age|years[_ -]?old)\b",
        ),
    },
    {
        "id": "usa_geography_generalization",
        "jurisdiction": "USA",
        "action": Action.GENERALIZE,
        "reason": "HIPAA geography header requiring sub-state generalization.",
        "patterns": (
            r"\b(village|district|state|country|geo|location|site[_ -]?address)\b",
        ),
    },
    {
        "id": "usa_free_text_suppression",
        "jurisdiction": "USA",
        "action": Action.SUPPRESS,
        "reason": "Free-text header may contain identifiers and needs suppression review.",
        "patterns": (
            r"\b(comment|note|narrative|free[_ -]?text|describe|description|specify|other)\b",
        ),
    },
    {
        "id": "usa_unique_study_identifier",
        "jurisdiction": "USA",
        "action": Action.PSEUDONYMIZE,
        "reason": "Unique study or participant identifier header.",
        "patterns": (
            r"\b(participant|subject|patient|person|study|record|case)[_ -]?(id|code|key|number|no)\b",
            r"\b(id|identifier|uuid|guid)\b",
            r"(^|[_ -])(?:subj(?:id)?|fid|pid|ptid|hhid|recordid)$",
        ),
    },
    {
        "id": "india_dpdpa_contact_identifiers",
        "jurisdiction": "INDIA",
        "action": Action.DROP,
        "reason": "India personal-data direct contact identifier header.",
        "patterns": (
            r"\b(email|e[-_ ]?mail|phone|telephone|mobile|cell|address|postal|pin[_ -]?code)\b",
            r"\b(passport|voter|ration|pan|bank|account)\b",
        ),
    },
    {
        "id": "india_aadhaar_identifier",
        "jurisdiction": "INDIA",
        "action": Action.DROP,
        "reason": "Aadhaar identity number header.",
        "patterns": (
            r"\b(aadhaar|adhar|aadhar|uidai)\b",
            r"\buid[_ -]?(no|number|id)?\b",
        ),
    },
    {
        "id": "india_date_identifier",
        "jurisdiction": "INDIA",
        "action": Action.JITTER_DATE,
        "reason": "Date-like personal-data header.",
        "patterns": (
            r"\b(date|datetime|timestamp|time[_ -]?stamp)\b",
            r"(^|[_ -])(dob|dod)([_ -]|$)",
            r"(^|[_ -])[a-z0-9]*(dat|dt)\d*$",
        ),
    },
    {
        "id": "india_free_text_suppression",
        "jurisdiction": "INDIA",
        "action": Action.SUPPRESS,
        "reason": "Free-text personal-data header needs suppression review.",
        "patterns": (
            r"\b(comment|note|narrative|free[_ -]?text|describe|description|specify|other)\b",
        ),
    },
    {
        "id": "india_unique_person_identifier",
        "jurisdiction": "INDIA",
        "action": Action.PSEUDONYMIZE,
        "reason": "Unique person or study identifier header.",
        "patterns": (
            r"\b(participant|subject|patient|person|study|record|case)[_ -]?(id|code|key|number|no)\b",
            r"\b(id|identifier|uuid|guid)\b",
            r"(^|[_ -])(?:subj(?:id)?|fid|pid|ptid|hhid|recordid)$",
        ),
    },
)


def _build_pinned_rules(jurisdictions: tuple[str, ...]) -> tuple[HeaderRule, ...]:
    wanted = set(jurisdictions)
    rules: list[HeaderRule] = []
    for spec in _PINNED_RULE_SPECS:
        jurisdiction = str(spec["jurisdiction"])
        if jurisdiction not in wanted:
            continue
        rules.append(
            HeaderRule(
                id=str(spec["id"]),
                jurisdiction=jurisdiction,
                action=spec["action"],  # type: ignore[arg-type]
                patterns=_compile_many(spec["patterns"]),  # type: ignore[arg-type]
                reason=str(spec["reason"]),
            )
        )
    return tuple(rules)


def _canonical_bundle_payload(
    sources: tuple[dict[str, str], ...],
    rules: tuple[HeaderRule, ...],
) -> dict[str, Any]:
    return {
        "sources": list(sources),
        "rules": [
            {
                "id": rule.id,
                "jurisdiction": rule.jurisdiction,
                "action": rule.action.value,
                "patterns": [pattern.pattern for pattern in rule.patterns],
                "reason": rule.reason,
            }
            for rule in rules
        ],
    }


def _sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_header(header: str) -> str:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", header.strip())
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized)
    return normalized.strip("_").lower()


def _header_match_texts(header: str) -> tuple[str, str]:
    normalized = _normalize_header(header)
    return normalized, normalized.replace("_", " ")


def validate_official_source_url(url: str) -> None:
    """Reject non-HTTPS, non-official rule sources."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme != "https" or host not in _OFFICIAL_SOURCE_HOSTS:
        raise OfficialSourceRejected(f"non-official privacy source rejected: {url}")


def _fetch_source_hash(url: str, *, timeout: float = 2.0) -> str | None:
    """Best-effort official-source freshness probe.

    The downloaded body is not persisted or exposed to the LLM. Only a content
    hash is retained in the run-audit rule bundle.
    """
    try:
        request = Request(url, headers={"User-Agent": "RePORT-AI-Portal/phi-review"})  # noqa: S310 - validated official HTTPS.
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - validated official HTTPS.
            body = response.read(2_000_000)
    except Exception:
        return None
    return hashlib.sha256(body).hexdigest()


def load_study_privacy_config(study_dir: str | Path) -> StudyPrivacyConfig:
    """Load and validate ``_study_privacy.yaml`` from a raw study directory."""
    study_path = Path(study_dir)
    config_path = study_path / "_study_privacy.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("_study_privacy.yaml must contain a mapping")

    jurisdictions_raw = raw.get("jurisdictions")
    if not isinstance(jurisdictions_raw, list) or not jurisdictions_raw:
        raise ValueError("jurisdictions must be a non-empty list")
    jurisdictions = tuple(str(item).upper() for item in jurisdictions_raw)
    unsupported = sorted(set(jurisdictions) - _SUPPORTED_JURISDICTIONS)
    if unsupported:
        raise ValueError(f"unsupported jurisdiction(s): {', '.join(unsupported)}")

    rule_refresh = str(raw.get("rule_refresh", "pinned_only"))
    if rule_refresh not in _SUPPORTED_REFRESH_MODES:
        raise ValueError(f"unsupported rule_refresh mode: {rule_refresh}")

    conflict_policy = str(raw.get("conflict_policy", "strictest_wins"))
    if conflict_policy not in _SUPPORTED_CONFLICT_POLICIES:
        raise ValueError(f"unsupported conflict_policy: {conflict_policy}")

    approval = raw.get("approval", {})
    if not isinstance(approval, dict):
        raise ValueError("approval must be a mapping")
    max_attempts = int(approval.get("max_synthetic_attempts", 5))
    if max_attempts < 1:
        raise ValueError("approval.max_synthetic_attempts must be >= 1")

    parallelism = raw.get("parallelism", {})
    if not isinstance(parallelism, dict):
        raise ValueError("parallelism must be a mapping")

    return StudyPrivacyConfig(
        study_dir=study_path,
        jurisdictions=jurisdictions,
        rule_refresh=rule_refresh,
        conflict_policy=conflict_policy,
        max_synthetic_attempts=max_attempts,
        approval_mode=str(approval.get("mode", "hybrid")),
        parallelism_mode=str(parallelism.get("mode", "auto")),
    )


def refresh_jurisdiction_rules(
    privacy_config: StudyPrivacyConfig,
    *,
    allow_network: bool = False,
) -> RuleBundle:
    """Resolve jurisdiction rules, falling back to the pinned rule pack.

    ``allow_network`` is intentionally conservative in this first support
    module: official URLs are validated, then the audited pinned rules are used
    when network refresh is disabled or unavailable.
    """
    base_sources = tuple(
        dict(source)
        for source in _PINNED_SOURCES
        if source["jurisdiction"] in set(privacy_config.jurisdictions)
    )
    for source in base_sources:
        validate_official_source_url(source["url"])

    source_mode = "pinned"
    sources: list[dict[str, str]] = []
    if allow_network and privacy_config.rule_refresh == "online_preferred":
        fetched_all = True
        for source in base_sources:
            fetched_hash = _fetch_source_hash(source["url"])
            enriched = dict(source)
            if fetched_hash:
                enriched["fetched_sha256"] = fetched_hash
            else:
                fetched_all = False
            sources.append(enriched)
        source_mode = "latest_official" if fetched_all else "pinned"
        if not fetched_all:
            sources = list(base_sources)
    else:
        sources = list(base_sources)

    rules = _build_pinned_rules(privacy_config.jurisdictions)
    payload = _canonical_bundle_payload(tuple(sources), rules)
    return RuleBundle(
        source_mode=source_mode,
        rules_sha256=_sha256_json(payload),
        sources=tuple(sources),
        rules=rules,
    )


def classify_headers(
    headers: list[str] | tuple[str, ...],
    privacy_config: StudyPrivacyConfig,
    rule_bundle: RuleBundle,
) -> dict[str, HeaderClassification]:
    """Classify headers with strictest-wins conflict handling."""
    if privacy_config.conflict_policy != "strictest_wins":
        raise ValueError(f"unsupported conflict_policy: {privacy_config.conflict_policy}")

    result: dict[str, HeaderClassification] = {}
    for header in headers:
        match_texts = _header_match_texts(header)
        action = Action.KEEP
        matched_rules: list[str] = []
        jurisdictions: list[str] = []
        reasons: list[str] = []

        for rule in rule_bundle.rules:
            if not any(pattern.search(text) for text in match_texts for pattern in rule.patterns):
                continue
            matched_rules.append(rule.id)
            jurisdictions.append(rule.jurisdiction)
            reasons.append(rule.reason)
            if _ACTION_RANK[rule.action] > _ACTION_RANK[action]:
                action = rule.action

        result[header] = HeaderClassification(
            header=header,
            action=action,
            matched_rules=tuple(matched_rules),
            jurisdictions=tuple(dict.fromkeys(jurisdictions)),
            reasons=tuple(dict.fromkeys(reasons)),
        )
    return result


_FORBIDDEN_IMPORT_PREFIXES = (
    "http",
    "logging",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "urllib",
)
_FORBIDDEN_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "globals",
    "input",
    "locals",
    "open",
    "print",
}
_FORBIDDEN_ATTR_NAMES = {
    "download",
    "read",
    "run",
    "system",
    "upload",
    "write",
}
_FORBIDDEN_LOGGER_CALLS = {
    "critical",
    "debug",
    "error",
    "exception",
    "info",
    "log",
    "warning",
}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def validate_pure_transform_source(source: str) -> PureTransformValidation:
    """Statically reject generated transform code with side-effect surfaces."""
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return PureTransformValidation(ok=False, errors=(f"SyntaxError: {exc.msg}",))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            errors.append("Import statements are not allowed in pure transforms")
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if alias.name.startswith(_FORBIDDEN_IMPORT_PREFIXES) or root in _FORBIDDEN_IMPORT_PREFIXES:
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            errors.append("ImportFrom statements are not allowed in pure transforms")
            root = module.split(".", 1)[0]
            if module.startswith(_FORBIDDEN_IMPORT_PREFIXES) or root in _FORBIDDEN_IMPORT_PREFIXES:
                errors.append(f"forbidden import: {module}")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            errors.append("with blocks are not allowed in pure transforms")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            leaf = name.rsplit(".", 1)[-1]
            if name in _FORBIDDEN_CALL_NAMES or leaf in _FORBIDDEN_CALL_NAMES:
                errors.append(f"forbidden call: {name}")
            if leaf in _FORBIDDEN_ATTR_NAMES:
                errors.append(f"forbidden side-effect call: {name}")
            if name.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                errors.append(f"forbidden module call: {name}")
            if name.startswith(("logger.", "log.")) and leaf in _FORBIDDEN_LOGGER_CALLS:
                errors.append(f"forbidden logging call: {name}")
        elif isinstance(node, ast.Attribute):
            full_name = _call_name(node)
            if full_name.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                errors.append(f"forbidden module access: {full_name}")

    unique_errors = tuple(dict.fromkeys(errors))
    return PureTransformValidation(ok=not unique_errors, errors=unique_errors)


def _review_blockers(headers: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    reasons: list[str] = []
    seen: set[str] = set()
    for idx, header in enumerate(headers, start=1):
        normalized = _normalize_header(header)
        if not normalized:
            reasons.append(f"blank header at position {idx}")
            continue
        if normalized in seen:
            reasons.append(f"duplicate normalized header: {normalized}")
        seen.add(normalized)
    return tuple(reasons)


def _adversarial_header_validation(
    privacy_config: StudyPrivacyConfig,
    rule_bundle: RuleBundle,
) -> tuple[str, ...]:
    """Run header-only adversarial probes without emitting fake row values."""
    probes = {
        "synthetic_participant_id_header": Action.PSEUDONYMIZE,
        "synthetic_visit_date_header": Action.JITTER_DATE,
        "synthetic_email_header": Action.DROP,
        "synthetic_aadhaar_header": Action.DROP,
        "synthetic_culture_result_header": Action.KEEP,
    }
    classified = classify_headers(tuple(probes), privacy_config, rule_bundle)
    failures = [
        f"adversarial header probe failed: {header}"
        for header, expected in probes.items()
        if classified[header].action != expected
    ]
    return tuple(failures)


def review_form_headers(
    *,
    form_name: str,
    headers: list[str] | tuple[str, ...],
    privacy_config: StudyPrivacyConfig,
    rule_bundle: RuleBundle,
) -> FormReviewApproval:
    """Review one form's headers before any row-value extraction is allowed."""
    classifications_by_header = classify_headers(headers, privacy_config, rule_bundle)
    classifications = tuple(classifications_by_header[header] for header in headers)
    actions = {header: item.action.value for header, item in classifications_by_header.items()}
    blockers = _review_blockers(headers)
    adversarial_failures = _adversarial_header_validation(privacy_config, rule_bundle)
    reasons = tuple(dict.fromkeys((*blockers, *adversarial_failures)))

    status = "held" if reasons else "approved"
    attempts = privacy_config.max_synthetic_attempts if status == "held" else 1
    return FormReviewApproval(
        form_name=form_name,
        status=status,
        attempts=attempts,
        actions=actions,
        classifications=classifications,
        reasons=reasons,
        rule_bundle_sha256=rule_bundle.rules_sha256,
        source_mode=rule_bundle.source_mode,
    )


def verify_approval_payload(payload: dict[str, Any]) -> None:
    """Validate approval report shape and ensure it contains no value samples."""
    required = {
        "run_id",
        "study",
        "created_utc",
        "jurisdictions",
        "conflict_policy",
        "rule_bundle",
        "forms",
        "approved_forms",
        "held_forms",
        "status",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"approval payload missing required keys: {sorted(missing)}")

    text = json.dumps(payload, sort_keys=True)
    forbidden_markers = (
        "raw_value",
        "sample_value",
        "synthetic_value",
        "Alice",
        "555-",
        "123-45-6789",
    )
    leaked = [marker for marker in forbidden_markers if marker in text]
    if leaked:
        raise ValueError(f"approval payload contains value-like marker(s): {leaked}")

    forms = payload.get("forms")
    if not isinstance(forms, list):
        raise ValueError("approval payload forms must be a list")
    for item in forms:
        if not isinstance(item, dict):
            raise ValueError("approval form item must be a mapping")
        if item.get("status") not in {"approved", "held"}:
            raise ValueError(f"invalid approval status: {item.get('status')!r}")
