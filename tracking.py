"""Token usage tracking — per provider, model, and day. Persists to JSON."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
USAGE_FILE = DATA_DIR / "usage.json"

# Approximate OpenAI pricing per 1M tokens (USD) — used for "estimated savings"
OPENAI_PRICING: dict[str, dict[str, float]] = {
    # model family -> {"prompt": price_per_1M, "completion": price_per_1M}
    "default": {"prompt": 0.50, "completion": 1.50},
    "gpt-4": {"prompt": 30.0, "completion": 60.0},
    "gpt-3.5": {"prompt": 0.50, "completion": 1.50},
    "claude": {"prompt": 3.0, "completion": 15.0},
}


def _pricing_for_model(model: str) -> dict[str, float]:
    lower = model.lower()
    for key, pricing in OPENAI_PRICING.items():
        if key == "default":
            continue
        if key in lower:
            return pricing
    return OPENAI_PRICING["default"]


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _week_keys() -> list[str]:
    """Return date keys for the last 7 days (including today)."""
    keys = []
    now = time.time()
    for i in range(7):
        t = now - i * 86400
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        keys.append(dt.strftime("%Y-%m-%d"))
    return keys


class UsageTracker:
    """Thread-safe token usage tracker with JSON persistence."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._data: dict[str, Any] = self._load()
        self._dirty = False
        self._last_save = time.time()

    def _load(self) -> dict[str, Any]:
        if USAGE_FILE.exists():
            try:
                with open(USAGE_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Could not load usage file, starting fresh")
        return {"daily": {}, "total": {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(USAGE_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
            self._dirty = False
            self._last_save = time.time()
        except OSError as e:
            logger.warning("Could not save usage file: %s", e)

    def _maybe_save(self) -> None:
        if self._dirty and (time.time() - self._last_save) > 5:
            self._save()

    def record(
        self,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        day = _today_key()

        with self._lock:
            # Daily record
            daily = self._data.setdefault("daily", {})
            day_data = daily.setdefault(day, {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "by_provider": {},
                "by_model": {},
            })
            day_data["requests"] += 1
            day_data["prompt_tokens"] += prompt_tokens
            day_data["completion_tokens"] += completion_tokens
            day_data["total_tokens"] += total_tokens

            # Per-provider daily
            prov = day_data["by_provider"].setdefault(provider, {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })
            prov["requests"] += 1
            prov["prompt_tokens"] += prompt_tokens
            prov["completion_tokens"] += completion_tokens
            prov["total_tokens"] += total_tokens

            # Per-model daily
            mdl = day_data["by_model"].setdefault(model, {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })
            mdl["requests"] += 1
            mdl["prompt_tokens"] += prompt_tokens
            mdl["completion_tokens"] += completion_tokens
            mdl["total_tokens"] += total_tokens

            # All-time totals
            total = self._data.setdefault("total", {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })
            total["requests"] += 1
            total["prompt_tokens"] += prompt_tokens
            total["completion_tokens"] += completion_tokens
            total["total_tokens"] += total_tokens

            self._dirty = True
            self._maybe_save()

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            daily = self._data.get("daily", {})
            total = self._data.get("total", {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })

            today_key = _today_key()
            today = daily.get(today_key, {
                "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "by_provider": {}, "by_model": {},
            })

            week_keys = _week_keys()
            week = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for wk in week_keys:
                day_data = daily.get(wk)
                if day_data:
                    week["requests"] += day_data.get("requests", 0)
                    week["prompt_tokens"] += day_data.get("prompt_tokens", 0)
                    week["completion_tokens"] += day_data.get("completion_tokens", 0)
                    week["total_tokens"] += day_data.get("total_tokens", 0)

            # Calculate estimated savings
            def _savings(tokens_data: dict[str, int]) -> float:
                pricing = _pricing_for_model("default")
                prompt = tokens_data.get("prompt_tokens", 0)
                completion = tokens_data.get("completion_tokens", 0)
                return (prompt * pricing["prompt"] + completion * pricing["completion"]) / 1_000_000

            return {
                "today": {**today},
                "week": week,
                "all_time": {**total},
                "estimated_savings": {
                    "today_usd": round(_savings(today), 4),
                    "week_usd": round(_savings(week), 4),
                    "all_time_usd": round(_savings(total), 4),
                },
            }

    def get_dashboard_stats(self) -> dict[str, Any]:
        stats = self.get_stats()
        today = stats["today"]
        return {
            "today_requests": today.get("requests", 0),
            "today_tokens": today.get("total_tokens", 0),
            "today_prompt_tokens": today.get("prompt_tokens", 0),
            "today_completion_tokens": today.get("completion_tokens", 0),
            "all_time_requests": stats["all_time"].get("requests", 0),
            "all_time_tokens": stats["all_time"].get("total_tokens", 0),
            "savings_today": stats["estimated_savings"]["today_usd"],
            "savings_all_time": stats["estimated_savings"]["all_time_usd"],
            "by_provider": today.get("by_provider", {}),
            "by_model": today.get("by_model", {}),
        }


# Global singleton
usage_tracker = UsageTracker()
