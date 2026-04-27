"""Local-Ollama NER sweep for free-text narrative residuals — DESIGN STUB.

The rule catalog plus the clinical-phrase allowlist are the primary
defences against PHI in structured fields. For residuals inside
narrative free-text (``*_SPECIFY``, ``*_OTH``, ``AESPECIFY``, PDF body
text), a regex catalog cannot enumerate every possible PHI token. This
module reserves the API surface for a planned local-Ollama NER sweep
to address that long tail.

**Planned design:**

* Invoke a local large language model via Ollama (`qwen3:14b` or
  `qwen3:8b` with a structured JSON prompt) per narrative
  chunk. The Ollama endpoint is already part of this project's
  dependency set, so no new heavyweight runtime dep is introduced.
* The prompt instructs the model to emit a list of ``{offset, length,
  category}`` tuples for every span that looks like PHI. The caller
  redacts each span in-place and writes a count to the scrub audit.
* :mod:`scripts.security.phi_allowlist` runs BEFORE the NER call to
  short-circuit the common case and keep latency bounded.
* Feature-flag gate: ``REPORTALIN_OLLAMA_NER=1`` must be set AND the
  configured Ollama endpoint must respond to a health check; otherwise
  the sweep is a no-op.

**Why the implementation is deferred:** prompt + model calibration
against the Indo-VAP narrative corpus requires iteration with the
operator. The rest of the honest-broker architecture can ship without
it because:

1. ``drop_fields`` already eliminates known narrative fields wholesale
   (the whole-value-hash pseudo has been replaced with DROP).
2. :func:`scripts.security.phi_gate.phi_gate_check` runs on every
   agent tool return, catching any PHI pattern that does leak.
3. :func:`scripts.utils.log_hygiene.install_phi_redactor` redacts
   pipeline logs, closing the last in-process PHI side-channel.

When the implementation lands, this module will grow
:func:`sweep_narrative` + :func:`_invoke_ollama_ner` + a structured
JSON prompt template. Until then :func:`is_enabled` returns False and
:func:`sweep_narrative` raises :class:`PHINERNotImplementedError` so a
caller wiring it by mistake fails fast.
"""

from __future__ import annotations

import os

__all__ = [
    "PHINERNotImplementedError",
    "is_enabled",
    "sweep_narrative",
]

_OLLAMA_NER_FLAG = "REPORTALIN_OLLAMA_NER"


class PHINERNotImplementedError(RuntimeError):
    """Raised when the NER hook is invoked before the implementation exists.

    Keeps the public symbol table stable so ``from scripts.security import
    phi_ner`` callers compile; enforces fail-fast at runtime if someone
    wires an unfinished code path by mistake.
    """


def is_enabled() -> bool:
    """Return True iff ``REPORTALIN_OLLAMA_NER`` opts in AND the sweep is built.

    Today the second term is always False because the implementation is
    not in the tree. Callers should treat ``False`` as "the NER sweep is
    a no-op; proceed without narrative NER scrubbing."
    """
    env_opt_in = os.environ.get(_OLLAMA_NER_FLAG, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # The implementation is not yet in the tree; do not flip to True
    # until the prompt + calibration PR ships.
    impl_ready = False
    return env_opt_in and impl_ready


def sweep_narrative(text: str) -> str:
    """Redact PHI spans in *text* via local-Ollama NER — NOT IMPLEMENTED.

    Raises :class:`PHINERNotImplementedError`. Call :func:`is_enabled`
    first; if it returns False, skip this call and fall back to the
    rule-based DROP action on the narrative field.
    """
    raise PHINERNotImplementedError(
        "scripts.security.phi_ner.sweep_narrative is a design stub. "
        "See the module docstring for the planned implementation. Until "
        "it lands, narrative PHI is handled by drop_fields in "
        "phi_scrub.yaml and by phi_gate_check at the agent boundary."
    )
