"""Security surface for RePORT AI Portal.

**What.** The public security-module boundary for the four-tier honest-broker
pipeline: zone-enforcement helpers (:mod:`.secure_env`), the 8-action PHI
scrubber (:mod:`.phi_scrub`), the query-time PHI gate and k-anonymity gate
(:mod:`.phi_gate`, :mod:`.kanon_gate`), the shared regex catalog
(:mod:`.phi_patterns`), and the clinical-phrase allowlist
(:mod:`.phi_allowlist`).

**Why.** A single import surface keeps call sites honest — downstream modules
write ``from scripts.security import assert_output_zone, phi_gate_check``
rather than digging into the per-module layout. This also gates which
symbols are part of the stable runtime contract (the ``__all__`` below) vs.
internal implementation detail.

**How.** Each submodule stays independently importable; this ``__init__``
re-exports the most commonly called symbols. For surface that is only
useful inside one submodule (like individual regex compiler helpers), do
NOT add a re-export — keep ``__all__`` tight so accidental couplings are
visible.
"""

from __future__ import annotations

from .kanon_gate import KAnonResult, kanon_check, mask_small_cell, suppress_small_cells
from .phi_gate import PHIGateConfigError, PHIGateResult, phi_gate_check
from .phi_scrub import (
    PHIScrubConfig,
    PHIScrubError,
    bootstrap_key,
    load_key,
    load_scrub_config,
    run_scrub,
)
from .secure_env import (
    ZoneViolationError,
    assert_clean_zone,
    assert_not_raw,
    assert_output_not_in_data,
    assert_output_zone,
    assert_write_zone,
    validate_paths,
)

__all__ = [  # noqa: RUF022 — grouped by concept for readability, not alphabetical
    # Zone enforcement
    "ZoneViolationError",
    "assert_clean_zone",
    "assert_not_raw",
    "assert_output_not_in_data",
    "assert_output_zone",
    "assert_write_zone",
    "validate_paths",
    # PHI scrub (8-action catalog)
    "PHIScrubConfig",
    "PHIScrubError",
    "bootstrap_key",
    "load_key",
    "load_scrub_config",
    "run_scrub",
    # Agent-boundary gates
    "KAnonResult",
    "PHIGateConfigError",
    "PHIGateResult",
    "kanon_check",
    "mask_small_cell",
    "phi_gate_check",
    "suppress_small_cells",
]
