"""Published llm_source bundle readiness checks for the chat UI."""

from __future__ import annotations

import json
from pathlib import Path

import config

# Terminal-run gate (mirrors scripts.skills.extract_to_llm_source.EXIT_OK /
# EXIT_PARTIAL_REVIEW). Kept as local literals so this UI-convenience module does
# not import the heavy publishing CLI just to read two exit codes.
_EXIT_OK = 0
_EXIT_PARTIAL_REVIEW = 8
_TERMINAL_EXIT_CODES = frozenset({_EXIT_OK, _EXIT_PARTIAL_REVIEW})
_TERMINAL_PUBLISH_STATUSES = frozenset({"complete", "partial"})


def _has_dataset_jsonl() -> bool:
    return config.TRIO_DATASETS_DIR.is_dir() and any(config.TRIO_DATASETS_DIR.glob("*.jsonl"))


def _dictionary_source_expected() -> bool:
    source_dir = Path(getattr(config, "DATA_DICTIONARY_DIR", ""))
    try:
        return source_dir.is_dir() and any(
            path.is_file() and not path.name.startswith(".") for path in source_dir.rglob("*")
        )
    except OSError:
        return False


def _has_dictionary_mapping_jsonl() -> bool:
    mapping_dir = Path(
        getattr(
            config,
            "DICTIONARY_JSON_OUTPUT_DIR",
            config.STUDY_LLM_SOURCE_DIR / "dictionary_mapping" / "jsonl",
        )
    )
    return mapping_dir.is_dir() and any(mapping_dir.rglob("*.jsonl"))


def _has_policy_sot() -> bool:
    sot_dir = getattr(config, "LLM_SOURCE_SOT_DIR", config.STUDY_LLM_SOURCE_DIR / "SoT")
    sot_path = Path(sot_dir)
    if sot_path.is_dir() and any(sot_path.glob("*/pdf/*_policy.yaml")):
        return True

    legacy_dir = getattr(
        config,
        "LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR",
        config.STUDY_LLM_SOURCE_DIR / "source_truth",
    )
    legacy_path = Path(legacy_dir)
    return legacy_path.is_dir() and (
        any(legacy_path.glob("*_policy.yaml")) or any(legacy_path.glob("*_policy.lean.yaml"))
    )


def bundle_readiness_issues() -> list[str]:
    """Return human-readable reasons the published bundle is not ready."""

    issues: list[str] = []
    if not config.STUDY_LLM_SOURCE_DIR.exists():
        issues.append(f"missing llm_source directory: {config.STUDY_LLM_SOURCE_DIR}")
    if not _has_dataset_jsonl():
        issues.append(f"missing scrubbed dataset JSONL under {config.TRIO_DATASETS_DIR}")
    if not _has_policy_sot():
        issues.append("missing Source Truth policy output under llm_source/SoT/<pair>/pdf/")
    if _dictionary_source_expected() and not _has_dictionary_mapping_jsonl():
        issues.append(f"missing dictionary mapping JSONL under {config.DICTIONARY_JSON_OUTPUT_DIR}")
    return issues


def _latest_run_status(study: str) -> tuple[Path, dict] | None:
    """Return ``(run_dir, parsed_status)`` for the latest TERMINAL run, or None.

    "Latest" is the chronologically most recent run by ``completed_utc`` (an
    ISO-8601 timestamp from ``status.json``) — NOT by run-directory name. Run
    ids embed a uuid4 hex (random), so name order is not chronological; sorting
    by name would surface an arbitrary run as "latest". This mirrors
    :func:`scripts.skills.extract_to_llm_source._resolve_run_id`.

    Only TERMINAL runs are considered: ``exit_code`` in {0, 8} (EXIT_OK,
    EXIT_PARTIAL_REVIEW) AND ``publish_status`` in {"complete", "partial"}. A
    non-terminal run (e.g. paused needs-advice, exit 6) is ignored so the notice
    reflects an actual published outcome, not an in-flight/aborted run.

    This is ADVISORY metadata for the Load Study UI: it never raises. Any
    missing/malformed path or unreadable JSON yields ``None`` so the caller can
    degrade to "no notice" rather than surfacing an error to the operator.
    """
    try:
        runs_dir = Path(config.OUTPUT_DIR) / study / "runs"
        if not runs_dir.is_dir():
            return None
        # (completed_utc, run_dir, status) for each terminal run, picked by ts.
        terminal: list[tuple[str, Path, dict]] = []
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            status_path = run_dir / "status.json"
            if not status_path.is_file():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            if not isinstance(status, dict):
                continue
            if status.get("exit_code") not in _TERMINAL_EXIT_CODES:
                continue
            if status.get("publish_status") not in _TERMINAL_PUBLISH_STATUSES:
                continue
            completed = status.get("completed_utc") or status.get("started_utc") or ""
            terminal.append((str(completed), run_dir, status))
        if not terminal:
            return None
        # Chronologically latest by completed_utc; run_dir name breaks ties.
        latest = max(terminal, key=lambda item: (item[0], item[1].name))
        return latest[1], latest[2]
    except OSError:
        return None


def _held_reasons_for_forms(run_dir: Path, held_forms: list[str]) -> dict[str, list[str]]:
    """Best-effort per-form held reasons from the run's approval report.

    Reads ``phi_handling_approval.json`` (column NAMES / reason strings only —
    never row values) and returns ``{form_name: [reason, ...]}`` for each held
    form that has reasons recorded. Never raises; returns ``{}`` on any error.
    """
    reasons: dict[str, list[str]] = {}
    try:
        approval_path = run_dir / "phi_handling_approval.json"
        if not approval_path.is_file():
            return {}
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        if not isinstance(approval, dict):
            return {}
        held = set(held_forms)
        for form in approval.get("forms", []):
            if not isinstance(form, dict):
                continue
            name = str(form.get("form_name", ""))
            if name not in held:
                continue
            form_reasons = [str(r) for r in form.get("reasons", []) if str(r)]
            if form_reasons:
                reasons[name] = form_reasons
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return {}
    return reasons


def held_set_notice(study: str | None = None) -> str | None:
    """Return an INFORMATIONAL held-set notice for the latest run, or None.

    ADVISORY ONLY — distinct from :func:`bundle_readiness_issues`, which is a
    hard publish-readiness block. When the most recent run held one or more
    forms for human PHI review, this returns a human-readable string naming the
    held forms (and their reasons when recorded) so the Load Study UI can show a
    NON-BLOCKING ``st.warning``: the operator may keep querying the already-
    published approved sets.

    The held form NAMES and reason strings only — no row values — are surfaced.

    Fail-closed-to-silent: a missing/malformed ``status.json`` (or any I/O or
    parse error) yields ``None`` rather than raising. This is a UI convenience,
    never a correctness gate; it must never crash the chat surface.
    """
    if study is None:
        study = getattr(config, "STUDY_NAME", "") or ""
    if not study:
        return None

    resolved = _latest_run_status(study)
    if resolved is None:
        return None
    run_dir, status = resolved

    held_forms = [str(f) for f in status.get("held_forms", []) if str(f)]
    if not held_forms:
        return None

    reasons = _held_reasons_for_forms(run_dir, held_forms)

    lines = [
        f"{len(held_forms)} form(s) were held for human PHI review and are not "
        "published. You can continue querying the approved study data.",
    ]
    for form in held_forms:
        form_reasons = reasons.get(form)
        if form_reasons:
            lines.append(f"- {form}: {'; '.join(form_reasons)}")
        else:
            lines.append(f"- {form}")
    return "\n".join(lines)


def published_bundle_exists() -> bool:
    """Return True when the assistant has the minimum published bundle.

    The active bundle shape requires scrubbed dataset JSONL plus Source Truth
    policy output. Dictionary mappings are required when a raw dictionary
    source is present, preserving the host pipeline's previous dictionary leg.
    """

    try:
        return not bundle_readiness_issues()
    except Exception:
        return False
