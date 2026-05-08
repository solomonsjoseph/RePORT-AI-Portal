"""Per-form dispatcher: extractor + reviewer in parallel batches of 4-8.

Returns a tuple `(results, errors)`:
- `results`: list of merged extractor+reviewer dicts for forms that
  completed successfully.
- `errors`: list of `(form, exception)` tuples for forms that failed.
  Failures are logged but do not abort the remaining work.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from scripts.source_truth.sot_extractor_agent import run_extractor
from scripts.source_truth.sot_reviewer_agent import run_reviewer
from scripts.utils.logging_system import get_logger

_LOG = get_logger(__name__)


def _run_one_form(
    form: str,
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
    reviews_dir: Path,
) -> dict[str, Any]:
    extracted = run_extractor(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
        drafts_dir=drafts_dir,
        evidence_pack_drafts_dir=evidence_pack_drafts_dir,
    )
    reviewed = run_reviewer(
        form=form,
        sot_dir=sot_dir,
        raw_pdf_dir=raw_pdf_dir,
        dataset_dir=dataset_dir,
        pilot_dir=pilot_dir,
        draft_yaml_path=Path(extracted["yaml_path"]),
        draft_pack_path=Path(extracted["evidence_pack_path"]),
        reviews_dir=reviews_dir,
    )
    return {**extracted, **reviewed}


def dispatch_forms(
    forms: Iterable[str],
    sot_dir: Path,
    raw_pdf_dir: Path,
    dataset_dir: Path,
    pilot_dir: Path,
    drafts_dir: Path,
    evidence_pack_drafts_dir: Path,
    reviews_dir: Path,
    concurrency: int = 4,
) -> tuple[list[dict[str, Any]], list[tuple[str, BaseException]]]:
    """Per-form dispatcher: extractor + reviewer in parallel batches of 4-8.

    Returns a tuple `(results, errors)`:
    - `results`: list of merged extractor+reviewer dicts for forms that
      completed successfully.
    - `errors`: list of `(form, exception)` tuples for forms that failed.
      Failures are logged but do not abort the remaining work.
    """
    form_list = list(forms)
    out: list[dict[str, Any]] = []
    errors: list[tuple[str, BaseException]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(8, concurrency))) as pool:
        futures = {
            pool.submit(
                _run_one_form,
                form=form,
                sot_dir=sot_dir,
                raw_pdf_dir=raw_pdf_dir,
                dataset_dir=dataset_dir,
                pilot_dir=pilot_dir,
                drafts_dir=drafts_dir,
                evidence_pack_drafts_dir=evidence_pack_drafts_dir,
                reviews_dir=reviews_dir,
            ): form
            for form in form_list
        }
        for fut in as_completed(futures):
            form = futures[fut]
            try:
                out.append(fut.result())
            except Exception as exc:
                _LOG.exception("sot_dispatch.failed form=%s", form)
                errors.append((form, exc))
    return out, errors
