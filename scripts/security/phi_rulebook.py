"""PHI Rulebook engine — versioned offline cache over the jurisdiction rules (C2).

The jurisdiction classification rules live in
:mod:`scripts.security.phi_review` (``refresh_jurisdiction_rules`` resolves a
:class:`~scripts.security.phi_review.RuleBundle` from the pinned, official-source
rule pack, optionally probing the live official sources for a freshness hash).
This module wraps that primitive with three operational guarantees the raw
function does not provide:

* **Versioned offline cache.** Each resolved bundle's provenance (rules SHA-256,
  source mode, official sources, rule summaries) is persisted to a versioned JSON
  cache keyed by the sorted jurisdiction set, so repeated runs/processes share a
  durable record without re-probing the network.
* **Committed seed for airgapped first run.** A v1 seed cache is committed under
  ``config/_defaults/phi_rulebook/`` so the very first run in an environment with
  no network and no prior cache still has a known-good baseline to compare
  against. (The classification *rules themselves* are pinned in code, so the
  engine always functions offline — the seed/cache add provenance + drift
  detection, not the rules.)
* **Drift detection.** The freshly-built bundle's ``rules_sha256`` is compared to
  the cached/seed baseline; a mismatch is surfaced (``drift_detected``) so an
  operator/IRB reviewer is alerted when the effective rule set changed since the
  last recorded run — whether from a code change to the pinned rules or a live
  source update.

Value-free: the cache holds rule *metadata* (ids, jurisdictions, actions,
reasons, source URLs, SHA-256s) — never any study data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import config
from scripts.security.phi_review import (
    RuleBundle,
    StudyPrivacyConfig,
    refresh_jurisdiction_rules,
)
from scripts.utils.logging_system import get_logger

__all__ = [
    "RULEBOOK_CACHE_VERSION",
    "RulebookResolution",
    "cache_filename",
    "default_cache_dir",
    "default_seed_dir",
    "read_cache_entry",
    "resolve_rulebook",
    "write_cache_entry",
]

_logger = get_logger(__name__)

#: Bump when the cache JSON schema changes (invalidates older cache files).
RULEBOOK_CACHE_VERSION = 1

# Cache-status values for RulebookResolution.cache_status.
_STATUS_LIVE = "live_fetch"  # bundle came from a successful live source probe
_STATUS_CACHE_HIT = "cache_hit"  # a matching prior cache entry existed
_STATUS_SEED = "seed"  # only the committed seed baseline existed
_STATUS_REBUILT = "rebuilt_no_cache"  # no cache and no seed — pinned rebuild only


@dataclass(frozen=True)
class RulebookResolution:
    """Result of resolving the active rulebook with cache/seed/drift accounting."""

    bundle: RuleBundle  # live bundle (with compiled patterns) for classification
    jurisdictions: tuple[str, ...]
    cache_status: str
    drift_detected: bool
    baseline_sha256: str | None  # what the current bundle was compared against
    cache_version: int = RULEBOOK_CACHE_VERSION


def _juris_key(jurisdictions: tuple[str, ...]) -> str:
    """Stable filename key from a jurisdiction set (sorted, upper-cased)."""
    return "_".join(sorted({j.upper() for j in jurisdictions}))


def cache_filename(jurisdictions: tuple[str, ...], *, version: int = RULEBOOK_CACHE_VERSION) -> str:
    """Return the versioned cache filename for a jurisdiction set."""
    return f"rulebook_v{version}_{_juris_key(jurisdictions)}.json"


def default_cache_dir() -> Path:
    """Per-study live cache location (audit zone, metadata only, no LLM access)."""
    return Path(config.STUDY_AUDIT_DIR) / "phi_rulebook"


def default_seed_dir() -> Path:
    """Committed v1 seed location (airgapped first-run baseline)."""
    return Path(config.CONFIG_DEFAULTS_DIR) / "phi_rulebook"


def _cache_payload(bundle: RuleBundle, jurisdictions: tuple[str, ...]) -> dict:
    """Build the value-free cache JSON for a resolved bundle."""
    payload: dict = bundle.to_json()
    payload["cache_version"] = RULEBOOK_CACHE_VERSION
    payload["jurisdictions"] = sorted({j.upper() for j in jurisdictions})
    return payload


def read_cache_entry(path: Path, *, jurisdictions: tuple[str, ...]) -> dict | None:
    """Read + validate a cache/seed entry; return None if absent/invalid/mismatched.

    Validates the cache version and that the recorded jurisdictions match the
    requested set, so a stale-schema or wrong-jurisdiction file is ignored rather
    than trusted. Fail-soft: any I/O or parse error returns None.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _logger.warning("rulebook cache unreadable: %s (ignored)", path)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("cache_version") != RULEBOOK_CACHE_VERSION:
        _logger.warning(
            "rulebook cache version mismatch at %s (have %s, want %s); ignored",
            path,
            data.get("cache_version"),
            RULEBOOK_CACHE_VERSION,
        )
        return None
    want = sorted({j.upper() for j in jurisdictions})
    if data.get("jurisdictions") != want:
        return None
    if not isinstance(data.get("rules_sha256"), str):
        return None
    return data


def write_cache_entry(cache_dir: Path, bundle: RuleBundle, jurisdictions: tuple[str, ...]) -> Path:
    """Persist the bundle provenance to the versioned cache; return the path."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / cache_filename(jurisdictions)
    payload = _cache_payload(bundle, jurisdictions)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def resolve_rulebook(
    privacy_config: StudyPrivacyConfig,
    *,
    allow_network: bool = False,
    cache_dir: Path | None = None,
    seed_dir: Path | None = None,
) -> RulebookResolution:
    """Resolve the active rulebook, comparing to cache/seed and updating the cache.

    Order of baseline preference for drift comparison: a matching live cache
    entry, else the committed seed. The live classification rules always come
    from :func:`refresh_jurisdiction_rules` (pinned in code), so resolution never
    depends on the network or the cache being present.
    """
    jurisdictions = tuple(privacy_config.jurisdictions)
    cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    seed_dir = Path(seed_dir) if seed_dir is not None else default_seed_dir()

    bundle = refresh_jurisdiction_rules(privacy_config, allow_network=allow_network)

    cache_entry = read_cache_entry(
        cache_dir / cache_filename(jurisdictions), jurisdictions=jurisdictions
    )
    seed_entry = (
        None
        if cache_entry is not None
        else read_cache_entry(seed_dir / cache_filename(jurisdictions), jurisdictions=jurisdictions)
    )
    baseline = cache_entry or seed_entry
    baseline_sha = baseline.get("rules_sha256") if baseline else None
    drift = baseline_sha is not None and baseline_sha != bundle.rules_sha256

    if bundle.source_mode == "latest_official":
        status = _STATUS_LIVE
    elif cache_entry is not None:
        status = _STATUS_CACHE_HIT
    elif seed_entry is not None:
        status = _STATUS_SEED
    else:
        status = _STATUS_REBUILT

    if drift:
        _logger.warning(
            "PHI rulebook DRIFT for %s: effective rules_sha256 %s differs from "
            "recorded baseline %s. The classification rule set changed since the "
            "last recorded run — confirm the change is intended.",
            _juris_key(jurisdictions),
            bundle.rules_sha256[:12],
            (baseline_sha or "")[:12],
        )

    # Persist the current bundle provenance so the next run has a live baseline.
    try:
        write_cache_entry(cache_dir, bundle, jurisdictions)
    except OSError:  # pragma: no cover - cache write is best-effort
        _logger.warning("rulebook cache write failed at %s (non-fatal)", cache_dir)

    return RulebookResolution(
        bundle=bundle,
        jurisdictions=jurisdictions,
        cache_status=status,
        drift_detected=drift,
        baseline_sha256=baseline_sha,
    )
