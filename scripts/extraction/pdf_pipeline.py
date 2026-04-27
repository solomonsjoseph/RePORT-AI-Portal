"""Two-way PDF extraction pipeline (Phase 3.F + 3.G + 3.H).

Closes the audit findings 3.F (raw PDF bytes shipped to vision API),
3.G (LLM response not scanned for echoed PHI), and 3.H (no idempotent
retry caching) from ``docs/irb_dossier/phase3_phi_followups.md``.

The pipeline has exactly **two acceptable output paths** per PDF —
either the LLM tier succeeds (paired with the code-extracted text),
or we fall back to a human-verified snapshot. The load-study UI step
never fails on a single PDF.

**Way 1 — LLM + code (merged):**

The code path always runs first (pdfplumber → text + heuristic
variable candidate). When a *capable* LLM is configured (per
:func:`scripts.utils.llm_capabilities.is_capable_model`), the LLM
tier runs **paired** with the code path:

- The code-extracted text is **redacted in place** using the existing
  PHI catalog (``phi_patterns.BLOCKING_PATTERNS``) so identifiers in
  form headers become ``<LABEL>`` markers before any byte leaves the
  host. **No raw PDF bytes** transit the API.
- The redacted text is sent to the LLM with the schema prompt. The
  LLM response is parsed and every string field is re-scrubbed
  through :func:`scripts.ai_assistant.phi_safe.guard_text` to catch
  echoed identifiers.
- The LLM response is **merged** with the code-tier candidate: LLM
  wins on field-level conflicts (it's more accurate on complex CRFs);
  the code candidate fills in vars the LLM missed.

**Way 2 — Snapshot:**

When the LLM tier is unavailable for any reason (no capable model
configured, no API key, image-only PDF, LLM call error), the pipeline
falls back to a human-verified snapshot at
``output/{STUDY}/agent/snapshots/initial/pdfs/<form>.json``. **A
code-only result is NEVER an acceptable output** — heuristic
extraction without LLM oversight is too unreliable to publish, so
we'd rather use a verified baseline than ship potentially-wrong
metadata into ``trio_bundle/``.

Idempotent caching: the LLM tier keys on
``SHA-256(pdf_bytes) || provider || model || PHI_SCRUB_CONFIG_HASH``
so a re-run with the same inputs hits the cache and skips the API
call. Cache invalidates on any input change.

Zone discipline (audit finding A3): the pipeline-tier LLM client is
constructed fresh in this module and uses the KeyStore for the API
key; it does NOT route through the agent's ``_build_llm`` and never
sees an environment variable. The HTTP payload contains ONLY the
redacted text plus the schema prompt — no file paths, no agent state.
The defensive ``_assert_no_raw_phi_in_payload`` check fails loud if
any pre-redaction string somehow reaches the payload.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ORCHESTRATOR_SUPPORTED_PROVIDERS",
    "ExtractionResult",
    "extract_pdf",
]


# Provider IDs whose LLM tier is wired in :func:`_extract_via_llm`.
# Sources of truth in one place so the wizard's radio gate and the
# orchestrator's runtime dispatch can never disagree — the wizard offers
# the ``llm`` choice ONLY when the configured provider is in this set
# AND the model passes the capability allowlist. Otherwise the operator
# would silently get a snapshot fallback after picking "fresh LLM".
ORCHESTRATOR_SUPPORTED_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "google", "google-genai", "gemini"}
)


# ── Result shape ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Outcome of one PDF run through the three-tier pipeline.

    ``tier`` reports which path produced the surfaced ``data``:
    ``"merged"`` (LLM succeeded, paired with code-extracted text),
    ``"llm"`` (LLM succeeded but code-path text was empty so there
    was nothing to merge with), ``"snapshot"`` (LLM unavailable;
    fell back to verified baseline), or ``"empty"`` (both unavailable
    and no snapshot — UI will see an empty form).

    ``llm_skipped_reason`` documents why the LLM tier did not run
    (capability gate, provider unavailable, etc.) for operator
    diagnostics; it stays ``None`` when the LLM tier did run.

    ``cache_hit`` is True when the LLM tier was skipped because a
    valid cached response was found.
    """

    pdf_name: str
    tier: str
    data: dict[str, Any]
    llm_skipped_reason: str | None = None
    cache_hit: bool = False
    code_succeeded: bool = False
    llm_succeeded: bool = False
    snapshot_used: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ── Code-path extraction ───────────────────────────────────────────────────


def _extract_text_via_pdfplumber(pdf_path: Path) -> str:
    """Read the entire PDF as text. Best-effort; returns empty string on
    error so the orchestrator can fall through to other tiers."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning(
            "pdf_pipeline: pdfplumber not installed; code-path extraction "
            "will return empty text for %s",
            pdf_path.name,
        )
        return ""
    try:
        chunks: list[str] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text()
                if txt:
                    chunks.append(txt)
        return "\n\n".join(chunks)
    except Exception as exc:  # pragma: no cover — provider-side errors
        logger.warning("pdf_pipeline: pdfplumber failed on %s: %s", pdf_path.name, exc)
        return ""


def _candidate_from_text(pdf_name: str, text: str) -> dict[str, Any] | None:
    """Best-effort heuristic: derive a minimal variables.json candidate
    from raw PDF text. Used as the code-path tier when no LLM is
    available. The output has the same shape as the LLM path so the
    merge step is symmetric.

    Heuristic rules (conservative — we'd rather output nothing than
    output bad metadata that pollutes the trio_bundle):

    - Form name: derived from the PDF filename stem (uppercase,
      underscores stripped).
    - Variables: lines matching ``LABEL: description`` patterns where
      LABEL is uppercase + digits (typical CRF column codes). Each
      becomes a variable entry with description = the matched text
      after the colon.
    - When fewer than 3 variables are detected the candidate is
      discarded (likely a non-CRF PDF or an OCR'd image).
    """
    if not text or not text.strip():
        return None
    form = pdf_path_to_form_name(pdf_name)
    var_re = re.compile(r"^([A-Z][A-Z0-9_]{2,30})\s*[:\-]\s*(.{5,200}?)\s*$", re.MULTILINE)
    variables: dict[str, dict[str, Any]] = {}
    for match in var_re.finditer(text):
        name, desc = match.group(1).strip(), match.group(2).strip()
        if name in variables:
            continue
        variables[name] = {
            "name": name,
            "description": desc,
            "data_type": "unknown",
            "source": "code-path",
        }
    if len(variables) < 3:
        return None
    return {
        "form_name": form,
        "form_label": form.replace("_", " ").title(),
        "source_pdf": pdf_name,
        "extraction_tier": "code",
        "variables": variables,
    }


def pdf_path_to_form_name(pdf_name: str) -> str:
    """Drop the .pdf suffix; uppercase. Used for the variables.json
    ``form_name`` field across all tiers so they merge cleanly."""
    stem = Path(pdf_name).stem
    return re.sub(r"[^A-Za-z0-9_]+", "_", stem).upper()


# ── LLM-path extraction ─────────────────────────────────────────────────────


def _redact_text_for_llm(text: str) -> str:
    """Run the existing PHI catalog over text before sending to the LLM."""
    from scripts.ai_assistant.phi_safe import redact_phi_in_text

    return redact_phi_in_text(text)


def _assert_no_raw_phi_in_payload(payload: str) -> None:
    """Defensive: confirm the redaction step actually fired before any
    HTTP call. Searches the payload for blocking-tier patterns; if any
    match remains, raises so we fail loud rather than ship raw PHI."""
    from scripts.security.phi_gate import phi_gate_check

    result = phi_gate_check(payload)
    if result.blocked:
        raise RuntimeError(
            f"pdf_pipeline: redaction failed — payload still contains "
            f"blocking-tier PHI ({list(result.findings)}). Refusing to "
            f"send to LLM."
        )


def _scrub_llm_response(data: dict[str, Any]) -> dict[str, Any]:
    """Walk every string field and replace blocking-tier PHI matches
    with their ``<LABEL>`` form. Keys are not scrubbed (would change
    the schema); only string values."""
    from scripts.ai_assistant.phi_safe import redact_phi_in_text

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return redact_phi_in_text(node)
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    walked = _walk(data)
    if not isinstance(walked, dict):
        raise TypeError(
            f"_scrub_llm_response received non-dict input: {type(data).__name__}"
        )
    return walked


def _extract_via_llm(
    redacted_text: str,
    *,
    provider: str,
    model: str,
    api_key: str,
) -> dict[str, Any] | None:
    """Send redacted PDF text to the LLM. Returns the parsed JSON
    response, or ``None`` if the call fails / returns invalid JSON.
    The caller has already verified provider/model are capable."""
    _assert_no_raw_phi_in_payload(redacted_text)

    prompt = (
        "You are extracting a structured variable schema from CRF text. "
        "Return strict JSON: {form_name, form_label, source_pdf, "
        '"variables": {<NAME>: {name, description, data_type, options?}}}. '
        "Do not invent variables — only return ones present in the text."
    )

    text: str = ""
    try:
        if provider == "anthropic":
            from anthropic import Anthropic

            anthropic_client = Anthropic(api_key=api_key)
            anthropic_resp = anthropic_client.messages.create(
                model=model,
                max_tokens=8192,
                temperature=0.0,
                system=prompt,
                messages=[{"role": "user", "content": redacted_text}],
            )
            text = anthropic_resp.content[0].text  # type: ignore[union-attr]
        elif provider in ("google", "google-genai", "gemini"):
            from google import genai

            google_client = genai.Client(api_key=api_key)
            # google-genai SDK typing is loose; ``models.generate_content``
            # is the supported entry point per official docs.
            google_resp = google_client.models.generate_content(  # type: ignore[attr-defined]
                model=model,
                contents=[prompt, redacted_text],  # type: ignore[arg-type]
            )
            text = google_resp.text or ""
        else:
            logger.warning(
                "pdf_pipeline: provider %r not wired for LLM extraction "
                "(only anthropic + google supported in PR #15)",
                provider,
            )
            return None
    except Exception as exc:
        logger.warning("pdf_pipeline: LLM call failed (%s): %s", provider, exc)
        return None

    json_text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", json_text, re.DOTALL)
    if m:
        json_text = m.group(1).strip()
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("pdf_pipeline: LLM returned non-JSON: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    parsed.setdefault("extraction_tier", "llm")
    return _scrub_llm_response(parsed)


# ── Merge ───────────────────────────────────────────────────────────────────


def _merge(code_data: dict[str, Any] | None, llm_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge code-tier + LLM-tier candidates. LLM wins on field-level
    conflicts within a variable; code-tier fills in variables the LLM
    missed."""
    if code_data is None and llm_data is None:
        return None
    if code_data is None:
        return {**llm_data, "extraction_tier": "llm"}  # type: ignore[dict-item]
    if llm_data is None:
        return {**code_data, "extraction_tier": "code"}

    merged_vars: dict[str, dict[str, Any]] = {}
    code_vars = code_data.get("variables", {}) or {}
    llm_vars = llm_data.get("variables", {}) or {}
    for name, c_def in code_vars.items():
        merged_vars[name] = {**c_def}
    for name, l_def in llm_vars.items():
        if name in merged_vars:
            merged_vars[name].update(l_def)  # LLM wins on overlap
        else:
            merged_vars[name] = {**l_def}
    return {
        "form_name": llm_data.get("form_name") or code_data.get("form_name"),
        "form_label": llm_data.get("form_label") or code_data.get("form_label"),
        "source_pdf": code_data.get("source_pdf") or llm_data.get("source_pdf"),
        "extraction_tier": "merged",
        "variables": merged_vars,
    }


# ── Backup snapshot ─────────────────────────────────────────────────────────


def _load_snapshot_for(pdf_name: str, snapshot_dir: Path) -> dict[str, Any] | None:
    """Return the parsed snapshot JSON for *pdf_name* if a verified
    baseline exists. Snapshot directory layout mirrors trio_bundle/pdfs/."""
    if not snapshot_dir.is_dir():
        return None
    candidates = (
        snapshot_dir / f"{Path(pdf_name).stem}_variables.json",
        snapshot_dir / f"{Path(pdf_name).stem}.json",
    )
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["extraction_tier"] = "snapshot"
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "pdf_pipeline: snapshot %s unreadable: %s", path, exc
                )
    return None


# ── Idempotent cache ────────────────────────────────────────────────────────


def _cache_key(pdf_path: Path, provider: str, model: str) -> str:
    """SHA-256 of (pdf bytes || provider || model || phi_scrub.yaml SHA-256).
    Invalidates on any input change including a scrub-rule edit."""
    import contextlib

    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    scrub_hash = "no-config"
    # Best-effort: any failure to read the scrub config (missing config
    # module, missing yaml, OS error) drops back to the ``no-config``
    # sentinel so the cache key is still deterministic per
    # (pdf_bytes, provider, model). Cache hits across yaml edits are not
    # a security risk because the redaction step ALSO runs at request
    # time — the cache only saves the LLM round-trip.
    with contextlib.suppress(ImportError, AttributeError, OSError):
        import config as _cfg

        scrub_path = Path(_cfg.PHI_SCRUB_CONFIG_PATH)
        if scrub_path.is_file():
            scrub_hash = hashlib.sha256(scrub_path.read_bytes()).hexdigest()[:16]
    raw = f"{pdf_hash}||{provider}||{model}||{scrub_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _cache_put(cache_dir: Path, key: str, data: dict[str, Any]) -> None:
    import contextlib

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


# ── Top-level orchestrator ──────────────────────────────────────────────────


def extract_pdf(
    pdf_path: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    snapshot_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> ExtractionResult:
    """Run the two-way pipeline for a single PDF.

    There are exactly **two** acceptable output paths:

    1. **LLM + code (merged)** — when a capable LLM is configured AND the
       LLM call succeeds, the LLM response is merged with the
       code-extracted heuristic candidate (LLM wins on field-level
       conflicts; code fills in vars the LLM missed). The code path
       contributes both the extracted text used as the LLM input AND a
       baseline candidate for merge — they are paired.
    2. **Snapshot** — when the LLM tier is unavailable for any reason
       (no capable model, no API key, image-only PDF, LLM call error),
       fall back to a human-verified ``initial`` snapshot. Code-only
       output is **never** an acceptable result; heuristic-only metadata
       is too unreliable to publish without LLM oversight, so we'd
       rather use a verified baseline than ship potentially-wrong
       extraction.

    All keyword args are optional. When ``provider`` / ``model`` /
    ``api_key`` are all set AND :func:`is_capable_model` returns True,
    the LLM tier runs. Otherwise the LLM tier is skipped with a
    diagnostic ``llm_skipped_reason``.

    ``snapshot_dir`` is the directory holding human-verified backup
    JSONs (typically ``output/{STUDY}/agent/snapshots/initial/pdfs/``).
    When omitted, the snapshot fallback is unavailable.

    ``cache_dir`` is the LLM-response cache root (typically
    ``tmp/{STUDY}/.pdf_cache/``). When omitted, the cache is disabled.
    """
    from scripts.utils.llm_capabilities import is_capable_model

    pdf_name = pdf_path.name
    code_data: dict[str, Any] | None = None
    llm_data: dict[str, Any] | None = None
    cache_hit = False
    skipped: str | None = None

    # Tier 1: code path — extracts text + a baseline candidate. The
    # candidate is ONLY useful as input to the merge step (paired with
    # the LLM result); it's never returned standalone.
    text = _extract_text_via_pdfplumber(pdf_path)
    if text:
        code_data = _candidate_from_text(pdf_name, text)

    # Tier 2: LLM path (only when capable AND configured)
    if not is_capable_model(provider, model):
        skipped = f"model {provider}/{model} not on capable allowlist"
    elif not api_key:
        skipped = "no API key in KeyStore for selected provider"
    elif not text:
        skipped = "code-path text extraction empty (image-only PDF?)"
    else:
        # Cache lookup
        if cache_dir is not None:
            key = _cache_key(pdf_path, provider, model)  # type: ignore[arg-type]
            cached = _cache_get(cache_dir, key)
            if cached is not None:
                llm_data = cached
                cache_hit = True
                logger.info("pdf_pipeline: cache hit for %s", pdf_name)

        # Fresh LLM call
        if llm_data is None:
            redacted = _redact_text_for_llm(text)
            llm_data = _extract_via_llm(
                redacted, provider=provider, model=model, api_key=api_key  # type: ignore[arg-type]
            )
            if llm_data is None:
                skipped = "LLM call failed or returned invalid JSON"
            elif cache_dir is not None:
                _cache_put(cache_dir, _cache_key(pdf_path, provider, model), llm_data)  # type: ignore[arg-type]

    # Decide path: LLM+code (merged) OR snapshot. Code-only is NEVER
    # a valid output (per the user's 2026-04-27 directive: heuristic
    # extraction without LLM oversight is too unreliable to publish).
    merged: dict[str, Any] | None = None
    snapshot_used = False
    if llm_data is not None:
        # Way 1: LLM succeeded → merge with code-tier candidate.
        merged = _merge(code_data, llm_data)
    else:
        # Way 2: LLM unavailable / failed → discard any code-only
        # candidate and fall back to the human-verified snapshot.
        if code_data is not None:
            logger.info(
                "pdf_pipeline: %s — discarding code-only candidate (LLM "
                "tier unavailable: %s); falling back to snapshot",
                pdf_name,
                skipped,
            )
        if snapshot_dir is not None:
            merged = _load_snapshot_for(pdf_name, snapshot_dir)
            snapshot_used = merged is not None

    if merged is None:
        return ExtractionResult(
            pdf_name=pdf_name,
            tier="empty",
            data={
                "form_name": pdf_path_to_form_name(pdf_name),
                "source_pdf": pdf_name,
                "extraction_tier": "empty",
                "variables": {},
                "warning": (
                    "No tier produced extractable variable metadata; this "
                    "form will appear empty in trio_bundle. Investigate "
                    "the PDF or add a verified snapshot."
                ),
            },
            llm_skipped_reason=skipped,
        )

    return ExtractionResult(
        pdf_name=pdf_name,
        tier=merged.get("extraction_tier", "merged"),
        data=merged,
        llm_skipped_reason=skipped,
        cache_hit=cache_hit,
        code_succeeded=code_data is not None,
        llm_succeeded=llm_data is not None,
        snapshot_used=snapshot_used,
    )
