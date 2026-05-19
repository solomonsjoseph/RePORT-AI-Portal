"""Per-run context helpers: run_id resolution and sidecar writers.

All runtime-only fields (timestamps, run identifiers) that would otherwise
bake into the primary pipeline artifacts are instead written here to per-run
sidecars under ``output/{STUDY}/runs/{run_id}/``.  Primary artifacts stay
content-only and byte-identical across consecutive runs on identical input.

Public API
----------
resolve_run_id()
    Return the active run identifier.  Reads ``REPORTAL_RUN_ID`` env var
    when set; otherwise mints a fresh ``run_<uuid4().hex>`` string.

write_extraction_timing_sidecar(...)
    Atomically write ``output/{STUDY}/runs/{run_id}/extraction_timing.json``.

write_lineage_timing_sidecar(...)
    Atomically write ``output/{STUDY}/runs/{run_id}/lineage_timing.json``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.extraction.io import atomic_write_json

__all__ = [
    "resolve_run_id",
    "write_extraction_timing_sidecar",
    "write_lineage_timing_sidecar",
]

_ENV_VAR = "REPORTAL_RUN_ID"


def resolve_run_id() -> str:
    """Return the active run identifier.

    Reads the ``REPORTAL_RUN_ID`` environment variable when set; otherwise
    generates a fresh ``run_<uuid4().hex>`` string.  The future
    ``extract_to_llm_source`` CLI wrapper (P3.1) will set ``REPORTAL_RUN_ID``
    before launching the pipeline so every sidecar produced in one invocation
    shares the same run_id.  When the env var is absent, each call generates
    a distinct id — existing single-run behaviour is preserved.
    """
    env_val = os.environ.get(_ENV_VAR)
    if env_val:
        return env_val
    return f"run_{uuid4().hex}"


def write_extraction_timing_sidecar(
    *,
    output_dir: Path,
    run_id: str,
    study: str,
    extraction_utc: str,
    pipeline_version: str,
    per_form_timing: dict[str, Any] | None = None,
) -> Path:
    """Atomically write ``runs/{run_id}/extraction_timing.json`` under *output_dir*.

    Parameters
    ----------
    output_dir:
        Study-level output root (e.g. ``output/{STUDY}``).
    run_id:
        Active run identifier (from :func:`resolve_run_id`).
    study:
        Study name, included verbatim for human readability.
    extraction_utc:
        ISO-8601 UTC timestamp string captured at extraction start.
    pipeline_version:
        ``__version__`` of the pipeline at extraction time.
    per_form_timing:
        Optional mapping of form/file name → elapsed-seconds float.

    Returns
    -------
    Path
        Absolute path to the written sidecar file.
    """
    sidecar_dir = output_dir / "runs" / run_id
    sidecar_path = sidecar_dir / "extraction_timing.json"

    payload: dict[str, Any] = {
        "run_id": run_id,
        "study": study,
        "extraction_utc": extraction_utc,
        "pipeline_version": pipeline_version,
    }
    if per_form_timing is not None:
        payload["per_form_timing"] = per_form_timing

    atomic_write_json(sidecar_path, payload)
    return sidecar_path


def write_lineage_timing_sidecar(
    *,
    runs_dir: Path,
    run_id: str,
    study: str,
    generated_utc: str,
    mtime_utc: dict[str, str],
) -> Path:
    """Atomically write ``{runs_dir}/{run_id}/lineage_timing.json``.

    Parameters
    ----------
    runs_dir:
        Parent directory for per-run sidecars (e.g. ``output/{STUDY}/runs``).
    run_id:
        Active run identifier.
    study:
        Study name.
    generated_utc:
        ISO-8601 UTC timestamp at manifest generation time.
    mtime_utc:
        Mapping of file path (relative to their respective root) → ISO-8601
        mtime string, collected from all input and output file records.

    Returns
    -------
    Path
        Absolute path to the written sidecar file.
    """
    sidecar_dir = runs_dir / run_id
    sidecar_path = sidecar_dir / "lineage_timing.json"

    payload: dict[str, Any] = {
        "run_id": run_id,
        "study": study,
        "generated_utc": generated_utc,
        "mtime_utc": mtime_utc,
    }
    atomic_write_json(sidecar_path, payload)
    return sidecar_path
