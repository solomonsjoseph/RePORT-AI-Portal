# Minimal type stubs for the anthropic SDK.
# Only the surface used by RePORT AI Portal is typed here.

from typing import Any

class _Messages:
    def create(self, **kwargs: Any) -> Any: ...

class Anthropic:
    messages: _Messages
    def __init__(self, *, api_key: str = ..., **kwargs: Any) -> None: ...
