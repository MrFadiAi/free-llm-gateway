"""Model routing with provider fallback logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from config import AppConfig, ModelFallback
from providers import ProviderConfig, ProviderError, send_to_provider
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # max fallback attempts per request


@dataclass
class RequestLog:
    timestamp: float
    model: str
    provider: str
    provider_model: str
    success: bool
    error: str | None = None
    latency_ms: float = 0.0


class Router:
    """Routes requests to providers with fallback logic."""

    def __init__(self, config: AppConfig, rate_limiter: RateLimiter) -> None:
        self.config = config
        self.rate_limiter = rate_limiter
        self._logs: list[RequestLog] = []
        self._max_logs = 100

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

    def _select_provider(self, fallbacks: list[ModelFallback]) -> list[tuple[ProviderConfig, str]]:
        """Filter fallbacks to available providers (have keys, not rate-limited)."""
        candidates: list[tuple[ProviderConfig, str]] = []
        for fb in fallbacks:
            provider = self._get_provider(fb.provider)
            if not provider:
                continue
            state = self.rate_limiter.get_or_create(provider.name, provider.rpm_limit, provider.rpd_limit)
            if state.is_limited():
                logger.info("Provider %s is rate-limited, skipping", provider.name)
                continue
            candidates.append((provider, fb.model))
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
            }
            for l in reversed(logs)
        ]

    async def route_request(
        self,
        model: str,
        payload: dict[str, Any],
        client: Any,
    ) -> tuple[Any, str, str]:
        """Route a request through the fallback chain.

        Returns (response, provider_name, provider_model_name).
        Raises if all providers fail.
        """
        import httpx

        fallbacks = self.get_fallbacks(model)
        if not fallbacks:
            raise ValueError(f"Unknown model: {model}")

        candidates = self._select_provider(fallbacks)
        if not candidates:
            raise ValueError(
                f"No available providers for model '{model}'. "
                "Check API keys and rate limits."
            )

        errors: list[str] = []
        for provider, provider_model in candidates[:MAX_RETRIES]:
            start = time.time()
            try:
                logger.info(
                    "Routing %s -> %s/%s", model, provider.name, provider_model
                )
                self.rate_limiter.record_request(provider.name)
                result = await send_to_provider(client, provider, provider_model, payload)
                latency = (time.time() - start) * 1000

                self._log_request(RequestLog(
                    timestamp=start,
                    model=model,
                    provider=provider.name,
                    provider_model=provider_model,
                    success=True,
                    latency_ms=latency,
                ))
                return result, provider.name, provider_model

            except ProviderError as e:
                latency = (time.time() - start) * 1000
                errors.append(f"{provider.name}: {e.message[:200]}")
                self._log_request(RequestLog(
                    timestamp=start,
                    model=model,
                    provider=provider.name,
                    provider_model=provider_model,
                    success=False,
                    error=e.message[:200],
                    latency_ms=latency,
                ))
                if _is_rate_limited_error(e):
                    state = self.rate_limiter.get_or_create(provider.name)
                    # Force limited state until window resets
                    logger.warning("Provider %s hit rate limit", provider.name)
                logger.warning(
                    "Provider %s failed for %s: %s", provider.name, model, e.message[:100]
                )
                continue

        raise RuntimeError(
            f"All providers failed for model '{model}': {'; '.join(errors)}"
        )

    def get_model_status(self) -> list[dict[str, Any]]:
        """Get status overview of all configured models."""
        result = []
        for model_name, model_cfg in self.config.models.items():
            providers_info = []
            for fb in model_cfg.fallbacks:
                provider = self._get_provider(fb.provider)
                has_key = provider is not None
                limited = self.rate_limiter.is_limited(fb.provider) if has_key else False
                providers_info.append({
                    "provider": fb.provider,
                    "model": fb.model,
                    "available": has_key and not limited,
                    "has_key": has_key,
                    "rate_limited": limited,
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
