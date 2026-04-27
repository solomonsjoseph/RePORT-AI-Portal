"""k-anonymity / small-cell suppression gate for agent-tool responses.

At the trio-bundle -> agent boundary, row-level queries can surface
equivalence classes (age-band x sex x district x outcome) with very
small sample sizes. A response returning one matched row with all
sensitive attributes visible defeats the whole scrub — the scrub
guarantees de-identification at rest, but k-anon defends against
re-identification at query time.

This module provides two utilities:

* :func:`kanon_check` — given a list of equivalence-class records and
  a *k* threshold, returns a :class:`KAnonResult` with ``blocked`` set
  when any class has fewer than *k* members.
* :func:`suppress_small_cells` — given aggregate counts, replaces any
  count < *k* with the string ``"<5"`` (or equivalent) so the agent
  surface never reveals an exact small-cell value.

IRB-grade benchmark anchor: Pillar 1.7 — k-anonymity ≥ 5 enforced on
quasi-identifier combos surfaced to the agent; l-diversity ≥ 2 is a
tracked design gap (see references.rst).
Reference: ICMR 2017 §11.7; NIST SP 800-188 §5.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "KAnonResult",
    "kanon_check",
    "mask_small_cell",
    "suppress_small_cells",
]


_DEFAULT_K = 5
_SUPPRESSED_LABEL = "<5"


@dataclass(frozen=True, slots=True)
class KAnonResult:
    """Outcome of a k-anonymity check.

    ``blocked`` is ``True`` when at least one equivalence class is
    smaller than *k*. ``smallest_class_size`` reports the minimum
    class size observed (or 0 when no classes were supplied).
    ``violating_keys`` is a sorted tuple of equivalence-class keys
    whose size is below the threshold; each key is a string form of
    the quasi-identifier tuple, safe to log.
    """

    blocked: bool
    smallest_class_size: int
    violating_keys: tuple[str, ...]


def _key_to_str(key: tuple[Any, ...]) -> str:
    return "|".join("" if v is None else str(v) for v in key)


def kanon_check(
    rows: Iterable[Mapping[str, Any]],
    *,
    quasi_identifiers: tuple[str, ...],
    k: int = _DEFAULT_K,
) -> KAnonResult:
    """Return a :class:`KAnonResult` for the given rows + quasi-identifiers.

    Does NOT mutate *rows*. Counts equivalence classes by the tuple of
    quasi-identifier values; any class with size < *k* marks the result
    as ``blocked``. An empty input returns ``blocked=False`` with zero
    class size — caller decides whether empty is permitted.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not quasi_identifiers:
        raise ValueError("quasi_identifiers must be non-empty")

    counts: dict[tuple[Any, ...], int] = {}
    for row in rows:
        key = tuple(row.get(col) for col in quasi_identifiers)
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return KAnonResult(blocked=False, smallest_class_size=0, violating_keys=())

    smallest = min(counts.values())
    violating = sorted(_key_to_str(key) for key, size in counts.items() if size < k)
    blocked = smallest < k
    if blocked:
        logger.warning(
            "kanon_check: smallest class %d < k=%d (%d violating equivalence classes)",
            smallest,
            k,
            len(violating),
        )
    return KAnonResult(
        blocked=blocked,
        smallest_class_size=smallest,
        violating_keys=tuple(violating),
    )


def mask_small_cell(count: int, *, k: int = _DEFAULT_K, label: str = _SUPPRESSED_LABEL) -> Any:
    """Return *count* if ``count >= k``, else *label* (default ``"<5"``).

    Pair with :func:`suppress_small_cells` when aggregating cross-
    tabulations for the agent surface.
    """
    if count >= k:
        return count
    return label


def suppress_small_cells(
    counts: Mapping[Any, int],
    *,
    k: int = _DEFAULT_K,
    label: str = _SUPPRESSED_LABEL,
) -> dict[Any, Any]:
    """Return a new dict where values < *k* are replaced with *label*.

    Leaves keys untouched. Intended for cross-tab / frequency counts
    that a tool is about to return to the LLM.
    """
    return {key: mask_small_cell(val, k=k, label=label) for key, val in counts.items()}
