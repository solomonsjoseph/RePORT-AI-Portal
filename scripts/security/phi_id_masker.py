"""HMAC-SHA256 masker for PHI variable-ids in LLM-visible artifacts.

Same key file as ``scripts/security/phi_scrub.py`` (HMAC pseudonymizer).
The mask is deterministic and form-scoped so two forms with the same
variable name produce different tokens. Output is the first 12 hex
characters of the digest — sufficient entropy for issue tracking, far
shorter than a full digest, and unambiguously not a real variable name.
"""

from __future__ import annotations

import hmac
from hashlib import sha256
from pathlib import Path

from scripts.security.phi_scrub import PHIKeyMissingError, PHIScrubError, load_key

_TOKEN_HEX_LEN = 12


class PHIIdMaskerError(PHIScrubError):
    """Raised when masking cannot proceed (missing key, etc.)."""


def mask_variable_id(form: str, variable_id: str, *, key_path: Path | None = None) -> str:
    """Return a 12-hex opaque token for ``(form, variable_id)``.

    The returned token is suitable for embedding in HITL issue bodies, PR
    bodies, and any other LLM-visible artifact. The full mapping is
    recoverable only by parties with the HMAC key.
    """

    try:
        key = load_key(key_path)
    except PHIKeyMissingError as exc:
        raise PHIIdMaskerError(f"PHI HMAC key unavailable: {exc}") from exc
    payload = f"{form}|{variable_id}".encode()
    digest = hmac.new(key, payload, sha256).hexdigest()
    return digest[:_TOKEN_HEX_LEN]
