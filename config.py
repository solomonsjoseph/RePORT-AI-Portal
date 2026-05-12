"""Central runtime configuration for RePORT AI Portal.

**What.** All path constants, environment-variable resolution, study
detection, LLM provider inference, staging-directory management,
and directory creation in one place.

**Why.** 138 call sites across the pipeline, agent, UI, and test suite
use ``import config`` — a single canonical location prevents scattered
``os.getenv`` and ``Path(...)`` literals throughout the codebase.

**How.** All values are resolved at import time. ``STUDY_NAME`` is
determined by the ``$STUDY_NAME`` env var or a filesystem scan of
``data/raw/``. LLM provider is inferred from model-name prefix unless
overridden by ``$LLM_PROVIDER``. Staging directories are NOT created
eagerly; call :func:`ensure_directories` after startup.
"""

# config.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, overload

import yaml

# ----------------------------------------------------------------------------
# ENV HELPERS (centralized, validated access)
# ----------------------------------------------------------------------------


@overload
def _get_env(key: str, default: str) -> str: ...
@overload
def _get_env(key: str, default: None = None) -> str | None: ...
def _get_env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _get_env_int(key: str, default: int) -> int:
    raw = _get_env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _get_env_bool(key: str, default: bool) -> bool:
    value = str(_get_env(key, str(default))).lower()
    return value in {"1", "true", "yes", "on"}


def production_mode_enabled() -> bool:
    """Return True when production controls should fail closed."""

    return (
        _get_env_bool("REPORT_AI_PRODUCTION", False)
        or _get_env_bool("REPORT_AI_REQUIRE_PHI_LOG_REDACTOR", False)
        or str(_get_env("REPORT_AI_AUTH_MODE", "")).strip().lower() == "proxy"
    )


def strict_study_detection_enabled() -> bool:
    """Return True when missing auto-detected study data should abort import."""

    return _get_env_bool("REPORT_AI_STRICT_STUDY_DETECTION", False)


# ----------------------------------------------------------------------------
# YAML CONFIG (config/config.yaml — optional overlay)
# ----------------------------------------------------------------------------

CONFIG_YAML_PATH = Path(__file__).resolve().parent / "config" / "config.yaml"


def _load_yaml_config() -> dict[str, Any]:
    """Load config.yaml if it exists; return empty dict otherwise."""
    if CONFIG_YAML_PATH.is_file():
        with CONFIG_YAML_PATH.open() as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    return {}


_YAML_CFG: dict[str, Any] = _load_yaml_config()


def yaml_get(*keys: str, default: Any = None) -> Any:
    """Retrieve a nested key from the loaded YAML config.

    >>> yaml_get("app", "log_level", default="INFO")
    'INFO'
    """
    node: Any = _YAML_CFG
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return default
    return node if node is not None else default


# ----------------------------------------------------------------------------
# VERSION
# ----------------------------------------------------------------------------

try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.0.0"

DEFAULT_DATASET_NAME = "Indo-VAP"
DEFAULT_LOG_LEVEL = "INFO"

LOG_NAME = "report_ai_portal"
LOG_LEVEL = _get_env("LOG_LEVEL", yaml_get("app", "log_level", default=DEFAULT_LOG_LEVEL))
logger = logging.getLogger(LOG_NAME)

AGENT_MODEL_ID: str = os.environ.get("REPORTAL_AGENT_MODEL", "claude-opus-4-7")


# ----------------------------------------------------------------------------
# BASE PATHS
# ----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"

OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / ".logs"
TMP_DIR = BASE_DIR / "tmp"


# ----------------------------------------------------------------------------
# STUDY DETECTION
# ----------------------------------------------------------------------------


def detect_study_name(*, strict: bool | None = None) -> str:
    strict = strict_study_detection_enabled() if strict is None else strict
    if not RAW_DATA_DIR.exists():
        msg = f"RAW_DATA_DIR missing: {RAW_DATA_DIR}"
        if strict:
            raise RuntimeError(msg)
        logger.warning("%s → using default: %s", msg, DEFAULT_DATASET_NAME)
        return DEFAULT_DATASET_NAME

    try:
        exclude = {".backup", ".DS_Store", "output"}

        candidates = [
            p.name
            for p in RAW_DATA_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in exclude
        ]

        for candidate in sorted(candidates):
            if (RAW_DATA_DIR / candidate / "datasets").is_dir():
                return candidate

        msg = f"No valid study found under {RAW_DATA_DIR}"
        if strict:
            raise RuntimeError(msg)
        logger.warning("%s → using default: %s", msg, DEFAULT_DATASET_NAME)
        return DEFAULT_DATASET_NAME

    except OSError as exc:
        if strict:
            raise RuntimeError(f"Study detection failed under {RAW_DATA_DIR}") from exc
        logger.warning("Study detection failed → fallback to default", exc_info=True)
        return DEFAULT_DATASET_NAME


# ENV override ALWAYS wins
_STUDY_NAME_ENV = _get_env("STUDY_NAME")
if _STUDY_NAME_ENV:
    if "/" in _STUDY_NAME_ENV or "\\" in _STUDY_NAME_ENV or _STUDY_NAME_ENV in {".", ".."}:
        raise ValueError("STUDY_NAME must be a plain folder name, not a path")
    STUDY_NAME = _STUDY_NAME_ENV
else:
    STUDY_NAME = detect_study_name()


# ----------------------------------------------------------------------------
# STUDY PATHS
# ----------------------------------------------------------------------------

STUDY_DATA_DIR = RAW_DATA_DIR / STUDY_NAME
STUDY_OUTPUT_DIR = OUTPUT_DIR / STUDY_NAME

# Raw study subdirectories (under data/raw/<study>/)
DATASETS_DIR = STUDY_DATA_DIR / "datasets"
ANNOTATED_PDFS_DIR = STUDY_DATA_DIR / "annotated_pdfs"
DATA_DICTIONARY_DIR = STUDY_DATA_DIR / "data_dictionary"

# Trio bundle is the single consolidated clean-output tree.
# Everything that was formerly split across clean/jsonl/* now lives here.
TRIO_BUNDLE_DIR = STUDY_OUTPUT_DIR / "trio_bundle"

# LLM-visible source directory — canonical home for artefacts the agent reads.
STUDY_LLM_SOURCE_DIR = STUDY_OUTPUT_DIR / "llm_source"

TRIO_DATASETS_DIR = STUDY_LLM_SOURCE_DIR / "dataset_schema" / "files"

# Note: the concept index path
# (``STUDY_LLM_SOURCE_DIR / "concept" / "concept_index.json"``) is owned
# by ``scripts.source_truth.build``, which constructs it from the
# caller-supplied ``output_root``. We deliberately do NOT export a
# config-level constant here so the build coordinator stays the single
# source of truth for that artifact's path.

STUDY_AUDIT_DIR = STUDY_OUTPUT_DIR / "audit"

# Audit-report paths (written by the cleanup/dedup pipeline).
# Only the dataset leg produces audit reports — dictionary and PDF legs carry
# no PHI, so their cleanup is side-effect-only (pruning without a report).
# Step-cache manifests for dataset_processing also land under STUDY_AUDIT_DIR
# so the LLM-visible llm_source/ tree stays content-only.
AUDIT_DATASET_REPORT_PATH: Path = STUDY_AUDIT_DIR / "dataset_cleanup_report.json"
AUDIT_SCRUB_REPORT_PATH: Path = STUDY_AUDIT_DIR / "phi_scrub_report.json"

DICTIONARY_JSON_OUTPUT_DIR = STUDY_LLM_SOURCE_DIR / "dictionary_mapping" / "jsonl"

# ----------------------------------------------------------------------------
# PHASE 0 — SoT GAP CONSTANTS
# ----------------------------------------------------------------------------
# Source-of-Truth directory lives under data/SoT/<study>/ (not under raw/).
SOT_DIR: Path = DATA_DIR / "SoT" / STUDY_NAME
# Raw form PDFs live at the root of RAW_DATA_DIR / STUDY_NAME.
RAW_PDF_DIR: Path = RAW_DATA_DIR / STUDY_NAME
# Pilot extraction results land under tmp/results/.
PILOT_RESULTS_DIR: Path = TMP_DIR / "results"
# Working drafts for SoT-gap analysis live under tmp/sot_gap_drafts/.
SOT_GAP_DRAFTS_DIR: Path = TMP_DIR / "sot_gap_drafts"
# Coverage and report artefacts written at the end of a gap run.
SOT_GAP_COVERAGE_PATH: Path = TMP_DIR / "sot_gap_coverage.json"
SOT_GAP_REPORT_PATH: Path = TMP_DIR / "sot_gap_report.md"
# Evidence packs are subdirectories of the gap-drafts workspace.
SOT_EVIDENCE_PACK_DRAFTS_DIR: Path = SOT_GAP_DRAFTS_DIR / "evidence_packs"

# --- Phase 1: PHI rule audit and expand --------------------------------------
PHI_TECHNIQUES_INVENTORY_PATH: Path = (
    BASE_DIR / "docs" / "superpowers" / "specs" / "2026-05-08-phi-techniques-inventory.md"
)
PHI_COVERAGE_MATRIX_PATH: Path = (
    BASE_DIR / "docs" / "superpowers" / "specs" / "2026-05-08-phi-coverage-matrix.md"
)
PHI_SWEEP_FINDINGS_PATH: Path = TMP_DIR / "phi_sweep_findings.json"
PHI_SWEEP_HITL_DRAFTS_DIR: Path = TMP_DIR / "phi_sweep_hitl_drafts"
PHI_SWEEP_PR_DRAFTS_DIR: Path = TMP_DIR / "phi_sweep_pr_drafts"

# --- Phase 2: llm_source restructure -----------------------------------------
LLM_SOURCE_DATASET_SCHEMA_FILES_DIR: Path = STUDY_LLM_SOURCE_DIR / "dataset_schema" / "files"
LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH: Path = (
    STUDY_LLM_SOURCE_DIR / "dataset_schema" / "catalog.json"
)
LLM_SOURCE_DICTIONARY_MAPPING_DIR: Path = STUDY_LLM_SOURCE_DIR / "dictionary_mapping"
LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR: Path = LLM_SOURCE_DICTIONARY_MAPPING_DIR / "jsonl"
LLM_SOURCE_DICTIONARY_CATALOG_PATH: Path = LLM_SOURCE_DICTIONARY_MAPPING_DIR / "catalog.json"
LLM_SOURCE_STUDY_METADATA_DIR: Path = STUDY_LLM_SOURCE_DIR / "study_metadata"
LLM_SOURCE_STUDY_METADATA_CATALOG_PATH: Path = LLM_SOURCE_STUDY_METADATA_DIR / "catalog.json"
LLM_SOURCE_EVIDENCE_PACKS_DIR: Path = LLM_SOURCE_STUDY_METADATA_DIR / "evidence_packs"
LLM_SOURCE_CONCEPT_DIR: Path = STUDY_LLM_SOURCE_DIR / "concept"

# Lean-catalog size thresholds (bytes). CI fails if a catalog exceeds.
LEAN_CATALOG_DICTIONARY_MAX_BYTES: int = 20 * 1024
LEAN_CATALOG_DATASET_SCHEMA_MAX_BYTES: int = 50 * 1024
LEAN_CATALOG_STUDY_METADATA_MAX_BYTES: int = 200 * 1024

# --- Phase 3: cross-verify ---------------------------------------------------
# STUDY_AUDIT_DIR is defined above in the study-paths block; reuse it here.
PHI_ID_MAPPING_PATH: Path = STUDY_AUDIT_DIR / "phi_id_mapping.json"
CROSS_VERIFY_REPEAT_LEDGER_PATH: Path = STUDY_AUDIT_DIR / "cross_verify_repeat_ledger.json"
CROSS_VERIFY_SAFE_REPORT_PATH: Path = TMP_DIR / "cross_verify_safe_report.json"
CROSS_VERIFY_AGENT_WORKDIR: Path = TMP_DIR / "cross_verify_agent_workdir"
CROSS_VERIFY_PR_DRAFTS_DIR: Path = TMP_DIR / "cross_verify_pr_drafts"
CROSS_VERIFY_HITL_DRAFTS_DIR: Path = TMP_DIR / "cross_verify_hitl_drafts"
CROSS_VERIFY_REPEAT_THRESHOLD: int = 2

# --- Phase 4: audit-zone hardening -------------------------------------------
AUDIT_NO_LLM_SENTINEL_NAME: str = ".NO_LLM_ZONE"
AUDIT_SENTINEL_ALARM_PATH: Path = TMP_DIR / "audit_sentinel_alarms.jsonl"
AUDIT_NO_LLM_ZONE_ATTRIBUTE: str = "report-ai-portal-no-llm"

# ----------------------------------------------------------------------------
# AGENT STATE TIER (per-session state, NOT study output)
# ----------------------------------------------------------------------------
# Per-session, agent-owned operational state — analysis runs, conversation
# transcripts. Telemetry lives under STUDY_AUDIT_DIR so the LLM's permitted
# agent/** zone stays free of operator-audit bytes. Everything inside the
# fully-gitignored ``output/`` tree keeps PHI-scrubbed cohort bytes out of
# git by default.
AGENT_STATE_DIR: Path = STUDY_OUTPUT_DIR / "agent"
AGENT_OUTPUT_DIR: Path = AGENT_STATE_DIR / "analysis"
CONVERSATIONS_DIR: Path = AGENT_STATE_DIR / "conversations"

# ----------------------------------------------------------------------------
# SNAPSHOT TIER (legacy path — LLM-INVISIBLE security boundary)
# ----------------------------------------------------------------------------
# The snapshot/restore subsystem itself has been retired (SoT-based extraction
# now produces a reviewable ``llm_source/`` tree directly). This constant is
# preserved as a security-zone marker: ``data/snapshots/`` is intentionally
# OUTSIDE the LLM agent's read zone (which is ``llm_source/`` + ``agent/``),
# and ``cutover_gate`` plus the agent file-access tests still assert that any
# path under this directory is hard-rejected by ``validate_agent_read``.
STUDY_SNAPSHOTS_DIR: Path = DATA_DIR / "snapshots" / STUDY_NAME

# Staging workspace — per-study tree inside TMP_DIR. Managed per-run by
# main.py's _prepare_staging() / _publish_staging(); NOT created eagerly by
# ensure_directories() so a stale workspace from a crashed previous run is
# always purged explicitly before reuse.
STUDY_STAGING_DIR: Path = TMP_DIR / STUDY_NAME
STAGING_DATASETS_DIR: Path = STUDY_STAGING_DIR / "datasets"
STAGING_DICTIONARY_DIR: Path = STUDY_STAGING_DIR / "dictionary"

# ----------------------------------------------------------------------------
# PHI SCRUB
# ----------------------------------------------------------------------------
# Narrow PHI handling: per-subject deterministic date jitter (SANT method) +
# HMAC-SHA256 ID pseudonymization. See scripts/security/phi_scrub.py.
#
# Config file lives alongside the module so study-specific regex patterns can
# be edited without touching code.
PHI_SCRUB_CONFIG_PATH: Path = BASE_DIR / "scripts" / "security" / "phi_scrub.yaml"


def _phi_key_path() -> Path:
    """Resolve the sidecar PHI HMAC key path.

    Uses ``$XDG_CONFIG_HOME/report_ai_portal/phi_key`` when the env var is set,
    otherwise falls back to ``~/.config/report_ai_portal/phi_key``. The key lives
    OUTSIDE the repo tree and is never read by the agent or committed to git.
    """
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "report_ai_portal" / "phi_key"


PHI_KEY_PATH: Path = _phi_key_path()


# ----------------------------------------------------------------------------
# EXTRACTION CONFIG (centralized — used by all extraction modules)
# ----------------------------------------------------------------------------

# Temporary-file prefixes for atomic writes.  Each module uses its own prefix
# so crash-leftover temp files can be attributed to their source.
TEMP_PREFIX_DATASET: str = "report_ai_portal_dataset_"
TEMP_PREFIX_DICT: str = "report_ai_portal_dict_"
TEMP_PREFIX_DEDUP: str = "report_ai_portal_dedup_"

# Secure temp workspace — the prefix is intentionally generic+randomised so
# the directory name leaks no information about what pipeline stage created it.
SECURE_TEMP_PREFIX: str = "rpln_"

# Duplicate-column detection regex for dataset extraction
DUPLICATE_COLUMN_PATTERN: str = r"^(.+?)_?(\d+)$"


# ----------------------------------------------------------------------------
# LLM PROVIDER INFERENCE
# ----------------------------------------------------------------------------


def _infer_provider(model_name: str) -> str:
    """Infer LangChain provider string from model name prefix.

    Recognised patterns:
        llama*, mistral*, phi*, gemma*, qwen* (incl. qwen3:8b), deepseek*,
        codellama*, tinyllama*, vicuna*, falcon*, orca*  → "ollama"
        claude*                               → "anthropic"
        gpt-*, o1*, o3*, o4*, text-davinci*   → "openai"
        gemini*                               → "google-genai"

    Falls back to ``"ollama"`` (local inference, no API key needed).
    """
    m = model_name.lower()
    _ollama_prefixes = (
        "llama",
        "mistral",
        "phi3",
        "phi-3",
        "gemma",
        "qwen",
        "deepseek",
        "codellama",
        "tinyllama",
        "vicuna",
        "falcon",
        "orca",
    )
    if m.startswith(_ollama_prefixes):
        return "ollama"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "o4", "text-davinci")):
        return "openai"
    if m.startswith("gemini"):
        return "google-genai"
    # NVIDIA-hosted models use "org/model" slug format
    _nvidia_orgs = ("moonshotai/", "nvidia/", "mistralai/", "deepseek-ai/", "qwen/", "meta/")
    if any(m.startswith(prefix) for prefix in _nvidia_orgs):
        return "nvidia-ai-endpoints"
    return "ollama"  # safe default — local inference, no key needed


LLM_MODEL = _get_env("LLM_MODEL", yaml_get("ai_assistant", "llm_model", default="qwen3:8b"))
# LLM_PROVIDER: explicit env var wins; otherwise infer from model name.
LLM_PROVIDER: str = _get_env("LLM_PROVIDER") or _infer_provider(LLM_MODEL)

# Qwen3 downgrade ladder — descending parameter count. When Ollama refuses
# a rung with "requires more system memory", _init_llm walks this list to
# find the largest rung that actually loads. Only applies to qwen3:* models;
# other models (Claude, GPT, custom Ollama) pass through unchanged.
QWEN3_DOWNGRADE_LADDER: tuple[str, ...] = ("qwen3:8b", "qwen3:4b", "qwen3:1.7b")


def preferred_or_installed_downgrade(model: str) -> list[str]:
    """Return the sequence of model names to try starting at ``model``.

    For qwen3 rungs in :data:`QWEN3_DOWNGRADE_LADDER`, returns the ladder
    from the given rung downward. For any other model, returns a one-element
    list — we only auto-step qwen3 because the three rungs are behaviourally
    compatible (same family, same tool-use format, same thinking convention).
    """
    if model in QWEN3_DOWNGRADE_LADDER:
        start = QWEN3_DOWNGRADE_LADDER.index(model)
        return list(QWEN3_DOWNGRADE_LADDER[start:])
    return [model]


# ----------------------------------------------------------------------------
# AI Assistant / AGENT
# ----------------------------------------------------------------------------

# Telemetry lives under STUDY_AUDIT_DIR (not AGENT_STATE_DIR) to keep the
# LLM's permitted agent/** zone clear of operator-audit bytes. Per the PHI
# rule, LLM must never read telemetry; parking it under audit/ — the same
# zone that holds phi_scrub_report.json and dataset_cleanup_report.json —
# makes that boundary structural, not a per-file carve-out.
TELEMETRY_DIR = STUDY_AUDIT_DIR / "telemetry"
TELEMETRY_SINK = TELEMETRY_DIR / "events.jsonl"

# Chat / agent
AGENT_MAX_TOKENS: int = _get_env_int("AGENT_MAX_TOKENS", 16384)
AGENT_TIMEOUT: int = _get_env_int("AGENT_TIMEOUT", 300)
CHAT_RATE_LIMIT_WINDOW_SECONDS: int = _get_env_int("CHAT_RATE_LIMIT_WINDOW_SECONDS", 60)
CHAT_RATE_LIMIT_MAX_TURNS: int = _get_env_int("CHAT_RATE_LIMIT_MAX_TURNS", 12)
# Watchdog on the agent stream: raise TimeoutError if no chunk is produced
# for this many seconds. Measures inter-chunk idle time, NOT total wall
# clock — so slow-but-steady streams (long tool runs) stay alive. The E3
# benchmark stall went 6+ minutes of total silence with no stop signal;
# 180s is ~10x the p99 of a healthy routing step.
AGENT_STREAM_IDLE_TIMEOUT: int = _get_env_int("AGENT_STREAM_IDLE_TIMEOUT", 180)

# Analytical engine limits
ANALYSIS_TIMEOUT: int = _get_env_int("ANALYSIS_TIMEOUT", 300)
ANALYSIS_MAX_OUTPUT: int = _get_env_int("ANALYSIS_MAX_OUTPUT", 200_000)
ANALYSIS_MAX_FIGURES: int = _get_env_int("ANALYSIS_MAX_FIGURES", 20)

# Sandbox subprocess limits — operational tunables (safe to lower; lowering
# only tightens the security envelope). The trust boundary itself
# (import allowlist, env-var blocklist, AST guards) is hardcoded in
# ``scripts.ai_assistant.sandbox`` and is not configurable from here.
#
# Defaults sized for production runs of the typical pandas+numpy+plotly
# stack: numpy alone reserves ~700 MB of address space on Linux when loaded
# (RLIMIT_AS is whole-vmap, not RSS). RLIMIT_NPROC is per-user not per-tree
# on Linux, so a small cap conflicts with whatever else the host user is
# running — 4096 is high enough to coexist with shared CI environments
# while still preventing runaway fork bombs.
SANDBOX_MAX_MEMORY_MB: int = _get_env_int("SANDBOX_MAX_MEMORY_MB", 2048)
SANDBOX_MAX_PROCS: int = _get_env_int("SANDBOX_MAX_PROCS", 4096)
SANDBOX_MAX_FILES: int = _get_env_int("SANDBOX_MAX_FILES", 256)
SANDBOX_PERSIST_CODE: bool = _get_env("SANDBOX_PERSIST_CODE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Orchestration mode: "auto" | "single-agent" | "multi-agent"
AGENT_ORCHESTRATION_MODE: str = _get_env(
    "AGENT_ORCHESTRATION_MODE",
    yaml_get("ai_assistant", "agent", "orchestration_mode", default="auto"),
)

# Enforce LangChain tracing OFF by default (privacy-first)
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


# ----------------------------------------------------------------------------
# DIRECTORY CREATION
# ----------------------------------------------------------------------------


def ensure_directories() -> None:
    """Create runtime directories. Sensitive dirs (containing PHI-scrubbed
    data, agent state, conversations, audit, or logs) are
    hardened to mode 0o700 after creation so they're not world-readable
    under the typical umask 0o022. Dirs that may legitimately need group
    access (``OUTPUT_DIR`` parent, ``TMP_DIR`` is already 0o700 via
    secure-staging) are left at default mode."""
    sensitive_paths = [
        STUDY_OUTPUT_DIR,
        LOGS_DIR,
        TRIO_BUNDLE_DIR,
        TRIO_DATASETS_DIR,
        DICTIONARY_JSON_OUTPUT_DIR,
        STUDY_AUDIT_DIR,
        AGENT_STATE_DIR,
        AGENT_OUTPUT_DIR,
        CONVERSATIONS_DIR,
        TELEMETRY_DIR,
        # NOTE: ``STUDY_SNAPSHOTS_DIR`` is intentionally NOT created here.
        # It is a human-reviewed baseline under ``data/snapshots/{STUDY}/``.
        # Auto-creating it would hide the absence of a reviewed fallback.
    ]
    for path in [OUTPUT_DIR, TMP_DIR, *sensitive_paths]:
        path.mkdir(parents=True, exist_ok=True)
    import contextlib

    for path in sensitive_paths:
        # Best-effort: a chmod failure (e.g., not the file owner) is not a
        # fatal startup error.
        with contextlib.suppress(OSError):
            path.chmod(0o700)


# ----------------------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------------------


def validate_config() -> None:
    # --- PATH VALIDATION ---
    required_paths = [
        RAW_DATA_DIR,
        STUDY_DATA_DIR,
        DATASETS_DIR,
        DATA_DICTIONARY_DIR,
    ]

    for path in required_paths:
        if not path.exists():
            raise FileNotFoundError(f"Missing required path: {path}")

    # PDF source is optional — the pipeline handles its absence gracefully
    if not ANNOTATED_PDFS_DIR.exists():
        logger.warning(
            "Annotated PDFs directory not found: %s — PDF extraction will be skipped",
            ANNOTATED_PDFS_DIR,
        )

    # Ensure the dictionary directory contains at least one file
    if DATA_DICTIONARY_DIR.is_dir() and not any(DATA_DICTIONARY_DIR.iterdir()):
        raise FileNotFoundError(f"Dictionary directory is empty: {DATA_DICTIONARY_DIR}")

    # --- LOG FINAL STATE ---
    logger.info(
        "Config loaded | study=%s",
        STUDY_NAME,
    )
