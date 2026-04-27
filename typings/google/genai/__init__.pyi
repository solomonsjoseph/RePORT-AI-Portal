# Minimal type stubs for google.genai (google-genai SDK).
# Only the surface used by RePORT AI Portal is typed here.

from typing import Any

from google.genai import types as types

class _Models:
    def generate_content(
        self,
        *,
        model: str,
        contents: list[Any] | Any,
        config: Any | None = ...,
    ) -> Any: ...

class Client:
    models: _Models
    def __init__(self, *, api_key: str = ..., **kwargs: Any) -> None: ...
