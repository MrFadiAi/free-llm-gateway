"""Model routing with provider fallback logic, retry with backoff, and round-robin."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import AppConfig, ModelFallback
from providers import ProviderConfig, ProviderError, send_to_provider
from rate_limiter import RateLimiter

if TYPE_CHECKING:
    from health import HealthChecker

logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # max fallback providers to try per request
RETRY_MAX_ATTEMPTS = int(os.environ.get("RETRY_MAX_ATTEMPTS", "2"))
RETRY_BACKOFF_BASE = float(os.environ.get("RETRY_BACKOFF_BASE", "1.0"))


@dataclass
class RequestLog:
    timestamp: float
    model: str
    provider: str
    provider_model: str
    success: bool
    error: str | None = None
    latency_ms: float = 0.0
    tokens: dict[str, int] | None = None
    attempt: int = 1


def _extract_usage(result: Any) -> dict[str, int] | None:
    """Extract token usage from a provider response."""
    if not isinstance(result, dict):
        return None
    usage = result.get("usage")
    if not usage or not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
    }


class AllRateLimitedError(RuntimeError):
    """All providers for a model returned 429 rate-limit errors."""


class Router:
    """Routes requests to providers with fallback logic and round-robin."""

    def __init__(
        self,
        config: AppConfig,
        rate_limiter: RateLimiter,
        health_checker: HealthChecker | None = None,
    ) -> None:
        self.config = config
        self.rate_limiter = rate_limiter
        self.health_checker = health_checker
        self._logs: list[RequestLog] = []
        self._max_logs = 100
        self._rr_index: dict[str, int] = {}  # round-robin index per model

    def get_fallbacks(self, model: str) -> list[ModelFallback]:
        """Get the ordered fallback chain for a unified model name."""
        model_cfg = self.config.models.get(model)
        if model_cfg:
            return model_cfg.fallbacks
        return []

    def _get_provider(self, name: str) -> ProviderConfig | None:
        p = self.config.providers.get(name)
        if not p or not p.api_key:
            return None
        return p

    def _select_provider(
        self, model: str, fallbacks: list[ModelFallback]
    ) -> list[tuple[ProviderConfig, str]]:
        """Filter fallbacks to available providers with round-robin ordering.

        Providers are ordered: healthy first (round-robin rotated), then
        down-but-in-cooldown, skipping rate-limited ones.
        """
        candidates: list[tuple[ProviderConfig, str]] = []
        for fb in fallbacks:
            provider = self._get_provider(fb.provider)
            if not provider:
                continue
            state = self.rate_limiter.get_or_create(
                provider.name, provider.rpm_limit, provider.rpd_limit
            )
            if state.is_limited():
                logger.info("Provider %s is rate-limited, skipping", provider.name)
                continue
            # Skip providers marked as down (unless cooldown expired)
            if self.health_checker and not self.health_checker.is_available(provider.name):
                continue
            candidates.append((provider, fb.model))

        if len(candidates) > 1:
            # Apply round-robin: rotate the candidates list
            idx = self._rr_index.get(model, 0) % len(candidates)
            self._rr_index[model] = idx + 1
            candidates = candidates[idx:] + candidates[:idx]

        return candidates

    def _log_request(self, log: RequestLog) -> None:
        self._logs.append(log)
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs:]

    def get_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        logs = self._logs[-limit:]
        return [
            {
                "timestamp": l.timestamp,
                "time_str": time.strftime("%H:%M:%S", time.localtime(l.timestamp)),
                "model": l.model,
                "provider": l.provider,
                "provider_model": l.provider_model,
                "success": l.success,
                "error": l.error,
                "latency_ms": round(l.latency_ms, 1),
                "tokens": l.tokens,
                "attempt": l.attempt,
            }
            for l in reversed(logs)
        ]

    async def route_request(
        self,
        model: str,
        payload: dict[str, Any],
        client: Any,
    ) -> tuple[Any, str, str]:
        """Route a request through the fallback chain with retry and backoff.

        Returns (response, provider_name, provider_model_name).
        Raises if all providers fail.
        """
        fallbacks = self.get_fallbacks(model)
        if not fallbacks:
            raise ValueError(f"Unknown model: {model}")

        candidates = self._select_provider(model, fallbacks)
        if not candidates:
            raise ValueError(
                f"No available providers for model '{model}'. "
                "Check API keys and rate limits."
            )

        errors: list[str] = []
        all_rate_limited = True
        for provider, provider_model in candidates[:MAX_RETRIES]:
            for attempt in range(RETRY_MAX_ATTEMPTS + 1):
                start = time.time()
                try:
                    logger.info(
                        "Routing %s -> %s/%s (attempt %d)",
                        model, provider.name, provider_model, attempt + 1,
                    )
                    self.rate_limiter.record_request(provider.name)
                    result = await send_to_provider(client, provider, provider_model, payload)
                    latency = (time.time() - start) * 1000

                    tokens = _extract_usage(result)
                    self._log_request(RequestLog(
                        timestamp=start,
                        model=model,
                        provider=provider.name,
                        provider_model=provider_model,
                        success=True,
                        latency_ms=latency,
                        tokens=tokens,
                        attempt=attempt + 1,
                    ))
                    return result, provider.name, provider_model

                except ProviderError as e:
                    latency = (time.time() - start) * 1000
                    error_msg = e.message[:200]

                    # Timeout (status 0): move to next provider immediately
                    if e.status == 0:
                        errors.append(f"{provider.name}: timeout")
                        self._log_request(RequestLog(
                            timestamp=start, model=model,
                            provider=provider.name,
                            provider_model=provider_model, success=False,
                            error="timeout", latency_ms=latency,
                            attempt=attempt + 1,
                        ))
                        break

                    # 429 rate limit
                    if e.status == 429:
                        # If Retry-After present, wait and retry same provider
                        if e.retry_after and attempt < RETRY_MAX_ATTEMPTS:
                            logger.info(
                                "Provider %s rate-limited, waiting %.1fs "
                                "(Retry-After), attempt %d/%d",
                                provider.name, e.retry_after,
                                attempt + 1, RETRY_MAX_ATTEMPTS + 1,
                            )
                            self._log_request(RequestLog(
                                timestamp=start, model=model,
                                provider=provider.name,
                                provider_model=provider_model, success=False,
                                error=f"rate limited (retry-after: {e.retry_after:.0f}s)",
                                latency_ms=latency, attempt=attempt + 1,
                            ))
                            await asyncio.sleep(e.retry_after)
                            continue
                        # Rotate key and move to next provider
                        if provider.total_keys > 1:
                            provider.rotate_key()
                            logger.info(
                                "Rotated key for %s to index %d",
                                provider.name, provider.active_key_index,
                            )
                        errors.append(f"{provider.name}: rate limited")
                        self._log_request(RequestLog(
                            timestamp=start, model=model,
                            provider=provider.name,
                            provider_model=provider_model, success=False,
                            error="rate limited", latency_ms=latency,
                            attempt=attempt + 1,
                        ))
                        logger.warning("Provider %s hit rate limit", provider.name)
                        break

                    # 500/502/503: retry with exponential backoff
                    if e.status in (500, 502, 503) and attempt < RETRY_MAX_ATTEMPTS:
                        backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                        logger.info(
                            "Provider %s error %d, retrying in %.1fs "
                            "(attempt %d/%d)",
                            provider.name, e.status, backoff,
                            attempt + 1, RETRY_MAX_ATTEMPTS + 1,
                        )
                        self._log_request(RequestLog(
                            timestamp=start, model=model,
                            provider=provider.name,
                            provider_model=provider_model, success=False,
                            error=f"server error {e.status}",
                            latency_ms=latency, attempt=attempt + 1,
                        ))
                        await asyncio.sleep(backoff)
                        continue

                    # Auth error: rotate key
                    if e.status in (401, 403) and provider.total_keys > 1:
                        provider.rotate_key()
                        logger.info(
                            "Rotated key for %s (auth error) to index %d",
                            provider.name, provider.active_key_index,
                        )

                    # All other errors: log and move to next provider
                    all_rate_limited = False
                    errors.append(f"{provider.name}: {error_msg}")
                    self._log_request(RequestLog(
                        timestamp=start, model=model,
                        provider=provider.name,
                        provider_model=provider_model, success=False,
                        error=error_msg, latency_ms=latency,
                        attempt=attempt + 1,
                    ))
                    logger.warning(
                        "Provider %s failed for %s: %s",
                        provider.name, model, e.message[:100],
                    )
                    break

        error_msg = f"All providers failed for model '{model}': {'; '.join(errors)}"
        if all_rate_limited and errors:
            raise AllRateLimitedError(error_msg)
        raise RuntimeError(error_msg)

    def get_model_status(self) -> list[dict[str, Any]]:
        """Get status overview of all configured models."""
        result = []
        for model_name, model_cfg in self.config.models.items():
            providers_info = []
            for fb in model_cfg.fallbacks:
                provider = self._get_provider(fb.provider)
                has_key = provider is not None
                limited = self.rate_limiter.is_limited(fb.provider) if has_key else False
                # Health status
                health_up = True
                health_status = "unknown"
                if self.health_checker:
                    h = self.health_checker.get_health(fb.provider)
                    health_status = h.status
                    health_up = self.health_checker.is_available(fb.provider)
                providers_info.append({
                    "provider": fb.provider,
                    "model": fb.model,
                    "available": has_key and not limited and health_up,
                    "has_key": has_key,
                    "rate_limited": limited,
                    "health": health_status,
                })
            active = next(
                (p for p in providers_info if p["available"]), None
            )
            result.append({
                "name": model_name,
                "providers": providers_info,
                "active_provider": active,
            })
        return result


def _is_rate_limited_error(err: ProviderError) -> bool:
    return err.status == 429
