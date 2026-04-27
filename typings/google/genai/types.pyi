# Minimal type stubs for google.genai.types (google-genai SDK).
# Only the surface used by RePORT AI Portal is typed here.

from typing import Any

class Part:
    text: str | None
    @staticmethod
    def from_bytes(*, data: bytes, mime_type: str) -> Part: ...

class GenerateContentConfig:
    def __init__(
        self,
        *,
        max_output_tokens: int | None = ...,
        temperature: float | None = ...,
        system_instruction: str | None = ...,
        **kwargs: Any,
    ) -> None: ...
