"""Version-aware model allowlist for high-risk actions.

Loading or reloading a study mutates ``output/{STUDY}/`` in place. That
pipeline is irreversible without a snapshot restore, so we gate it behind a
model quality bar:

- Anthropic Claude Opus     ≥ 4.6
- Google Gemini Pro         ≥ 3.1
- OpenAI GPT                ≥ 5.3

Any model explicitly in the Ollama provider category passes automatically —
local models are the user's own hardware and are assumed operator-approved.

The allowlist uses *version comparison*, not exact string matching, because
model names change. New minor versions are admitted automatically once they
meet the floor.

Public API
----------
- :func:`is_model_allowed_for_study_load` — single boolean check.
- :func:`describe_allowlist` — human-readable requirements string for the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ModelGateResult",
    "describe_allowlist",
    "is_model_allowed_for_study_load",
]


@dataclass(frozen=True)
class ModelGateResult:
    """Outcome of evaluating a model against the study-load allowlist."""

    allowed: bool
    reason: str


# Family rules: each tuple is (required_substrings, forbidden_substrings, floor).
# A model matches when it contains every required substring and none of the
# forbidden ones. The first matching rule wins.
_FAMILIES: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[int, int]], ...] = (
    # Claude Opus ≥ 4.6
    (("opus",), (), (4, 6)),
    # Gemini Pro ≥ 3.1  (explicitly reject "-flash" / non-pro variants)
    (("gemini", "pro"), ("flash", "nano"), (3, 1)),
    # OpenAI GPT ≥ 5.3  (reject legacy "gpt-4" / "gpt-3" even though they'd parse)
    (("gpt",), (), (5, 3)),
)


_VERSION_RE = re.compile(r"(?<!\d)(\d+)(?:[.\-_](\d+))?")


def _extract_version(model: str) -> tuple[int, int] | None:
    """Pull the first ``major(.minor)?`` pair from a model string."""

    match = _VERSION_RE.search(model)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) else 0
    return major, minor


def _normalise(name: str) -> str:
    return name.strip().lower()


def is_model_allowed_for_study_load(
    *, provider: str, model: str
) -> ModelGateResult:
    """Return whether ``provider``/``model`` may trigger a study load/reload.

    Rules:

    - Ollama (local) is always allowed — the user controls the runtime.
    - Otherwise, the model must match one of the known family rules and meet
      the minimum version (floor comparison is tuple-wise).
    - Unknown models are rejected (fail-closed).
    """

    p = _normalise(provider)
    m = _normalise(model)

    if not m:
        return ModelGateResult(allowed=False, reason="No model selected.")

    if p == "ollama":
        return ModelGateResult(
            allowed=True,
            reason="Local Ollama models are trusted by operator.",
        )

    version = _extract_version(m)

    for required, forbidden, floor in _FAMILIES:
        if not all(token in m for token in required):
            continue
        if any(token in m for token in forbidden):
            continue
        family_label = "/".join(required)
        if version is None:
            return ModelGateResult(
                allowed=False,
                reason=(
                    f"Could not parse version from {model!r}; "
                    f"need {family_label} ≥ {floor[0]}.{floor[1]}."
                ),
            )
        if version >= floor:
            return ModelGateResult(
                allowed=True,
                reason=(
                    f"{model} is at or above the {family_label} "
                    f"{floor[0]}.{floor[1]} floor."
                ),
            )
        return ModelGateResult(
            allowed=False,
            reason=(
                f"{model} is below the {family_label} "
                f"{floor[0]}.{floor[1]} floor."
            ),
        )

    return ModelGateResult(
        allowed=False,
        reason=(
            "Model is not on the study-load allowlist. "
            "Use Claude Opus ≥ 4.6, Gemini Pro ≥ 3.1, GPT ≥ 5.3, "
            "or a local Ollama model."
        ),
    )


def describe_allowlist() -> str:
    """Human-readable summary for UI captions."""

    return (
        "Loading or reloading study data requires a high-capability model: "
        "Claude **Opus ≥ 4.6**, Gemini **Pro ≥ 3.1**, GPT **≥ 5.3**, "
        "or any local **Ollama** model. "
        "\"Use Existing Data\" is always available regardless of model."
    )
