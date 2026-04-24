"""In-memory LRU response cache with configurable TTL."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL = int(os.environ.get("CACHE_TTL", "1800"))       # 30 minutes
DEFAULT_MAX_SIZE = int(os.environ.get("CACHE_MAX_SIZE", "1000"))


@dataclass
class CacheEntry:
    key: str
    response: dict[str, Any]
    created_at: float
    expires_at: float
    hits: int = 0


class ResponseCache:
    """Thread-safe LRU cache for non-streaming chat completion responses."""

    def __init__(self, ttl: int = DEFAULT_TTL, max_size: int = DEFAULT_MAX_SIZE) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(model: str, messages: list[dict], temperature: float | None) -> str:
        """Deterministic cache key from request parameters."""
        canonical = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:32]

    def get(self, key: str) -> tuple[dict[str, Any] | None, bool]:
        """Look up a cached response. Returns (response, cache_hit)."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None, False

            now = time.time()
            if now > entry.expires_at:
                del self._cache[key]
                self._misses += 1
                return None, False

            # LRU: move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hits += 1
            self._hits += 1
            return entry.response, True

    def put(self, key: str, response: dict[str, Any]) -> None:
        """Store a response in the cache."""
        now = time.time()
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                # Evict oldest
                self._cache.popitem(last=False)

            self._cache[key] = CacheEntry(
                key=key,
                response=response,
                created_at=now,
                expires_at=now + self._ttl,
            )

    def clear(self) -> int:
        """Clear all cached entries. Returns number of entries cleared."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            }

    def prune_expired(self) -> int:
        """Remove all expired entries. Returns count of pruned entries."""
        now = time.time()
        pruned = 0
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
            for k in expired_keys:
                del self._cache[k]
                pruned += 1
        return pruned


# Global singleton
response_cache = ResponseCache()
