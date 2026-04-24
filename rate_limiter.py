"""Per-provider rate limit tracking."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class RateLimitState:
    """Tracks request counts for a single provider within time windows."""

    provider: str
    rpm_limit: int = 0  # requests per minute, 0 = unlimited
    rpd_limit: int = 0  # requests per day, 0 = unlimited
    _minute_timestamps: list[float] = field(default_factory=list)
    _day_timestamps: list[float] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def _prune(self, now: float) -> None:
        minute_ago = now - 60
        day_ago = now - 86400
        self._minute_timestamps = [t for t in self._minute_timestamps if t > minute_ago]
        self._day_timestamps = [t for t in self._day_timestamps if t > day_ago]

    @property
    def rpm_used(self) -> int:
        with self._lock:
            self._prune(time.time())
            return len(self._minute_timestamps)

    @property
    def rpd_used(self) -> int:
        with self._lock:
            self._prune(time.time())
            return len(self._day_timestamps)

    @property
    def rpm_available(self) -> int:
        if self.rpm_limit <= 0:
            return -1  # unlimited
        return max(0, self.rpm_limit - self.rpm_used)

    @property
    def rpd_available(self) -> int:
        if self.rpd_limit <= 0:
            return -1  # unlimited
        return max(0, self.rpd_limit - self.rpd_used)

    def is_limited(self) -> bool:
        if self.rpm_limit > 0 and self.rpm_used >= self.rpm_limit:
            return True
        if self.rpd_limit > 0 and self.rpd_used >= self.rpd_limit:
            return True
        return False

    def record_request(self) -> None:
        now = time.time()
        with self._lock:
            self._prune(now)
            self._minute_timestamps.append(now)
            self._day_timestamps.append(now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "rpm_limit": self.rpm_limit,
            "rpd_limit": self.rpd_limit,
            "rpm_used": self.rpm_used,
            "rpd_used": self.rpd_used,
            "rpm_available": self.rpm_available,
            "rpd_available": self.rpd_available,
            "limited": self.is_limited(),
        }


class RateLimiter:
    """Manages rate limit state across all providers."""

    def __init__(self) -> None:
        self._states: dict[str, RateLimitState] = {}
        self._lock = Lock()

    def get_or_create(self, provider: str, rpm_limit: int = 0, rpd_limit: int = 0) -> RateLimitState:
        with self._lock:
            if provider not in self._states:
                self._states[provider] = RateLimitState(
                    provider=provider, rpm_limit=rpm_limit, rpd_limit=rpd_limit
                )
            return self._states[provider]

    def is_limited(self, provider: str) -> bool:
        state = self._states.get(provider)
        return state.is_limited() if state else False

    def record_request(self, provider: str) -> None:
        state = self._states.get(provider)
        if state:
            state.record_request()

    def get_all_status(self) -> dict[str, dict[str, Any]]:
        return {name: s.to_dict() for name, s in self._states.items()}

    def get_status(self, provider: str) -> dict[str, Any] | None:
        state = self._states.get(provider)
        return state.to_dict() if state else None


# Global singleton
rate_limiter = RateLimiter()
