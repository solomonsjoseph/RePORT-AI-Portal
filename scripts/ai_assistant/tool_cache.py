"""Session-scoped tool result cache for the RePORT AI Portal ReAct agent.

Caches tool call results by ``(tool_name, args_hash)`` so that repeated
identical tool calls within a session return instantly without re-reading
files from disk.

The cache is an ordered-dict LRU with a configurable max size.  Clearing
the cache (e.g. on ``:reset``) is a single ``.clear()`` call.

Usage::

    from scripts.ai_assistant.tool_cache import tool_cache

    # In a tool function:
    hit = tool_cache.get("search_variables", query="tuberculosis")
    if hit is not None:
        return hit
    result = _expensive_operation()
    tool_cache.put("search_variables", result, query="tuberculosis")
    return result

    # On session reset:
    tool_cache.clear()
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 256


class ToolCache:
    """LRU cache for tool results, keyed on (tool_name, args_hash)."""

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._store: OrderedDict[str, str] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Key computation
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(tool_name: str, **kwargs: Any) -> str:
        """Build a deterministic cache key from tool name + sorted kwargs."""
        # Sort kwargs for deterministic ordering, serialize to JSON
        args_str = json.dumps(kwargs, sort_keys=True, default=str)
        args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:16]
        return f"{tool_name}:{args_hash}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, tool_name: str, **kwargs: Any) -> str | None:
        """Look up a cached result.  Returns ``None`` on miss."""
        key = self._make_key(tool_name, **kwargs)
        with self._lock:
            value = self._store.get(key)
            if value is not None:
                self._store.move_to_end(key)
                self._hits += 1
                logger.debug("Cache HIT: %s (hits=%d)", key, self._hits)
                return value
            self._misses += 1
        return None

    def put(self, tool_name: str, result: str, **kwargs: Any) -> None:
        """Store a tool result.  Evicts LRU entry if at capacity."""
        key = self._make_key(tool_name, **kwargs)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = result
                return
            if len(self._store) >= self._max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("Cache EVICT: %s", evicted_key)
            self._store[key] = result

    def clear(self) -> None:
        """Clear all cached entries (e.g. on session reset)."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits = 0
            self._misses = 0
        logger.info("Tool cache cleared (%d entries evicted)", count)

    @property
    def stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics."""
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
        }


# Module-level singleton
tool_cache = ToolCache()
