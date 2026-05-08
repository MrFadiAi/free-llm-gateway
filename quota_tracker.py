"""Real-time Quota Tracker — tracks provider quotas with reset countdown.

Tracks per-provider quota usage including:
- Requests per minute/day with countdown to reset
- Token budgets with spending limits
- Custom quota alerts
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

QUOTA_FILE = Path(__file__).parent / "data" / "quotas.json"

# Known free tier limits per provider
PROVIDER_QUOTAS: dict[str, dict[str, Any]] = {
    "openrouter": {"rpm": 20, "rpd": 200, "tokens_per_day": 500_000},
    "github": {"rpm": 15, "rpd": 150, "tokens_per_day": 1_000_000},
    "groq": {"rpm": 30, "rpd": 14_400, "tokens_per_day": 5_000_000},
    "cerebras": {"rpm": 30, "rpd": 14_400, "tokens_per_day": 5_000_000},
    "nvidia": {"rpm": 40, "rpd": 5_000, "tokens_per_day": 10_000_000},
    "cloudflare": {"rpm": 50, "rpd": 10_000, "neurons_per_day": 10_000},
    "huggingface": {"rpm": 30, "rpd": 1_000, "tokens_per_day": 1_000_000},
    "google_gemini": {"rpm": 15, "rpd": 1_500, "tokens_per_day": 4_000_000},
    "mistral": {"rpm": 10, "rpd": 500, "tokens_per_day": 500_000},
    "cohere": {"rpm": 10, "rpd": 100, "tokens_per_month": 1_000_000},
    "siliconflow": {"rpm": 20, "rpd": 500, "tokens_per_day": 2_000_000},
    "kilo": {"rpm": 20, "rpd": 1000, "tokens_per_day": 2_000_000},
    "llm7": {"rpm": 10, "rpd": 500, "tokens_per_day": 1_000_000},
}


@dataclass
class QuotaUsage:
    """Tracks usage within a time window."""
    count: int = 0
    tokens: int = 0
    start_time: float = 0.0
    window_seconds: int = 0

    def reset_if_expired(self, now: float) -> None:
        if self.window_seconds > 0 and (now - self.start_time) > self.window_seconds:
            self.count = 0
            self.tokens = 0
            self.start_time = now

    def seconds_until_reset(self, now: float) -> int:
        if self.window_seconds <= 0:
            return -1  # No window
        elapsed = now - self.start_time
        remaining = self.window_seconds - elapsed
        return max(0, int(remaining))


@dataclass
class ProviderQuota:
    """Full quota tracking for a single provider."""
    provider: str
    rpm_limit: int = 0
    rpd_limit: int = 0
    token_budget_daily: int = 0
    token_budget_monthly: int = 0

    # Current usage windows
    minute_usage: QuotaUsage = field(default_factory=lambda: QuotaUsage(window_seconds=60))
    day_usage: QuotaUsage = field(default_factory=lambda: QuotaUsage(window_seconds=86400))
    month_usage: QuotaUsage = field(default_factory=lambda: QuotaUsage(window_seconds=2592000))

    # Custom spending limit (USD)
    spending_limit_daily: float = 0.0  # 0 = no limit
    spending_limit_monthly: float = 0.0
    estimated_spending_today: float = 0.0
    estimated_spending_month: float = 0.0

    def record_request(self, tokens: int = 0) -> None:
        now = time.time()
        self.minute_usage.reset_if_expired(now)
        self.day_usage.reset_if_expired(now)
        self.month_usage.reset_if_expired(now)
        self.minute_usage.count += 1
        self.minute_usage.tokens += tokens
        self.day_usage.count += 1
        self.day_usage.tokens += tokens
        self.month_usage.count += 1
        self.month_usage.tokens += tokens

    def is_within_limits(self) -> bool:
        now = time.time()
        self.minute_usage.reset_if_expired(now)
        self.day_usage.reset_if_expired(now)
        if self.rpm_limit > 0 and self.minute_usage.count >= self.rpm_limit:
            return False
        if self.rpd_limit > 0 and self.day_usage.count >= self.rpd_limit:
            return False
        if self.token_budget_daily > 0 and self.day_usage.tokens >= self.token_budget_daily:
            return False
        if self.token_budget_monthly > 0 and self.month_usage.tokens >= self.token_budget_monthly:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        self.minute_usage.reset_if_expired(now)
        self.day_usage.reset_if_expired(now)
        self.month_usage.reset_if_expired(now)

        return {
            "provider": self.provider,
            "limits": {
                "rpm": self.rpm_limit,
                "rpd": self.rpd_limit,
                "tokens_per_day": self.token_budget_daily,
                "tokens_per_month": self.token_budget_monthly,
                "spending_limit_daily": self.spending_limit_daily,
                "spending_limit_monthly": self.spending_limit_monthly,
            },
            "usage": {
                "minute": {
                    "requests": self.minute_usage.count,
                    "tokens": self.minute_usage.tokens,
                    "limit": self.rpm_limit,
                    "percent_used": round(self.minute_usage.count / self.rpm_limit * 100, 1) if self.rpm_limit > 0 else 0,
                    "seconds_until_reset": self.minute_usage.seconds_until_reset(now),
                },
                "day": {
                    "requests": self.day_usage.count,
                    "tokens": self.day_usage.tokens,
                    "limit": self.rpd_limit,
                    "percent_used": round(self.day_usage.count / self.rpd_limit * 100, 1) if self.rpd_limit > 0 else 0,
                    "seconds_until_reset": self.day_usage.seconds_until_reset(now),
                    "reset_at": _format_timestamp(self.day_usage.start_time + 86400),
                },
                "month": {
                    "requests": self.month_usage.count,
                    "tokens": self.month_usage.tokens,
                    "percent_used": round(
                        self.month_usage.tokens / self.token_budget_monthly * 100, 1
                    ) if self.token_budget_monthly > 0 else 0,
                    "seconds_until_reset": self.month_usage.seconds_until_reset(now),
                },
            },
            "spending": {
                "estimated_today_usd": round(self.estimated_spending_today, 4),
                "estimated_month_usd": round(self.estimated_spending_month, 4),
            },
            "within_limits": self.is_within_limits(),
        }


def _format_timestamp(ts: float) -> str:
    """Format a timestamp as a readable string."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class QuotaTracker:
    """Manages quota tracking across all providers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._quotas: dict[str, ProviderQuota] = {}
        self._load()

    def _load(self) -> None:
        """Initialize quotas from known provider limits."""
        for provider, limits in PROVIDER_QUOTAS.items():
            self._quotas[provider] = ProviderQuota(
                provider=provider,
                rpm_limit=limits.get("rpm", 0),
                rpd_limit=limits.get("rpd", 0),
                token_budget_daily=limits.get("tokens_per_day", 0),
                token_budget_monthly=limits.get("tokens_per_month", 0),
            )

        # Load persisted state
        if QUOTA_FILE.exists():
            try:
                with open(QUOTA_FILE) as f:
                    data = json.load(f)
                for name, qdata in data.get("quotas", {}).items():
                    if name not in self._quotas:
                        self._quotas[name] = ProviderQuota(provider=name)
                    q = self._quotas[name]
                    # Restore custom limits
                    if "rpm_limit" in qdata:
                        q.rpm_limit = qdata["rpm_limit"]
                    if "rpd_limit" in qdata:
                        q.rpd_limit = qdata["rpd_limit"]
                    if "token_budget_daily" in qdata:
                        q.token_budget_daily = qdata["token_budget_daily"]
                    if "spending_limit_daily" in qdata:
                        q.spending_limit_daily = qdata["spending_limit_daily"]
                    if "spending_limit_monthly" in qdata:
                        q.spending_limit_monthly = qdata["spending_limit_monthly"]
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            data: dict[str, Any] = {"quotas": {}}
            for name, q in self._quotas.items():
                data["quotas"][name] = {
                    "rpm_limit": q.rpm_limit,
                    "rpd_limit": q.rpd_limit,
                    "token_budget_daily": q.token_budget_daily,
                    "token_budget_monthly": q.token_budget_monthly,
                    "spending_limit_daily": q.spending_limit_daily,
                    "spending_limit_monthly": q.spending_limit_monthly,
                }
            with open(QUOTA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning("Could not save quota file: %s", e)

    def record_request(self, provider: str, tokens: int = 0) -> None:
        with self._lock:
            if provider not in self._quotas:
                self._quotas[provider] = ProviderQuota(provider=provider)
            self._quotas[provider].record_request(tokens)

    def set_provider_limits(
        self,
        provider: str,
        rpm: int | None = None,
        rpd: int | None = None,
        tokens_per_day: int | None = None,
        spending_daily: float | None = None,
        spending_monthly: float | None = None,
    ) -> ProviderQuota:
        with self._lock:
            if provider not in self._quotas:
                self._quotas[provider] = ProviderQuota(provider=provider)
            q = self._quotas[provider]
            if rpm is not None:
                q.rpm_limit = rpm
            if rpd is not None:
                q.rpd_limit = rpd
            if tokens_per_day is not None:
                q.token_budget_daily = tokens_per_day
            if spending_daily is not None:
                q.spending_limit_daily = spending_daily
            if spending_monthly is not None:
                q.spending_limit_monthly = spending_monthly
            self._save()
            return q

    def get_quota(self, provider: str) -> dict[str, Any] | None:
        q = self._quotas.get(provider)
        return q.to_dict() if q else None

    def get_all_quotas(self) -> dict[str, dict[str, Any]]:
        return {name: q.to_dict() for name, q in self._quotas.items()}

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Compact summary for the dashboard."""
        summary = []
        now = time.time()
        for name, q in sorted(self._quotas.items()):
            q.day_usage.reset_if_expired(now)
            q.minute_usage.reset_if_expired(now)
            summary.append({
                "provider": name,
                "within_limits": q.is_within_limits(),
                "rpm": {
                    "used": q.minute_usage.count,
                    "limit": q.rpm_limit,
                    "reset_in": q.minute_usage.seconds_until_reset(now),
                },
                "rpd": {
                    "used": q.day_usage.count,
                    "limit": q.rpd_limit,
                    "reset_in": q.day_usage.seconds_until_reset(now),
                },
                "tokens_today": q.day_usage.tokens,
                "tokens_limit": q.token_budget_daily,
            })
        return {"providers": summary}


# Global singleton
quota_tracker = QuotaTracker()
